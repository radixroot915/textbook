"""glossary — extract bolded terms across all chapters, ask LLM for
one-line definitions grounded in the chapter text, write as appendix.
"""

import os
import re
import logging
from collections import Counter

log = logging.getLogger(__name__)

_BOLD_PAT = re.compile(r'\*\*([^*\n]{3,60})\*\*')
_STOP_PHRASES = frozenset({
    "key takeaways", "review questions", "try this", "common mistake",
    "safety", "tip", "step", "warning", "note", "important",
    "by the end", "learning outcomes",
})


def _looks_like_term(s: str) -> bool:
    sl = s.strip().lower().rstrip(":.")
    if len(sl) < 3 or len(sl) > 50:
        return False
    if sl in _STOP_PHRASES or any(sl.startswith(sp) for sp in _STOP_PHRASES):
        return False
    if sl[0].isdigit():
        return False
    # Skip pure punctuation-laden runs
    if not re.search(r'[a-z]', sl):
        return False
    return True


def _harvest_terms(chapters_content: list[str]) -> list[tuple[str, int]]:
    """Return list of (term, frequency) for bolded phrases that look like
    technical terms. Frequency >= 2 across chapters indicates real jargon.
    """
    counter: Counter = Counter()
    for content in chapters_content:
        for m in _BOLD_PAT.finditer(content):
            term = m.group(1).strip().rstrip(":.,;")
            if _looks_like_term(term):
                counter[term.lower()] += 1
    return [(t, c) for t, c in counter.most_common() if c >= 1]


def build_glossary(topic: str, chapters: list, out_dir: str) -> str | None:
    """Build a glossary appendix from bolded terms in chapter content.
    Returns the output path, or None if no terms found.
    """
    try:
        from llm.ollama_client import call_json
        from config import RESEARCHER_MODEL
    except Exception as e:
        log.debug(f"[glossary] cannot import LLM client: {e}")
        return None

    chapter_texts = [c.content for c in chapters]
    terms = _harvest_terms(chapter_texts)
    if not terms:
        log.info("[glossary] no bolded terms found")
        return None

    # Process in small batches — one big call tends to truncate or fail
    terms = terms[:40]
    full_text = "\n\n---\n\n".join(c[:1200] for c in chapter_texts)[:14000]
    entries: list[tuple[str, str]] = []
    BATCH = 8

    for i in range(0, len(terms), BATCH):
        batch = terms[i: i + BATCH]
        term_list = "\n".join(f"- {t}" for t, _c in batch)
        prompt = f"""[INST]Define each of these terms in one short sentence (max 25 words), using ONLY information from the source text below. Do NOT use outside knowledge.

If a term cannot be defined from the source, output "SKIP" for it.

TERMS:
{term_list}

SOURCE TEXT:
{full_text}

Return JSON: {{"term": "definition", ...}}. Output ONLY the JSON object.[/INST]"""

        try:
            result = call_json(RESEARCHER_MODEL, prompt, temperature=0.2,
                               timeout=120, num_ctx=8192, num_predict=1024)
        except Exception as e:
            log.debug(f"[glossary] batch {i//BATCH+1} error: {e}")
            continue

        if not isinstance(result, dict):
            log.debug(f"[glossary] batch {i//BATCH+1} returned {type(result).__name__}")
            continue

        for term, defn in result.items():
            if isinstance(defn, str) and defn.strip() and defn.strip().upper() != "SKIP":
                entries.append((term, defn.strip()))

    if not entries:
        # Fallback: at least produce a terms-list glossary without definitions
        log.info(f"[glossary] no LLM definitions — writing terms list as fallback")
        entries = [(t, "(see chapter text for context)") for t, _ in terms[:30]]

    entries.sort(key=lambda x: x[0].lower())

    lines = [
        f"# {topic.replace('_',' ').title()} — Glossary",
        "",
        f"*{len(entries)} terms drawn from chapter content.*",
        "",
    ]
    for term, defn in entries:
        lines.append(f"**{term}** — {defn.strip()}")
        lines.append("")

    out_path = os.path.join(out_dir, f"{topic}_glossary.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info(f"[glossary] {len(entries)} entries → {out_path}")
    return out_path
