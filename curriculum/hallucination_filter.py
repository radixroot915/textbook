"""hallucination_filter — post-write validator for specific-value claims.

How hallucinations are filtered:

1. Extract every "specific value" sentence deterministically — anything
   containing a number+unit, a year/date, a standard ID, a brand-style
   capitalized phrase, or a quoted procedure name.

2. For each extracted specific, check the claim DB:
   - Number+unit match: value appears within ±15% in any claim of similar context
   - Year match: exact year appears in a claim
   - Standard ID: exact identifier appears in a claim
   - Capitalized phrase: appears as a whole or partial substring in a claim

3. Specifics that don't match any claim → strip the containing sentence.
   The chapter loses content rather than carrying invented facts.

Key principle: this is the RAILROAD's enforcement. The prompt asks the LLM
to behave; the filter enforces it mechanically after the fact. Catches
"plausible but invented" specifics like "65–75°F" or "5% laser power"
that slip through "anchored" generation.
"""

import os
import re
import json
import logging

log = logging.getLogger(__name__)


# Specific-value patterns to extract
_NUM_UNIT_PAT = re.compile(
    r'\b(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)'
    r'\s*(°[CF]|°|%|mm|cm|m|km|in|inch|inches|ft|kg|g|lb|oz|psi|bar|'
    r'amps?|A|V|hz|hp|rpm|sec(?:onds?)?|min(?:utes?)?|hr|hours?|'
    r'days?|weeks?|months?|years?|gauge|grit|gsm)\b',
    re.IGNORECASE,
)

_YEAR_PAT = re.compile(
    r'\b(1[5-9]\d{2}|20[0-2]\d)s?\b'
    r'|\b(early|mid|late)\s+(\d+(?:st|nd|rd|th))\s+century\b'
    r'|\b(\d+(?:st|nd|rd|th))\s+century\b',
    re.IGNORECASE,
)

_STANDARD_PAT = re.compile(
    r'\b(ASTM|ANSI|AISI|ISO|EN|DIN|JIS|MIL|NFPA|OSHA|NIOSH|UL|CE)'
    r'[\s-]?[A-Z]?\d+[A-Z0-9-]*\b'
)

# Capitalized proper-noun phrases — 2-4 words in Title Case.
# Excludes common chapter-section heads and sentence-start words.
_PROPER_NOUN_PAT = re.compile(
    r'\b([A-Z][a-z]+(?:[\s-][A-Z][a-z]+){1,3})\b'
)

# Words that shouldn't be flagged as proper-noun-style hallucinations even
# when capitalized — common chapter labels, section markers, generic terms.
_PROPER_NOUN_ALLOWLIST = frozenset({
    "learning outcomes", "review questions", "answer key", "try this",
    "key takeaways", "summary", "common mistake", "safety", "tip",
    "section", "chapter", "introduction", "conclusion", "table of contents",
    "see also", "by the end", "in this chapter", "for example",
    "north america", "south america", "middle east", "west africa",
    "north", "south", "east", "west",
    # Common generic English bigrams that pass the regex but aren't entities
    "you can", "you will", "you should", "they are", "this is",
    "the same", "the most", "the best", "the right",
})


def _is_real_proper_noun(phrase: str) -> bool:
    """Filter out sentence-start capitalizations and generic bigrams that
    happen to be Title Case (e.g. "The Leather"). True only for what looks
    like a real named entity — brand, product, person, place.
    """
    pl = phrase.lower().strip()
    if pl in _PROPER_NOUN_ALLOWLIST:
        return False
    # Drop leading determiners which often trigger false hits at sentence start
    words = pl.split()
    if not words:
        return False
    if words[0] in {"the", "a", "an", "this", "that", "these", "those"}:
        return False
    # Sentence-start single capitalized word is too common to flag
    if len(words) == 1:
        return False
    return True


# Sentence splitter — coarse but works on prose paragraphs
_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z\d])')


def _all_claims_text(claims: list[dict]) -> str:
    """Concatenated lowercased corpus of all claim texts and their numerics."""
    parts = []
    for c in claims:
        parts.append(c.get("text", "").lower())
        for n in c.get("numeric", []) or []:
            parts.append(str(n).lower())
    return " || ".join(parts)


def _number_appears_in_claims(value_str: str, unit: str, claims_text: str) -> bool:
    """Check whether a numeric value (with unit) plausibly appears in any
    claim. Tolerates ±15% match for non-integer ranges to allow paraphrase.
    """
    # Direct substring (cheap path) — covers exact wording
    unit_norm = unit.lower().strip()
    val_norm = value_str.strip().replace(' ', '')
    if f"{val_norm}{unit_norm}" in claims_text.replace(' ', ''):
        return True
    if f"{val_norm} {unit_norm}" in claims_text:
        return True

    # Parse value (single or range) and check ±15% tolerance
    range_match = re.match(r'(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)', value_str)
    if range_match:
        try:
            low = float(range_match.group(1))
            high = float(range_match.group(2))
        except ValueError:
            return False
        # Look for any number in claims_text near this range
        for m in re.finditer(r'\b(\d+(?:\.\d+)?)\b', claims_text):
            try:
                v = float(m.group(1))
            except ValueError:
                continue
            # Match if v is within the claim's range or within 15% of either endpoint
            if low * 0.85 <= v <= high * 1.15:
                # Verify the unit is also nearby in the claim text
                start = max(0, m.start() - 30)
                end = min(len(claims_text), m.end() + 30)
                if unit_norm in claims_text[start:end]:
                    return True
        return False

    # Single number
    single_match = re.match(r'(\d+(?:\.\d+)?)', value_str)
    if not single_match:
        return False
    try:
        v = float(single_match.group(1))
    except ValueError:
        return False
    lo, hi = v * 0.85, v * 1.15
    for m in re.finditer(r'\b(\d+(?:\.\d+)?)\b', claims_text):
        try:
            c = float(m.group(1))
        except ValueError:
            continue
        if lo <= c <= hi:
            start = max(0, m.start() - 30)
            end = min(len(claims_text), m.end() + 30)
            if unit_norm in claims_text[start:end]:
                return True
    return False


def _string_appears_in_claims(s: str, claims_text: str) -> bool:
    """Exact substring match against claims corpus (case-insensitive)."""
    return s.lower() in claims_text


def filter_chapter(content: str, claims: list[dict]) -> tuple[str, dict]:
    """Strip sentences containing specifics unsupported by the claim DB.

    Returns (filtered_content, stats) where stats is:
      {sentences_total, sentences_stripped, specifics_checked,
       specifics_unsupported, conversion_errors}
    """
    if not claims:
        return content, {"sentences_total": 0, "sentences_stripped": 0,
                          "specifics_checked": 0, "specifics_unsupported": 0,
                          "conversion_errors": 0}

    # First pass: collect sentences with bad unit conversions so we can
    # strip them too. Returns a set of sentence-prefixes that flagged.
    bad_conversion_prefixes: set = set()
    try:
        from curriculum.plausibility import check_unit_conversions
        for w in check_unit_conversions(content):
            bad_conversion_prefixes.add(w.sentence[:60].lower().strip())
    except Exception:
        pass

    claims_text = _all_claims_text(claims)
    stats = {
        "sentences_total": 0,
        "sentences_stripped": 0,
        "specifics_checked": 0,
        "specifics_unsupported": 0,
        "conversion_errors": len(bad_conversion_prefixes),
    }

    # Pedagogy sections — Try This / Review Questions / Answer Key — are
    # scaffolding for the reader, not factual claims to verify. Filtering
    # them strips legitimate pedagogical content (the v10 regression).
    _PEDAGOGY_HEADERS = re.compile(
        r'^#{2,4}\s*(?:🔧\s*)?(?:try this|review questions|answer key|learning outcomes|summary|key takeaways)\b',
        re.IGNORECASE | re.MULTILINE,
    )
    _ANY_HEADER = re.compile(r'^#{2,4}\s', re.MULTILINE)

    # Process at paragraph level so we keep block structure intact
    out_paragraphs = []
    in_pedagogy_section = False

    for para in content.split("\n\n"):
        stripped_para = para.strip()

        # Track whether we're inside a pedagogy section by watching headers.
        # Any header starting a pedagogy block flips us in; any other header
        # flips us out.
        if _ANY_HEADER.search(stripped_para):
            if _PEDAGOGY_HEADERS.search(stripped_para):
                in_pedagogy_section = True
            elif stripped_para.startswith(("##", "###", "####")):
                # New non-pedagogy header — exit pedagogy mode
                in_pedagogy_section = False

        # Skip filtering entirely while inside a pedagogy section
        if in_pedagogy_section:
            out_paragraphs.append(para)
            continue

        # Skip headings, blockquotes, code, lists — they have their own rules
        if not stripped_para or stripped_para.startswith(("#", ">", "```", "|")):
            out_paragraphs.append(para)
            continue
        if stripped_para.startswith(("-", "*", "1.", "2.", "3.")):
            out_paragraphs.append(para)
            continue

        sentences = _SENT_SPLIT.split(para)
        kept = []
        for sent in sentences:
            s = sent.strip()
            if len(s) < 20:
                kept.append(sent)
                continue
            stats["sentences_total"] += 1

            sentence_ok = True

            # Bad unit-pair conversion (e.g. "30°C / 50°F" — wrong)
            if any(s.lower().startswith(prefix)
                   for prefix in bad_conversion_prefixes):
                sentence_ok = False

            # Numeric+unit specifics
            if not sentence_ok:
                stats["sentences_stripped"] += 1
                continue
            for m in _NUM_UNIT_PAT.finditer(s):
                stats["specifics_checked"] += 1
                value_str, unit = m.group(1), m.group(2)
                if not _number_appears_in_claims(value_str, unit, claims_text):
                    stats["specifics_unsupported"] += 1
                    sentence_ok = False
                    break

            # Year / century specifics
            if sentence_ok:
                for m in _YEAR_PAT.finditer(s):
                    stats["specifics_checked"] += 1
                    matched = m.group(0)
                    if not _string_appears_in_claims(matched, claims_text):
                        stats["specifics_unsupported"] += 1
                        sentence_ok = False
                        break

            # Standard IDs
            if sentence_ok:
                for m in _STANDARD_PAT.finditer(s):
                    stats["specifics_checked"] += 1
                    if not _string_appears_in_claims(m.group(0), claims_text):
                        stats["specifics_unsupported"] += 1
                        sentence_ok = False
                        break

            # Proper-noun named entities — DISABLED for stripping (audit
            # found this was destroying legitimate brand references like
            # "Amy Roke clamps", "Hermès-style bags"). Kept as count-only
            # signal in stats so we can see how often it WOULD have fired.
            if sentence_ok:
                tail = s[len(s.split(maxsplit=1)[0]):] if ' ' in s else ''
                for m in _PROPER_NOUN_PAT.finditer(tail):
                    phrase = m.group(1)
                    if not _is_real_proper_noun(phrase):
                        continue
                    stats["specifics_checked"] += 1
                    if not _string_appears_in_claims(phrase, claims_text):
                        # Count as warning but do NOT strip
                        stats.setdefault("proper_noun_warnings", 0)
                        stats["proper_noun_warnings"] += 1

            if sentence_ok:
                kept.append(sent)
            else:
                stats["sentences_stripped"] += 1

        if kept:
            out_paragraphs.append(" ".join(kept).strip())
        else:
            out_paragraphs.append("")

    filtered = "\n\n".join(p for p in out_paragraphs if p.strip())
    filtered = re.sub(r'\n{3,}', '\n\n', filtered).strip()
    return filtered, stats
