"""drift_monitor — content-aware drift detection.

Two functions:
  - is_node_on_topic(): cheap gate run on every frontier node before
    it enters the queue. Catches gap-analysis nodes like "Administrative
    districts" before they trigger any harvesting.
  - log_save(): records each saved file's classification signals to a
    drift_log_<topic>.json so we can audit drift trends.

The gate uses lexicon overlap + topic-root containment + an optional
LLM check on borderline cases. The LLM check fires only when the
deterministic checks are ambiguous — saves cost.
"""

import os
import re
import json
import logging
from datetime import datetime, UTC
from threading import Lock

log = logging.getLogger(__name__)

_lock = Lock()


def _drift_log_path(topic: str) -> str:
    from config import BASE_DIR
    return os.path.join(BASE_DIR, f"drift_log_{topic}.json")


def _topic_roots(topic: str) -> list[str]:
    """Topic root variants: e.g. 'leatherworking' → ['leatherworking',
    'leather working', 'leather work', 'leather'].
    """
    base = topic.replace('_', ' ').strip().lower()
    roots = {base}
    for w in base.split():
        if len(w) > 4:
            roots.add(w)
        if len(w) > 7:
            for suf in ('working', 'smithing', 'making', 'ing'):
                if w.endswith(suf) and len(w) > len(suf) + 3:
                    roots.add(w[:-len(suf)])
    return sorted(roots, key=len, reverse=True)


_OBVIOUS_OFF_TOPIC = frozenset({
    "administrative", "gameplay", "game modes", "demographics",
    "politics", "elections", "religion", "mythology", "fiction",
    "celebrity", "sports", "music album",
})


def is_node_on_topic(topic: str, node: str, lexicon: list[str]) -> tuple[bool, str]:
    """Return (allowed, reason). True = node is plausibly on-topic.

    Three deterministic checks, then an optional LLM fallback.
    Reason is for logging the rejection cause.
    """
    if not node or not isinstance(node, str):
        return False, "empty"

    node_lower = node.lower().strip()

    # Hard reject: obvious off-topic terms
    for bad in _OBVIOUS_OFF_TOPIC:
        if bad in node_lower:
            return False, f"obvious-off-topic ({bad})"

    # Pass 1: topic root contained anywhere
    roots = _topic_roots(topic)
    for r in roots:
        if r in node_lower:
            return True, "topic-root-match"

    # Pass 2: lexicon term contained anywhere
    for term in lexicon[:30]:
        if isinstance(term, str) and term.lower() in node_lower:
            return True, "lexicon-match"

    # Pass 3: LLM gate for borderline cases
    try:
        from llm.ollama_client import call
        from config import RESEARCHER_MODEL
    except Exception:
        # If LLM unavailable, be strict — node doesn't match topic vocab
        return False, "no-topic-or-lexicon-match (llm unavailable)"

    prompt = (
        f"[INST]Is the phrase \"{node}\" plausibly related to "
        f"\"{topic.replace('_', ' ')}\" — either as a sub-topic, a related "
        f"material/scientific/historical concept, a technique, or an adjacent "
        f"field someone studying {topic.replace('_', ' ')} would benefit from "
        f"understanding? Be permissive — answer YES if there's any reasonable "
        f"connection; only answer NO if the phrase is clearly unrelated "
        f"(politics, gaming, demographics, fiction, unrelated geography, etc.). "
        f"Answer with one word: yes or no.[/INST]"
    )
    try:
        reply = call(RESEARCHER_MODEL, prompt, temperature=0.0,
                     timeout=20, num_ctx=1024, num_predict=8)
    except Exception:
        return False, "no-topic-or-lexicon-match (llm error)"

    if reply and reply.strip().lower().startswith("y"):
        return True, "llm-confirmed"
    return False, "llm-rejected"


def log_save(topic: str, filename: str, classification: dict | None,
             topic_density: float | None = None,
             claim_count: int | None = None):
    """Append a drift-log entry for a saved file."""
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "file": filename,
        "classification": classification or {},
        "topic_density": topic_density,
        "claim_count": claim_count,
    }
    with _lock:
        path = _drift_log_path(topic)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"entries": []}
        data["entries"].append(entry)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.debug(f"[drift] log write error: {e}")


def drift_summary(topic: str, window: int = 20) -> dict:
    """Summarize drift indicators over the last `window` saves."""
    path = _drift_log_path(topic)
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {"recent_entries": 0}
    entries = data.get("entries", [])[-window:]
    if not entries:
        return {"recent_entries": 0}

    # Counts of anomalous classifications
    n = len(entries)
    refs = sum(1 for e in entries if e.get("classification", {}).get("skill_tier") == "reference")
    narr = sum(1 for e in entries if e.get("classification", {}).get("content_type") == "narrative")
    avg_density = sum(e.get("topic_density") or 0 for e in entries) / max(n, 1)
    return {
        "recent_entries": n,
        "reference_ratio": refs / n,
        "narrative_ratio": narr / n,
        "avg_topic_density": round(avg_density, 3),
    }
