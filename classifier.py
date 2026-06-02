"""classifier — multi-dimensional content classification for saved files.

Each saved vault file gets one LLM call that returns three tags:
  - chapter_relevance: list of chapter-category labels the file applies to
  - skill_tier:        foundational | practical | theoretical | specialized | reference
  - content_type:      procedural | conceptual | comparative | troubleshooting | narrative

Stored in file_classifications.json. Used downstream by:
  - Compiler's _gather_passages to fast-filter by chapter relevance
  - Compiler's tier-aware section ordering (foundational early, theoretical late)
  - Index/reading-guide generators
"""

import os
import json
import logging
from threading import Lock

log = logging.getLogger(__name__)

_lock = Lock()

# Topic-agnostic content slots — describes WHAT a document is about
# in terms of any craft/trade, not which leather-specific chapter.
# The compiler matches these against chapter title keywords at score time.
CHAPTER_SLOTS = [
    # Materials family
    "material-properties",     # what materials are, characteristics, types
    "material-selection",      # choosing material for purpose
    "material-care",           # maintaining and storing materials
    "material-alteration",     # treating, modifying, preparing materials

    # Tools family
    "tools-basic",             # beginner essentials
    "tools-advanced",          # specialist / professional
    "tools-technique",         # using tools effectively
    "tools-equivalents",       # substitutes, workarounds, improvisation
    "tools-care",              # sharpening, cleaning, maintenance

    # Procedure family
    "procedure-foundational",  # core common procedures
    "procedure-technique",     # specific techniques
    "procedure-advanced",      # complex / specialist procedures
    "procedure-troubleshooting", # problem → cause → fix

    # Independent slots
    "safety",                  # hazards, PPE, workspace safety
    "theory",                  # science, chemistry, physics, why-it-works
    "history",                 # tradition, evolution, cultural context
    "projects",                # complete worked projects
    "reference",               # specs, lookups, comparisons, tables
]
SKILL_TIERS = ["foundational", "practical", "theoretical", "specialized", "reference"]
CONTENT_TYPES = ["procedural", "conceptual", "comparative", "troubleshooting", "narrative"]


def _store_path() -> str:
    from config import BASE_DIR
    return os.path.join(BASE_DIR, "file_classifications.json")


def _load() -> dict:
    try:
        with open(_store_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    try:
        with open(_store_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.debug(f"[classify] save error: {e}")


_PROMPT = """[INST]Classify this text excerpt from a {topic} reference document. Return JSON with exactly these three fields:

{{
  "chapter_relevance": [pick up to 3 labels from the LABELS list below that best describe this content],
  "skill_tier": "one of: foundational, practical, theoretical, specialized, reference",
  "content_type": "one of: procedural, conceptual, comparative, troubleshooting, narrative"
}}

LABELS (chapter_relevance — these are topic-agnostic content kinds):
- material-properties       — what a material is, its characteristics, types
- material-selection        — choosing the right material for a purpose
- material-care             — maintaining and storing materials
- material-alteration       — treating, modifying, preparing materials
- tools-basic               — beginner essential tools
- tools-advanced            — specialist or professional tools
- tools-technique           — how to use tools effectively
- tools-equivalents         — substitutes, workarounds, improvisation when ideal tools aren't available
- tools-care                — sharpening, cleaning, maintaining tools
- procedure-foundational    — core common procedures everyone does
- procedure-technique       — specific named techniques
- procedure-advanced        — complex or specialist procedures
- procedure-troubleshooting — problem → cause → fix content
- safety                    — hazards, protective equipment, workspace safety
- theory                    — science, chemistry, physics, "why it works"
- history                   — tradition, evolution, cultural context
- projects                  — complete worked-out projects
- reference                 — specs, lookups, comparisons, tables

SKILL TIER definitions:
- foundational = definitions, overview, history, intro material
- practical    = step-by-step how-to, hands-on technique
- theoretical  = science/chemistry/physics, "why" content, material properties
- specialized  = edge cases, advanced niche techniques, expert applications
- reference    = specs, lookup tables, comparisons, lists

CONTENT TYPE definitions:
- procedural      = numbered steps to do something
- conceptual      = explains ideas / principles
- comparative     = A vs B, when to use which
- troubleshooting = problem → cause → fix
- narrative       = historical, case study, anecdote

TEXT EXCERPT (~1200 chars):
{excerpt}

Output ONLY the JSON object — no prose, no markdown fences.[/INST]"""


def classify_file(filename: str, text: str, topic: str) -> dict | None:
    """Run the classifier on a text excerpt. Returns the classification dict
    or None on failure. Caches result by filename — subsequent calls return
    cached values without re-querying the LLM.
    """
    cached = _load().get(filename)
    if cached:
        return cached

    try:
        from llm.ollama_client import call_json
        from config import RESEARCHER_MODEL
    except Exception:
        return None

    if not text or len(text.strip()) < 200:
        return None

    excerpt = text[:1200].replace("\n\n\n", "\n\n")
    prompt = _PROMPT.format(
        topic=topic.replace("_", " "),
        excerpt=excerpt,
    )

    try:
        result = call_json(RESEARCHER_MODEL, prompt, temperature=0.0,
                           timeout=60, num_ctx=4096, num_predict=256)
    except Exception as e:
        log.debug(f"[classify] LLM error: {e}")
        return None

    if not isinstance(result, dict):
        return None

    # Validate / coerce
    cls = {
        "chapter_relevance": [],
        "skill_tier": "practical",
        "content_type": "conceptual",
    }
    cr = result.get("chapter_relevance", [])
    if isinstance(cr, list):
        cls["chapter_relevance"] = [
            str(c).lower().strip() for c in cr if isinstance(c, str)
        ][:5]
    elif isinstance(cr, str):
        cls["chapter_relevance"] = [cr.lower().strip()]

    st = str(result.get("skill_tier", "practical")).lower().strip()
    cls["skill_tier"] = st if st in SKILL_TIERS else "practical"

    ct = str(result.get("content_type", "conceptual")).lower().strip()
    cls["content_type"] = ct if ct in CONTENT_TYPES else "conceptual"

    # Cache
    with _lock:
        data = _load()
        data[filename] = cls
        _save(data)

    log.info(f"[classify] {filename[:50]}: "
             f"chapters={cls['chapter_relevance']} "
             f"tier={cls['skill_tier']} type={cls['content_type']}")
    return cls


def get_classification(filename: str) -> dict | None:
    return _load().get(filename)


def get_all() -> dict:
    return _load()
