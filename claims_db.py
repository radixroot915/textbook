"""claims_db — extract, store, and serve atomic verifiable claims.

The architectural shift from write-then-verify to extract-then-write:

  1. After a vault file is saved, run claim extraction (one LLM call per file)
  2. Store each claim with: text, source_file, source_type, numeric specs,
     keywords, trust level
  3. When the compiler writes a chapter, gather only the claims relevant
     to that chapter and pass them to a constrained prompt that REQUIRES
     all factual content come from the claim list
  4. Verification becomes a lookup, not an LLM judgment

Storage: claims_<topic>.json, persistent across runs.
"""

import os
import re
import json
import logging
from threading import Lock

log = logging.getLogger(__name__)

_lock = Lock()


def _store_path(topic: str) -> str:
    from config import BASE_DIR
    return os.path.join(BASE_DIR, f"claims_{topic}.json")


def _load(topic: str) -> dict:
    try:
        with open(_store_path(topic), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"claims": []}


def _save(topic: str, data: dict):
    try:
        with open(_store_path(topic), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning(f"[claims] save error: {e}")


_EXTRACT_PROMPT = """[INST]Extract ATOMIC FACTUAL CLAIMS from this {topic} reference text. Each claim must be one specific, self-contained statement of fact — a value, procedure step, material specification, safety threshold, or named technique.

GOOD claims (extract these):
- "Vegetable-tanned leather develops a natural patina over time"
- "Saddle stitching uses two needles passing through the same hole from opposite sides"
- "Edge bevelers come in sizes labelled #1 through #5, with #1 being smallest"
- "Stropping leather should be at least 8 oz thickness for stability"

BAD claims (DO NOT extract):
- Vague: "Leatherworking requires precision"
- Opinion: "This is the best technique"
- Generic: "Safety is important"
- Multi-fact: combine into one specific claim only

TEXT (~{char_count} chars):
{text}

For each claim, output a JSON object with these fields. Return a JSON ARRAY of these objects.

{{
  "text": "<the specific claim sentence, max 200 chars>",
  "type": "specification | procedure | material | safety | technique | comparison",
  "numeric": ["<number+unit pairs from this claim, e.g. '8 oz' or '90°F'>"],
  "keywords": ["<2-4 distinctive search terms from the claim>"]
}}

Aim for 5-20 claims. Output ONLY the JSON array. No markdown fences.[/INST]"""


def extract_and_store_claims(topic: str, source_file: str,
                              source_name: str, text: str) -> int:
    """Extract claims from one source file's text and add them to the DB.
    Returns the number of new claims added.
    """
    if not text or len(text) < 500:
        return 0

    # Skip if this source_file already has claims in the DB
    db = _load(topic)
    existing_files = {c.get("source_file") for c in db["claims"]}
    if source_file in existing_files:
        return 0

    try:
        from llm.ollama_client import call_json
        from config import RESEARCHER_MODEL
    except Exception:
        return 0

    # Use up to 8000 chars — enough for substantive extraction without
    # overflowing the prompt budget
    excerpt = text[:8000]
    prompt = _EXTRACT_PROMPT.format(
        topic=topic.replace("_", " "),
        char_count=len(excerpt),
        text=excerpt,
    )

    try:
        result = call_json(RESEARCHER_MODEL, prompt, temperature=0.1,
                           timeout=120, num_ctx=8192, num_predict=2048)
    except Exception as e:
        log.debug(f"[claims] LLM error on {source_file}: {e}")
        return 0

    if not isinstance(result, list):
        log.debug(f"[claims] {source_file}: extractor returned {type(result).__name__}")
        return 0

    # Build new claims list in memory (no shared state yet — safe to do
    # outside the lock since each thread has its own LLM result).
    new_claims = []
    is_low_trust = source_name in ("reddit", "duckduckgo", "hub", "cited")
    for item in result:
        if not isinstance(item, dict):
            continue
        claim_text = str(item.get("text", "")).strip()
        if len(claim_text) < 15 or len(claim_text) > 400:
            continue
        new_claims.append({
            "text": claim_text,
            "source_file": source_file,
            "source_name": source_name,
            "type": str(item.get("type", "general")).lower(),
            "numeric": item.get("numeric", []) if isinstance(item.get("numeric"), list) else [],
            "keywords": item.get("keywords", []) if isinstance(item.get("keywords"), list) else [],
            "low_trust": is_low_trust,
        })

    if not new_claims:
        return 0

    # Critical section: re-load latest state INSIDE the lock so we don't
    # overwrite a parallel writer's appends. Previously db was loaded outside
    # the lock and the save clobbered concurrent updates (observed 619 → 615
    # regression — 4 claims lost when wiki + reddit extracted in parallel).
    with _lock:
        db = _load(topic)
        existing_files = {c.get("source_file") for c in db["claims"]}
        if source_file in existing_files:
            # A parallel thread already added this file's claims — bail out
            return 0
        db["claims"].extend(new_claims)
        _save(topic, db)
        total = len(db["claims"])

    log.info(f"[claims] {source_file[:50]}: +{len(new_claims)} claims "
             f"(total: {total})")
    return len(new_claims)


def get_claims_for_chapter(topic: str, chapter_title: str,
                            expected_topics: list[str],
                            lexicon: list[str],
                            max_claims: int = 40) -> list[dict]:
    """Filter the DB to claims most relevant to a given chapter.
    Returns up to max_claims, scored by keyword overlap with the chapter's
    title and expected_topics.
    """
    db = _load(topic)
    if not db.get("claims"):
        return []

    # Build chapter signature: meaningful words from title + topics + lexicon
    chapter_words: set[str] = set()
    for src in [chapter_title] + list(expected_topics) + list(lexicon[:15]):
        for w in re.findall(r'\b\w{4,}\b', str(src).lower()):
            if w not in _STOP:
                chapter_words.add(w)

    scored = []
    for c in db["claims"]:
        text_lower = c.get("text", "").lower()
        kws = [k.lower() for k in c.get("keywords", [])]
        # Score: text overlap + keyword overlap
        text_hits = sum(1 for w in chapter_words if w in text_lower)
        kw_hits = sum(1 for w in chapter_words if any(w in k for k in kws))
        score = text_hits * 2 + kw_hits * 3
        if score > 0:
            scored.append((score, c))

    scored.sort(key=lambda x: -x[0])
    out = [c for _, c in scored[:max_claims]]
    # Fallback: if keyword scoring returned nothing AND the DB is non-empty,
    # hand back the most-recent claims so the writer still gets a numbered
    # list to attribute against. Without this, the chapter falls through to
    # DEEP_CHAPTER_PROMPT (no attribution rule) and the fact-checker can't
    # use the [CN] marker path. Marker coverage > narrow keyword match.
    if not out and db.get("claims"):
        out = list(db["claims"])[-max_claims:]
    return out


def render_claims_for_prompt(claims: list[dict]) -> str:
    """Format a claim list for inclusion in an LLM prompt. Each claim is
    prefixed with `[CN]` (1-indexed) so the writer can cite it via the
    attribution rule in CLAIM_DRIVEN_CHAPTER_PROMPT."""
    if not claims:
        return "(no claims available)"
    lines = []
    for i, c in enumerate(claims, 1):
        marker = " [low-trust source]" if c.get("low_trust") else ""
        src = os.path.basename(c.get("source_file", "?"))[:30]
        lines.append(f"[C{i}] {c['text']} — [{src}]{marker}")
    return "\n".join(lines)


def db_stats(topic: str) -> dict:
    db = _load(topic)
    claims = db.get("claims", [])
    if not claims:
        return {"total": 0}
    by_source: dict = {}
    by_type: dict = {}
    low_trust = 0
    for c in claims:
        by_source[c.get("source_name", "?")] = by_source.get(c.get("source_name", "?"), 0) + 1
        by_type[c.get("type", "?")] = by_type.get(c.get("type", "?"), 0) + 1
        if c.get("low_trust"):
            low_trust += 1
    return {
        "total": len(claims),
        "by_source": by_source,
        "by_type": by_type,
        "low_trust": low_trust,
    }


_STOP = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "into",
    "leatherworking", "leather", "topic", "chapter", "introduction",
    "general", "basic", "advanced", "common", "their", "your",
})
