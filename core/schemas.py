"""core.schemas — shared type contracts for the harvester's JSON I/O.

One place where every JSON file's shape is documented. TypedDicts give
type-checker hints with zero runtime cost; the load/save helpers ensure
every reader of a given file uses the same path resolution.

This module is the contract. Existing call sites continue to use their
per-module `_load` / `_save` helpers. Migration to these schemas is
incremental — new code should import from here; old code can switch when
it's touched for other reasons.
"""

from typing import TypedDict
import json
import os


# ---------------------------------------------------------------------------
# Claim DB — claims_<topic>.json

class ClaimDict(TypedDict):
    text: str
    source_file: str
    source_name: str
    type: str
    numeric: list[str]
    keywords: list[str]
    low_trust: bool


class ClaimsDB(TypedDict):
    claims: list[ClaimDict]


# ---------------------------------------------------------------------------
# Classification — file_classifications.json: {filename: ClassificationDict}

class ClassificationDict(TypedDict):
    chapter_relevance: list[str]
    skill_tier: str          # foundational | practical | theoretical | specialized | reference
    content_type: str        # procedural | conceptual | comparative | troubleshooting | narrative


# file_classifications.json shape: {filename: ClassificationDict}
FileClassifications = dict[str, ClassificationDict]


# ---------------------------------------------------------------------------
# Source attribution — file_origins.json: {topic: {filename: source_name}}

FileOrigins = dict[str, dict[str, str]]


# ---------------------------------------------------------------------------
# Drift log — drift_log_<topic>.json

class DriftLogEntry(TypedDict, total=False):
    ts: str
    file: str
    classification: dict
    topic_density: float | None
    claim_count: int | None


class DriftLog(TypedDict):
    entries: list[DriftLogEntry]


# ---------------------------------------------------------------------------
# Knowledge map — knowledge_map.json: {topic: KnowledgeMapTopic}

class KnowledgeMapNode(TypedDict, total=False):
    status: str              # pending | in_progress | stalled | complete
    files: list[str]
    discovery: str


class KnowledgeMapTopic(TypedDict, total=False):
    nodes: dict[str, KnowledgeMapNode]
    frontier_scores: dict[str, int]
    lexicon: list[str]
    junk_sources: list[str]
    high_value_sources: list[str]


KnowledgeMap = dict[str, KnowledgeMapTopic]


# ---------------------------------------------------------------------------
# Agent stats — agent_stats.json: {topic: {source: {saved, used, unused}}}

class AgentStats(TypedDict):
    saved: int
    used: int
    unused: int


AgentStatsDB = dict[str, dict[str, AgentStats]]


# ---------------------------------------------------------------------------
# Path resolution

def claims_db_path(topic: str) -> str:
    from config import BASE_DIR
    return os.path.join(BASE_DIR, f"claims_{topic}.json")


def classifications_path() -> str:
    from config import BASE_DIR
    return os.path.join(BASE_DIR, "file_classifications.json")


def origins_path() -> str:
    from config import BASE_DIR
    return os.path.join(BASE_DIR, "file_origins.json")


def drift_log_path(topic: str) -> str:
    from config import BASE_DIR
    return os.path.join(BASE_DIR, f"drift_log_{topic}.json")


def agent_stats_path() -> str:
    from config import BASE_DIR
    return os.path.join(BASE_DIR, "agent_stats.json")


def knowledge_map_path() -> str:
    from config import MAP_PATH
    return MAP_PATH


# ---------------------------------------------------------------------------
# Load / save helpers — single source of truth for path + shape

def _load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_claims_db(topic: str) -> ClaimsDB:
    return _load_json(claims_db_path(topic), {"claims": []})


def save_claims_db(topic: str, db: ClaimsDB) -> None:
    _save_json(claims_db_path(topic), db)


def load_classifications() -> FileClassifications:
    return _load_json(classifications_path(), {})


def save_classifications(data: FileClassifications) -> None:
    _save_json(classifications_path(), data)


def load_origins() -> FileOrigins:
    return _load_json(origins_path(), {})


def save_origins(data: FileOrigins) -> None:
    _save_json(origins_path(), data)


def load_drift_log(topic: str) -> DriftLog:
    return _load_json(drift_log_path(topic), {"entries": []})


def save_drift_log(topic: str, data: DriftLog) -> None:
    _save_json(drift_log_path(topic), data)


def load_agent_stats() -> AgentStatsDB:
    return _load_json(agent_stats_path(), {})


def save_agent_stats(data: AgentStatsDB) -> None:
    _save_json(agent_stats_path(), data)


def load_knowledge_map() -> KnowledgeMap:
    return _load_json(knowledge_map_path(), {})


def save_knowledge_map(data: KnowledgeMap) -> None:
    _save_json(knowledge_map_path(), data)


# ---------------------------------------------------------------------------
# QualityReport — re-export from its canonical home

from curriculum.quality_gate import QualityReport  # noqa: F401  (re-export)
