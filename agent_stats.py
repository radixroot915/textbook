"""
agent_stats — per-topic, per-agent quality tracking.

Each topic accumulates stats over runs:
  { topic: { source_name: { saved: N, used: N, unused: N } } }

Stored in BASE_DIR/agent_stats.json. Used by Coordinator to reorder/weight
the source list at startup so agents that historically produce *used*
content for a topic get tried first.

A separate file_origins.json maps filename → source_name so the compiler's
post-cleanup hook can attribute used/unused correctly.
"""
import os
import json
import logging
from threading import Lock

log = logging.getLogger(__name__)

_lock = Lock()


def _stats_path() -> str:
    from config import BASE_DIR
    return os.path.join(BASE_DIR, "agent_stats.json")


def _origins_path() -> str:
    from config import BASE_DIR
    return os.path.join(BASE_DIR, "file_origins.json")


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(path: str, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning(f"[stats] failed to save {path}: {e}")


def record_saved(topic: str, source_name: str, filename: str):
    """Increment saved count and record file origin."""
    with _lock:
        stats = _load(_stats_path())
        t = stats.setdefault(topic, {})
        s = t.setdefault(source_name, {"saved": 0, "used": 0, "unused": 0})
        s["saved"] += 1
        _save(_stats_path(), stats)

        origins = _load(_origins_path())
        origins.setdefault(topic, {})[filename] = source_name
        _save(_origins_path(), origins)


def record_compile_result(topic: str, used_files: set, all_files: set):
    """After compile, attribute used/unused back to source agents."""
    with _lock:
        origins = _load(_origins_path()).get(topic, {})
        stats = _load(_stats_path())
        t = stats.setdefault(topic, {})

        for fname in all_files:
            src = origins.get(fname)
            if not src:
                continue
            s = t.setdefault(src, {"saved": 0, "used": 0, "unused": 0})
            if fname in used_files:
                s["used"] += 1
            else:
                s["unused"] += 1

        _save(_stats_path(), stats)


def get_topic_priorities(topic: str) -> dict[str, float]:
    """Return {source_name: priority_score}. Higher = better. New agents
    (no history) get a neutral score of 1.0 so they still get tried.
    """
    stats = _load(_stats_path()).get(topic, {})
    priorities = {}
    for src, s in stats.items():
        saved = max(s.get("saved", 0), 1)
        used = s.get("used", 0)
        # Quality score: fraction of saved files that ended up used
        # Anchored so a single bad run doesn't permanently kill an agent
        priorities[src] = (used + 1) / (saved + 1)
    return priorities
