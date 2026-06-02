"""terminology — canonicalize concept naming across chapters.

Across a textbook, the same concept often gets written multiple ways:
  "saddle stitch" / "saddle stitching" / "saddle-stitched"
  "edge beveler" / "edge bevelling" / "edge-beveler"
  "vegetable-tanned" / "vegetable tanned" / "veg tan"

This module:
  1. Extracts candidate multi-word noun phrases from the textbook
  2. Groups them by morphological similarity (lemma + hyphen-normalization)
  3. Picks a canonical form per group (most frequent → tie-broken by shortest)
  4. Replaces all variants with the canonical form

Conservative: only canonicalizes phrases that have 2+ variants AND appear
3+ times total. Single-use phrases stay untouched.
"""

import re
from collections import Counter, defaultdict


# Common verb-noun suffixes to strip for lemmatization
_SUFFIX_PATS = [
    (re.compile(r'ing$'), ''),    # stitching → stitch
    (re.compile(r'ed$'), ''),     # stitched → stitch
    (re.compile(r'ies$'), 'y'),   # categories → category
    (re.compile(r'es$'), ''),     # bevelers → beveler? (handled below for s$)
    (re.compile(r's$'), ''),      # bevelers → beveler
]


def _suffix_class(word: str) -> str:
    """Crude part-of-speech classifier by suffix. Used to avoid merging
    different POS variants (gerund 'stitching' vs past-participle 'stitched').
    """
    wl = word.lower()
    if wl.endswith('ing'):
        return 'gerund'
    if wl.endswith('ed'):
        return 'past'
    if wl.endswith('s') and len(wl) > 4 and not wl.endswith('ss'):
        return 'plural'
    return 'base'


def _lemmatize_word(w: str) -> str:
    """Crude lemmatizer: strip common suffixes. Just enough to group
    morphological variants like stitch/stitching/stitched together.
    """
    wl = w.lower().strip()
    if len(wl) < 5:
        return wl
    for pat, repl in _SUFFIX_PATS:
        m = pat.search(wl)
        if m:
            return pat.sub(repl, wl)
    return wl


def _normalize_phrase(phrase: str) -> str:
    """Produce a comparison key for a multi-word phrase.
    Strip hyphens, lowercase, lemmatize each word, rejoin.
    """
    p = re.sub(r'[-\s]+', ' ', phrase.lower()).strip()
    words = [_lemmatize_word(w) for w in p.split() if w]
    return ' '.join(words)


# Match multi-word phrases worth canonicalizing: 2-3 words, mostly alpha,
# typically containing hyphens or representing technical noun phrases.
# Excludes pure stopword-only phrases.
_PHRASE_PAT = re.compile(
    r'\b('
    r'(?:[a-z][a-z]+)'                                   # word 1: lowercase
    r'(?:[\s-][a-z][a-z]+){1,2}'                         # words 2-3
    r')\b'
)

_STOPS = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "into",
    "their", "your", "you", "are", "was", "were", "have", "has",
    "use", "using", "used", "make", "made", "such", "more", "most",
    "also", "then", "when", "where", "while", "many", "some", "other",
})


def _is_canonicalizable(phrase: str) -> bool:
    """Filter out phrases that aren't worth canonicalizing — too generic
    or built mostly from stopwords.
    """
    words = phrase.lower().split()
    content = [w for w in words if w not in _STOPS]
    if len(content) < 2:
        return False
    # At least one word longer than 4 chars
    if not any(len(w) > 4 for w in content):
        return False
    return True


_WORD_TOKEN = re.compile(r'[a-z][a-z-]{2,}')


def canonicalize_textbook(md: str) -> tuple[str, int]:
    """Find inconsistent terminology across the textbook and replace each
    group with a single canonical form. Returns (modified_md, replacements_made).
    """
    # Extract candidate phrases — sliding window over word tokens, so both
    # bigrams and trigrams at each position are considered (not greedy)
    phrase_counter: Counter = Counter()

    md_lower = md.lower()
    tokens = [m.group(0) for m in _WORD_TOKEN.finditer(md_lower)]

    # Single-token compound forms ("saddle-stitched") count toward the same
    # concept as multi-word forms ("saddle stitched"). Add them as phrases.
    for tok in tokens:
        if '-' in tok:
            parts = tok.split('-')
            if 2 <= len(parts) <= 3 and all(len(p) >= 3 for p in parts):
                phrase = tok  # keep the hyphenated form for replacement
                if _is_canonicalizable(' '.join(parts)):
                    key = _normalize_phrase(phrase)
                    phrase_counter[(key, phrase)] = phrase_counter.get((key, phrase), 0) + 1

    # N-gram scan for space-separated phrases
    for n in (2, 3):
        for i in range(len(tokens) - n + 1):
            ngram = tokens[i: i + n]
            phrase = ' '.join(ngram)
            if not _is_canonicalizable(phrase):
                continue
            key = _normalize_phrase(phrase)
            phrase_counter[(key, phrase)] = phrase_counter.get((key, phrase), 0) + 1

    # Group by normalized key
    groups: dict = defaultdict(list)
    for (key, phrase), count in phrase_counter.items():
        groups[key].append((phrase, count))

    # Pick canonical per group with conservative thresholds. Audit found
    # 161 replacements per cycle was over-aggressive — making prose feel
    # mechanical. Now: only canonicalize a group when there's a CLEAR
    # majority (dominant variant ≥ 2x the others) AND ≥ 6 total occurrences.
    canonical_map: dict = {}
    for key, variants in groups.items():
        if len(variants) < 2:
            continue
        total = sum(c for _, c in variants)
        if total < 6:
            continue
        variants.sort(key=lambda v: (-v[1], len(v[0])))
        top_count = variants[0][1]
        runner_up_count = variants[1][1] if len(variants) > 1 else 0
        # Require a clear dominant — top must be ≥ 2x the next
        if top_count < 2 * runner_up_count:
            continue
        canonical = variants[0][0]
        canonical_last = canonical.split()[-1].split('-')[-1] if canonical else ''
        canonical_pos = _suffix_class(canonical_last)
        for variant, _c in variants[1:]:
            if variant == canonical:
                continue
            v_last = variant.split()[-1].split('-')[-1] if variant else ''
            if _suffix_class(v_last) != canonical_pos:
                continue
            canonical_map[variant] = canonical

    if not canonical_map:
        return md, 0

    # Apply replacements, preserving lead-cap when needed
    replacements = 0
    out = md
    for variant, canonical in canonical_map.items():
        # Build a case-insensitive word-boundary pattern
        pat = re.compile(
            r'(?<![\w-])(' + re.escape(variant) + r')(?![\w-])',
            re.IGNORECASE,
        )

        def _sub(m):
            nonlocal replacements
            replacements += 1
            matched = m.group(1)
            # Preserve capitalization style
            if matched[0].isupper():
                return canonical[0].upper() + canonical[1:]
            return canonical

        out = pat.sub(_sub, out)

    return out, replacements
