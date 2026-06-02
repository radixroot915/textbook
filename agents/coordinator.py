import os
import sys
import json
import logging
import asyncio
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VAULT_ROOT, AGENT_CONCURRENCY, MAP_PATH
from agents.researcher_agent import ResearcherAgent
from agents.gutenberg_agent import GutenbergAgent
from agents.openlibrary_agent import OpenLibraryAgent
from agents.wikisource_agent import WikiSourceAgent
from agents.wikibooks_agent import WikibooksAgent
from agents.archive_agent import ArchiveAgent
from agents.stackexchange_agent import StackExchangeAgent
from agents.wikipedia_agent import WikipediaAgent
from agents.youtube_agent import YouTubeTranscriptAgent
from agents.duckduckgo_agent import DuckDuckGoAgent
from agents.hathitrust_agent import HathiTrustAgent
from agents.dtic_agent import DTICAgent
from agents.libretexts_agent import LibreTextsAgent
from agents.core_agent import COREAgent
from agents.skillscommons_agent import SkillsCommonsAgent
from agents.everyspec_agent import EverySpecAgent
from agents.osha_agent import OSHATechManualAgent
from agents.reddit_agent import RedditAgent
from agents.cited_url_agent import CitedURLAgent
from agents.hub_agent import HubAgent
from agents.base_source_agent import _TITLE_BLOCKLIST, set_topic_bouncer
from curriculum.builder import build_curriculum
from curriculum.video_finder import build_video_guide
from curriculum.materials import build_materials_list

log = logging.getLogger(__name__)

WORKER_TIMEOUT = 600

HIGH_VALUE_SEEN = 2     # appearances across node searches before flagging
HIGH_VALUE_SAVE = 1     # must have saved at least once (quality gate)
HIGH_VALUE_LIMIT = 8    # candidate pull depth for flagged sources (vs normal 3)
DEFAULT_PULL_LIMIT = 5  # baseline candidates per node (was 3)


class Coordinator:
    def __init__(self, topic: str, min_files: int = 100, max_iterations: int = 5, skip_compile: bool = False, hub_urls: list[str] | None = None):
        self.topic = topic
        self.min_files = min_files
        self.max_iterations = max_iterations
        self.skip_compile = skip_compile
        self.researcher = ResearcherAgent(topic)
        self.sources = [
            WikipediaAgent(),
            GutenbergAgent(),
            OpenLibraryAgent(),
            WikiSourceAgent(),
            WikibooksAgent(),
            ArchiveAgent(),
            StackExchangeAgent(),
            YouTubeTranscriptAgent(),
            DuckDuckGoAgent(),
            HathiTrustAgent(),
            DTICAgent(),
            LibreTextsAgent(),
            COREAgent(),
            SkillsCommonsAgent(),
            EverySpecAgent(),
            OSHATechManualAgent(),
            RedditAgent(),
            CitedURLAgent(),
        ]
        for url in (hub_urls or []):
            self.sources.append(HubAgent(url))

        # Reorder sources by historical quality for this topic. Agents with
        # high used/saved ratios get tried first; new agents get neutral
        # priority so they still get a fair shot.
        try:
            from agent_stats import get_topic_priorities
            prios = get_topic_priorities(topic)
            if prios:
                self.sources.sort(key=lambda s: -prios.get(s.source_name, 1.0))
                top = ", ".join(f"{s.source_name}={prios.get(s.source_name, 1.0):.2f}"
                                for s in self.sources[:5])
                log.info(f"[*] Source priority order — top 5: {top}")
        except Exception as e:
            log.debug(f"[*] priority reorder skipped: {e}")

        self._skippable_ids: set = set()
        self._source_stats: dict = {}   # iid -> {"seen": int, "saved": int}
        self._high_value: set = set()
        self._stats_lock = threading.Lock()
        self._load_high_value()

    async def run(self) -> list:
        """Run one full harvest+compile cycle. Returns gap_nodes for the next cycle."""
        self.gap_nodes: list = []
        log.info(f"\n=== HARVESTER: {self.topic.upper()} ===")

        # Scope dedup to this topic so vaults don't block each other
        set_topic_bouncer(self.topic)
        log.info(f"[*] Dedup scoped to: fingerprints_{self.topic}.txt")

        log.info("[*] Bootstrapping research frontier...")
        nodes, lexicon = self.researcher.bootstrap()
        log.info(f"[*] Nodes: {len(nodes)} | Lexicon: {len(lexicon)} terms")
        if lexicon:
            log.info(f"[*] Lexicon sample: {', '.join(lexicon[:6])}...")
        if self._high_value:
            log.info(f"[*] High-value sources loaded: {len(self._high_value)}")

        iteration = 0
        total_files = self._count_files()

        while iteration < self.max_iterations and total_files < self.min_files:
            log.info(f"\n--- [ITERATION {iteration + 1}/{self.max_iterations}] ---")

            # Pull a batch and drift-gate every node before processing —
            # catches stale bad nodes from prior runs that survived in the
            # knowledge_map (e.g. "Administrative districts" from a polluted
            # gap analysis in a previous cycle).
            try:
                from drift_monitor import is_node_on_topic
            except Exception:
                is_node_on_topic = None

            def _filter_batch(candidates):
                if not is_node_on_topic:
                    return candidates
                out = []
                for n in candidates:
                    ok, reason = is_node_on_topic(self.topic, n, lexicon)
                    if not ok:
                        log.info(f"[DRIFT] skip stale node '{n[:60]}' — {reason}")
                        # Mark in map so it doesn't keep getting tried
                        try:
                            self.researcher.mark_stalled(n)
                        except Exception:
                            pass
                        continue
                    out.append(n)
                return out

            batch = _filter_batch([n for n, _ in self.researcher.frontier[:12]])[:6]
            if not batch:
                log.info("[*] Frontier empty (or all filtered) — running gap analysis...")
                self.researcher.identify_gaps()
                batch = _filter_batch([n for n, _ in self.researcher.frontier[:12]])[:6]
            if not batch:
                log.info("[*] Running lexicon sweep for crossover technique content...")
                new = self.researcher.lexicon_sweep()
                log.info(f"[*] Lexicon sweep added {len(new)} technique nodes")
                batch = _filter_batch([n for n, _ in self.researcher.frontier[:12]])[:6]
            if not batch:
                log.info("[*] No new nodes found. Stopping.")
                break

            # Look up node tier from the knowledge_map; agents with a
            # tier_affinity that excludes this node's tier are skipped.
            # Nodes without an explicit tier go to all agents (default).
            map_data = self.researcher._load_map()
            nodes_info = map_data.get(self.topic, {}).get("nodes", {})

            def _agents_for_node(node: str):
                tier = nodes_info.get(node, {}).get("tier")
                if not tier:
                    return self.sources
                matching = [
                    s for s in self.sources
                    if not getattr(s, "tier_affinity", None)
                    or tier in s.tier_affinity
                ]
                # Fallback to all sources if nothing matches the tier —
                # better to over-search than to drop the node entirely
                return matching or self.sources

            work_queue = asyncio.Queue()
            for node in batch:
                agents = _agents_for_node(node)
                for source in agents:
                    await work_queue.put((node, source))

            workers = set(
                asyncio.create_task(self._worker(work_queue, lexicon))
                for _ in range(AGENT_CONCURRENCY)
            )
            queue_done = asyncio.Event()

            async def _watchdog():
                while not queue_done.is_set():
                    await asyncio.sleep(15)
                    dead = {w for w in workers if w.done()}
                    for w in dead:
                        exc = w.exception() if not w.cancelled() else None
                        if exc:
                            log.warning(f"  [!] Worker died ({exc!r}) — restarting")
                        workers.discard(w)
                        if not queue_done.is_set():
                            workers.add(asyncio.create_task(self._worker(work_queue, lexicon)))

            watchdog = asyncio.create_task(_watchdog())
            await work_queue.join()
            queue_done.set()
            watchdog.cancel()
            for w in workers:
                w.cancel()

            self.researcher.identify_gaps()

            total_files = self._count_files()
            log.info(f"\n--- [ITERATION {iteration + 1} COMPLETE] Files: {total_files}/{self.min_files} | Frontier depth: {len(self.researcher.frontier)} ---")

            iteration += 1

        log.info(f"\n=== SYNTHESIS AND CURRICULUM BUILD ===")
        log.info("[*] Extracting grit from vault...")
        grit = self.researcher.synthesize_grit()
        log.info(f"[*] Extracted {len(grit)} grit items")

        self._save_grit(grit)

        if self.skip_compile:
            log.info("[*] --skip-compile set — skipping curriculum build")
        elif total_files > 0:
            log.info("[*] Building curriculum...")
            result = build_curriculum(self.topic, self.researcher.lexicon, grit)
            self.gap_nodes = result.get("gap_nodes", [])
            log.info("[*] Building video guide...")
            build_video_guide(self.topic, grit)
            log.info("[*] Building materials list...")
            build_materials_list(self.topic, grit)
        else:
            log.warning("[!] No files collected — curriculum generation skipped")

        log.info(f"\n=== COMPLETE ===")
        log.info(f"Files harvested: {total_files}")
        log.info(f"Grit items extracted: {len(grit)}")
        log.info(f"Gap nodes for next cycle: {len(self.gap_nodes)}")
        log.info(f"Output: {os.path.join(VAULT_ROOT, self.topic, 'curriculum')}")
        return self.gap_nodes

    async def _worker(self, queue: asyncio.Queue, lexicon: list):
        while True:
            try:
                node, source = await queue.get()
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self._process_node_source, node, source, lexicon),
                        timeout=WORKER_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    log.warning(f"  [!] TIMEOUT: {source.source_name}/{node} — skipping")
                except Exception as e:
                    log.error(f"  [!] Worker error on {source.source_name}/{node}: {e}")
                finally:
                    queue.task_done()
            except asyncio.CancelledError:
                break

    def _process_node_source(self, node: str, source, lexicon: list):
        log.info(f"  [{source.source_name}] Searching: {node}")
        try:
            candidates = source.search(node, self.topic, lexicon)
            log.info(f"  [{source.source_name}] {len(candidates)} candidates for '{node}'")

            # Use deeper pull if any known high-value source appears in results
            any_high_value = any(
                (c.get("identifier") or c.get("url", "")) in self._high_value
                for c in candidates[:HIGH_VALUE_LIMIT]
            )
            limit = HIGH_VALUE_LIMIT if any_high_value else DEFAULT_PULL_LIMIT

            successes = 0
            for candidate in candidates[:limit]:
                iid = candidate.get("identifier") or candidate.get("url", "")
                if iid and iid in self._skippable_ids:
                    continue

                title_lower = candidate.get("title", "").lower()
                if any(marker in title_lower for marker in _TITLE_BLOCKLIST):
                    log.info(f"    [{source.source_name}] SKIP blocked title — {candidate.get('title','')[:50]}")
                    if iid:
                        self._skippable_ids.add(iid)
                    continue

                if iid:
                    self._record_seen(iid)

                text = source.fetch_text(candidate)
                if not text:
                    log.info(f"    [{source.source_name}] EMPTY fetch — {candidate.get('title','?')[:40]}")
                    if iid:
                        self._skippable_ids.add(iid)
                    continue

                filename = source.validate_and_save(text, node, self.topic, lexicon, candidate)
                if filename:
                    log.info(f"  [{source.source_name}] SAVED: {filename}")
                    if iid:
                        self._record_saved(iid)
                    self.researcher.mark_grounded(node, filename)
                    self.researcher.expand_from_document(text, node)
                    successes += 1
                    if successes >= 3:
                        break
                else:
                    if iid:
                        self._skippable_ids.add(iid)

            if successes == 0:
                self.researcher.mark_stalled(node)
        except Exception as e:
            log.error(f"  [!] {source.source_name} error on '{node}': {e}")

    # -------------------------------------------------------------------------
    # High-value source tracking

    def _record_seen(self, iid: str):
        with self._stats_lock:
            s = self._source_stats.setdefault(iid, {"seen": 0, "saved": 0})
            s["seen"] += 1
            seen, saved = s["seen"], s["saved"]
        if seen >= HIGH_VALUE_SEEN and saved >= HIGH_VALUE_SAVE:
            self._flag_high_value(iid)

    def _record_saved(self, iid: str):
        with self._stats_lock:
            s = self._source_stats.setdefault(iid, {"seen": 0, "saved": 0})
            s["saved"] += 1
            seen, saved = s["seen"], s["saved"]
        if seen >= HIGH_VALUE_SEEN and saved >= HIGH_VALUE_SAVE:
            self._flag_high_value(iid)

    def _flag_high_value(self, iid: str):
        if iid in self._high_value:
            return
        self._high_value.add(iid)
        log.info(f"  [★] HIGH-VALUE flagged: {iid}")
        try:
            with open(MAP_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        hv_list = data.setdefault(self.topic, {}).setdefault("high_value_sources", [])
        if iid not in hv_list:
            hv_list.append(iid)
        with open(MAP_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _load_high_value(self):
        try:
            with open(MAP_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            hv = data.get(self.topic, {}).get("high_value_sources", [])
            self._high_value.update(hv)
            junk = data.get(self.topic, {}).get("junk_sources", [])
            self._skippable_ids.update(junk)
            if junk:
                log.info(f"[*] Junk sources loaded: {len(junk)} — will skip on fetch")
        except Exception:
            pass

    # -------------------------------------------------------------------------

    def _count_files(self) -> int:
        path = os.path.join(VAULT_ROOT, self.topic)
        if not os.path.exists(path):
            return 0
        return len([f for f in os.listdir(path) if f.endswith(".txt")])

    def _save_grit(self, grit: list):
        out_dir = os.path.join(VAULT_ROOT, self.topic, "curriculum")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{self.topic}_grit.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"topic": self.topic, "grit": grit}, f, indent=2)
        log.info(f"[*] Grit saved: {out_path}")
