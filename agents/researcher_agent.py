import os
import re
import sys
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VAULT_ROOT, MAP_PATH, RESEARCHER_MODEL, DENSITY_SAMPLE_BYTES
from llm.ollama_client import call_json
from llm.prompts import (
    SEED_PACKET_PROMPT, FRONTIER_EXPANSION_PROMPT, GAP_ANALYSIS_PROMPT
)
from organizer import analyze_technical_density

log = logging.getLogger(__name__)


class ResearcherAgent:
    def __init__(self, topic: str):
        self.topic = topic
        self.lexicon: list = []
        self.frontier: list = []        # [(node, score)]
        self.discovered: set = set()
        self._map_cache: dict | None = None

    # -------------------------------------------------------------------------
    # Bootstrap

    def bootstrap(self) -> tuple[list, list]:
        map_data = self._load_map()
        topic_data = map_data.get(self.topic, {})

        existing_lexicon = topic_data.get("lexicon", [])
        if existing_lexicon and len(existing_lexicon) >= 12 and _lexicon_is_domain_specific(existing_lexicon, self.topic):
            self.lexicon = existing_lexicon
            log.info(f"[*] Reusing stored lexicon ({len(self.lexicon)} terms)")
        else:
            prompt = SEED_PACKET_PROMPT.format(topic=self.topic.replace('_', ' '))
            result = call_json(RESEARCHER_MODEL, prompt, temperature=0.2)
            if isinstance(result, dict) and "nodes" in result and "lexicon" in result and not _is_template_echo(result):
                self.lexicon = result.get("lexicon", [])
                nodes = result.get("nodes", [])
            else:
                log.warning(f"[!] Seed packet returned {type(result).__name__} or template echo — using fallback")
                self.lexicon = _fallback_lexicon(self.topic)
                nodes = _fallback_nodes(self.topic)

            # Reload map fresh before saving — avoids overwriting concurrent changes
            # made while the LLM call was in flight (e.g. a manual map clear)
            map_data = self._load_map()
            if self.topic not in map_data:
                map_data[self.topic] = {}
            map_data[self.topic]["lexicon"] = self.lexicon
            map_data[self.topic]["status"] = "active"
            if "nodes" not in map_data[self.topic]:
                map_data[self.topic]["nodes"] = {}
            for n in nodes:
                if not isinstance(n, str):
                    continue
                words = n.strip().split()
                if len(words) > 6:
                    n = " ".join(words[:6])
                if not n:
                    continue
                if n not in map_data[self.topic]["nodes"]:
                    map_data[self.topic]["nodes"][n] = {"status": "pending", "files": []}
            self._save_map(map_data)

        map_data = self._load_map()
        nodes_data = map_data.get(self.topic, {}).get("nodes", {})
        self.frontier = []
        for node, info in nodes_data.items():
            if info.get("status") in ("pending", "stalled"):
                score = map_data[self.topic].get("frontier_scores", {}).get(node, 1)
                self.frontier.append((node, score))
            self.discovered.add(node)

        self.frontier.sort(key=lambda x: x[1], reverse=True)

        nodes_out = [n for n, _ in self.frontier]
        return nodes_out, self.lexicon

    # -------------------------------------------------------------------------
    # Frontier expansion

    def expand_from_document(self, text: str, current_node: str):
        existing = [n for n, _ in self.frontier] + list(self.discovered)
        prompt = FRONTIER_EXPANSION_PROMPT.format(
            topic=self.topic.replace('_', ' '),
            current_node=current_node,
            text_sample=text[:3000],
            existing_nodes="\n".join(f"- {n}" for n in existing[:30])
        )
        result = call_json(RESEARCHER_MODEL, prompt, temperature=0.3)
        if not isinstance(result, list):
            return

        map_data = self._load_map()
        topic_data = map_data.setdefault(self.topic, {})
        nodes_data = topic_data.setdefault("nodes", {})
        scores = topic_data.setdefault("frontier_scores", {})

        from drift_monitor import is_node_on_topic
        for new_node in result:
            if not isinstance(new_node, str) or new_node in self.discovered:
                continue
            words = new_node.strip().split()
            if len(words) > 6:
                new_node = " ".join(words[:6])
            if not new_node or new_node in self.discovered:
                continue
            ok, reason = is_node_on_topic(self.topic, new_node, self.lexicon)
            if not ok:
                log.info(f"[DRIFT] reject node '{new_node[:60]}' — {reason}")
                continue
            scores[new_node] = scores.get(new_node, 0) + 1
            if new_node not in nodes_data:
                nodes_data[new_node] = {
                    "status": "pending", "files": [],
                    "discovery": "frontier_expansion", "discovered_from": current_node
                }
                self.frontier.append((new_node, scores[new_node]))

        self._save_map(map_data)
        self.frontier.sort(key=lambda x: x[1], reverse=True)

    def lexicon_sweep(self) -> list:
        """Push domain-specific lexicon terms onto the frontier as technique nodes."""
        map_data = self._load_map()
        topic_data = map_data.setdefault(self.topic, {})
        nodes_data = topic_data.setdefault("nodes", {})
        scores = topic_data.setdefault("frontier_scores", {})

        new_nodes = []
        for term in self.lexicon:
            if len(term) < 4 or term.lower() in _GENERIC_TERMS:
                continue
            node = term if term in nodes_data else term
            if node in self.discovered:
                continue
            if node not in nodes_data:
                nodes_data[node] = {
                    "status": "pending", "files": [],
                    "discovery": "lexicon_sweep"
                }
            scores[node] = scores.get(node, 0) + 1
            self.frontier.append((node, 1))
            self.discovered.add(node)
            new_nodes.append(node)

        if new_nodes:
            self._save_map(map_data)
            self.frontier.sort(key=lambda x: x[1], reverse=True)
        return new_nodes

    def generate_deep_dive_nodes(self, min_corpus: int = 5) -> list:
        """Generate a tier-3 (theoretical / advanced) research frontier
        ONLY when the foundational corpus is already populated. Each new
        node is tagged tier='theoretical' so the coordinator routes it to
        academic/technical agents preferentially.

        Returns the list of new node strings added.
        """
        from llm.prompts import DEEP_DIVE_FRONTIER_PROMPT

        map_data = self._load_map()
        topic_data = map_data.setdefault(self.topic, {})
        nodes_data = topic_data.setdefault("nodes", {})
        grounded = [n for n, info in nodes_data.items()
                    if info.get("status") == "grounded"]
        if len(grounded) < min_corpus:
            log.info(f"[deep-dive] corpus too thin ({len(grounded)} < "
                     f"{min_corpus}) — skipping")
            return []

        prompt = DEEP_DIVE_FRONTIER_PROMPT.format(
            topic=self.topic.replace('_', ' '),
            covered_nodes="\n".join(f"- {n}" for n in grounded[:25]),
            lexicon=", ".join(self.lexicon[:20]),
        )
        result = call_json(RESEARCHER_MODEL, prompt, temperature=0.4,
                           timeout=120, num_ctx=4096, num_predict=512)
        if not isinstance(result, list):
            log.warning(f"[deep-dive] LLM returned {type(result).__name__}")
            return []

        scores = topic_data.setdefault("frontier_scores", {})
        from drift_monitor import is_node_on_topic
        added = []
        for node in result:
            if not isinstance(node, str):
                continue
            words = node.strip().split()
            if len(words) > 6:
                node = " ".join(words[:6])
            if not node or node in self.discovered:
                continue
            ok, reason = is_node_on_topic(self.topic, node, self.lexicon)
            if not ok:
                log.info(f"[DRIFT] reject deep-dive node '{node[:60]}' — {reason}")
                continue
            scores[node] = scores.get(node, 0) + 2  # higher priority
            nodes_data[node] = {
                "status": "pending",
                "files": [],
                "discovery": "deep_dive",
                "tier": "theoretical",
            }
            self.frontier.append((node, scores[node]))
            added.append(node)

        if added:
            self.frontier.sort(key=lambda x: -x[1])
            self._save_map(map_data)
            log.info(f"[deep-dive] +{len(added)} theoretical nodes: "
                     f"{', '.join(n[:40] for n in added[:3])}...")
        return added

    def identify_gaps(self):
        map_data = self._load_map()
        nodes_data = map_data.get(self.topic, {}).get("nodes", {})
        covered = [n for n, info in nodes_data.items() if info.get("status") == "grounded"]
        if len(covered) < 2:
            return

        prompt = GAP_ANALYSIS_PROMPT.format(
            topic=self.topic.replace('_', ' '),
            covered_nodes="\n".join(f"- {n}" for n in covered),
            lexicon=", ".join(self.lexicon[:20])
        )
        result = call_json(RESEARCHER_MODEL, prompt, temperature=0.4)
        if not isinstance(result, list):
            return

        topic_data = map_data.setdefault(self.topic, {})
        nodes_data = topic_data.setdefault("nodes", {})

        from drift_monitor import is_node_on_topic
        for gap in result:
            if not isinstance(gap, str) or gap in self.discovered:
                continue
            ok, reason = is_node_on_topic(self.topic, gap, self.lexicon)
            if not ok:
                log.info(f"[DRIFT] reject gap node '{gap[:60]}' — {reason}")
                continue
            nodes_data[gap] = {"status": "pending", "files": [], "discovery": "gap_analysis"}
            self.frontier.append((gap, 2))

        self._save_map(map_data)

    # -------------------------------------------------------------------------
    # Status updates

    def mark_grounded(self, node: str, filename: str):
        map_data = self._load_map()
        nodes = map_data.setdefault(self.topic, {}).setdefault("nodes", {})
        if node not in nodes:
            nodes[node] = {"status": "pending", "files": []}
        nodes[node]["status"] = "grounded"
        if filename not in nodes[node].get("files", []):
            nodes[node].setdefault("files", []).append(filename)
        self.discovered.add(node)
        self._save_map(map_data)

    def mark_stalled(self, node: str):
        map_data = self._load_map()
        nodes = map_data.setdefault(self.topic, {}).setdefault("nodes", {})
        if node not in nodes:
            nodes[node] = {"status": "stalled", "files": []}
        elif nodes[node].get("status") != "grounded":
            nodes[node]["status"] = "stalled"
        self.discovered.add(node)
        # Remove from in-memory frontier so it won't be retried this session
        self.frontier = [(n, s) for n, s in self.frontier if n != node]
        self._save_map(map_data)

    # -------------------------------------------------------------------------
    # Grit synthesis

    def synthesize_grit(self, max_files: int = None) -> list:

        topic_path = os.path.join(VAULT_ROOT, self.topic)
        if not os.path.exists(topic_path):
            return []

        files = [f for f in os.listdir(topic_path) if f.endswith(".txt")]
        if not files:
            return []

        files.sort(key=lambda f: os.path.getsize(os.path.join(topic_path, f)), reverse=True)
        files = files[:80 if max_files is None else max_files]

        all_grit = []
        seen_tasks = set()

        for fname in files:
            fpath = os.path.join(topic_path, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except Exception:
                continue

            score, _ = analyze_technical_density(text[:DENSITY_SAMPLE_BYTES], self.lexicon)
            if score < 3:
                continue
            log.info(f"  [SYNTH] {fname} score={score}")

            for chunk in _chunk_text(text, 300):
                for item in _keyword_grit(chunk, self.topic, self.lexicon):
                    task = item.get("task", "")
                    if task and task not in seen_tasks:
                        seen_tasks.add(task)
                        all_grit.append(item)

        return all_grit

    # -------------------------------------------------------------------------
    # Knowledge map helpers

    def _load_map(self) -> dict:
        if os.path.exists(MAP_PATH):
            try:
                with open(MAP_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_map(self, data: dict):
        with open(MAP_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self._map_cache = data

    def _cached_map(self) -> dict:
        if not hasattr(self, '_map_cache') or self._map_cache is None:
            self._map_cache = self._load_map()
        return self._map_cache


# -------------------------------------------------------------------------
# Helpers

def _chunk_text(text: str, chunk_size: int = 2000) -> list:
    words = text.split()
    return [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]


_GENERIC_TERMS = {
    "procedure", "safety", "tool", "material", "technique", "step", "process",
    "method", "manual", "instruction", "standard", "specification", "equipment",
    "operation", "technical", "system", "general", "basic", "advanced"
}

def _lexicon_is_domain_specific(lexicon: list, topic: str) -> bool:
    generic_count = sum(1 for t in lexicon if t.lower() in _GENERIC_TERMS or t.lower() == topic.lower())
    return generic_count < len(lexicon) / 2

def _fallback_lexicon(topic: str) -> list:
    return [topic, "procedure", "safety", "tool", "material", "technique",
            "step", "process", "method", "manual", "instruction", "standard",
            "specification", "equipment", "operation"]


_ECHO_PATTERNS = frozenset({"sub-topic", "term1", "term2", "term3", "term4", "term5"})

def _is_template_echo(packet: dict) -> bool:
    nodes = [str(n).lower() for n in packet.get("nodes", [])]
    lexicon = [str(t).lower() for t in packet.get("lexicon", [])]
    return any(pat in item for item in nodes + lexicon for pat in _ECHO_PATTERNS)


_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')
_ACTION_PAT = re.compile(
    r'\b(cut|trim|stitch|sew|punch|skive|bevel|burnish|dye|wet|mold|shape|wrap|fold|glue|cement|rivet|lace|carve|stamp|tool|finish|sand|buff|apply|attach|secure|mark|trace|measure|fit|assemble|install|heat|forge|hammer|quench|anneal|temper|grind|weld|harden|sharpen|polish|coat|seal|treat|prepare|form|press|clamp|crimp)\b',
    re.I
)
_MEASURE_PAT = re.compile(
    r'\b\d+[\.\d]*\s*(?:inch(?:es)?|mm|cm|lb|kg|oz|psi|rpm|amp|volt|degrees?|°|min(?:utes?)?|sec(?:onds?)?|ply|gauge)\b'
    r'|\b\d{3,4}\s*[FfCc]\b'
    r'|\b(?:cherry[\s-]?red|bright[\s-]?red|orange[\s-]?heat|yellow[\s-]?heat|white[\s-]?hot|dark[\s-]?red|black[\s-]?heat)\b'
    r'|\b\d+\s*(?:ounce|oz)\s*(?:leather|hide|veg[\s-]?tan)\b',
    re.I
)


def _keyword_grit(chunk: str, topic: str, lexicon: list) -> list:
    items = []
    sentences = _SENT_SPLIT.split(chunk)
    lex_lower = {t.lower() for t in lexicon}
    for sent in sentences:
        sl = sent.lower().strip()
        if len(sl) < 20:
            continue
        lex_hits = [t for t in lex_lower if t in sl]
        action_hits = _ACTION_PAT.findall(sent)
        has_measure = bool(_MEASURE_PAT.search(sent))
        # Include if: (lexicon term + action verb) OR (action verb + measurement)
        # OR (lexicon term + measurement)  — pure-spec sentences like
        # "Annealing range: 350–375°F" carry real technical content even
        # without action verbs and must not be dropped.
        if (lex_hits and action_hits) or (action_hits and has_measure) or (lex_hits and has_measure):
            tools = list({h.lower() for h in action_hits[:3]} | set(lex_hits[:2]))
            items.append({
                "task": sent.strip()[:150],
                "variables": {},
                "tools": tools[:5],
                "safety": [],
                "source": "keyword"
            })
    return items


def _fallback_nodes(topic: str) -> list:
    t = topic.replace('_', ' ')
    return [
        f"{t} fundamentals and theory",
        f"{t} tools and equipment",
        f"{t} safety procedures",
        f"{t} basic techniques",
        f"{t} intermediate methods",
        f"{t} project planning",
        f"{t} material selection",
        f"{t} finishing and quality",
    ]
