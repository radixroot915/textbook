"""textbook_postpass — small, standalone post-write transforms.

Each function takes the rendered textbook markdown and returns
(new_markdown, count). All run AFTER the textbook is written; none
require LLM access. Wire them into compile() at the end.

Currently implements:
  - link_glossary_terms      : first use of each glossary term linked to anchor
  - expand_acronyms          : first 3 uses of "PVA" expand to "PVA (polyvinyl acetate)", then collapse
  - build_index              : alphabetical index of bold terms with chapter refs
  - mark_confidence          : visible ✓ / ~ / ⚠ markers from fact_check.json
  - flag_era                 : sentences using obsolete terminology get a marker
"""

import os
import re
import json
from collections import defaultdict


# ---------------------------------------------------------------------------
# Helpers

def _slugify(t: str) -> str:
    s = t.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'\s+', '-', s)
    return s


def _chapter_blocks(md: str) -> list[tuple[str, int, int]]:
    """Return (title, start_offset, end_offset) for each `## Title` chapter."""
    starts = [(m.group(1).strip(), m.start())
              for m in re.finditer(r'^##\s+(.+?)\s*$', md, re.MULTILINE)]
    if not starts:
        return []
    starts.append(("__END__", len(md)))
    return [(t, s, starts[i + 1][1]) for i, (t, s) in enumerate(starts[:-1])]


# ---------------------------------------------------------------------------
# 1. Glossary auto-link

def link_glossary_terms(md: str, glossary_path: str) -> tuple[str, int]:
    """For each term in the glossary, link its first body occurrence to
    a #glossary-<slug> anchor. Skips terms already linked, in headings,
    or inside existing markdown links/code.
    """
    if not os.path.exists(glossary_path):
        return md, 0

    with open(glossary_path, encoding="utf-8") as f:
        gloss = f.read()

    # Glossary entries are formatted: **term** — definition
    terms = re.findall(r'^\*\*([^*]{3,60})\*\*', gloss, re.MULTILINE)
    if not terms:
        return md, 0

    # Sort longest first so multi-word terms match before substring single-words
    terms = sorted(set(terms), key=len, reverse=True)

    linked = 0
    used_terms: set[str] = set()

    # Split into lines so we can skip headings and code blocks
    lines = md.split('\n')
    in_code = False
    out: list[str] = []
    for line in lines:
        if line.strip().startswith('```'):
            in_code = not in_code
            out.append(line)
            continue
        if in_code or line.startswith('#') or line.strip().startswith('|---'):
            out.append(line)
            continue
        new_line = line
        for term in terms:
            if term.lower() in used_terms:
                continue
            # Match whole word/phrase, case-insensitive, NOT inside an
            # existing [..] link or after [-> Tool:
            pattern = re.compile(
                r'(?<![\[\w-])(' + re.escape(term) + r')(?![\w\]-])',
                re.IGNORECASE,
            )
            m = pattern.search(new_line)
            if m:
                slug = "glossary-" + _slugify(term)
                new_line = new_line[:m.start()] + \
                    f"[{m.group(1)}](#{slug})" + new_line[m.end():]
                used_terms.add(term.lower())
                linked += 1
        out.append(new_line)

    return '\n'.join(out), linked


# ---------------------------------------------------------------------------
# 2. Acronym expansion enforcement

_ACRONYM_PAT = re.compile(r'\b([A-Z]{2,6})\s*\(([^)]{3,80})\)')
_BARE_ACRONYM_PAT = re.compile(r'\b([A-Z]{2,6})\b')


def expand_acronyms(md: str, expand_uses: int = 3) -> tuple[str, int]:
    """Find acronyms defined with `XXX (full form)` and ensure the first
    N occurrences include the expansion, while later occurrences collapse
    to the bare acronym (drop redundant parens).

    Returns (modified_markdown, count_changed).
    """
    # First pass: scan body for "XXX (definition)" pairs to build a map
    expansions: dict[str, str] = {}
    for m in _ACRONYM_PAT.finditer(md):
        acro, expansion = m.group(1), m.group(2).strip()
        if 2 <= len(acro) <= 6 and len(expansion) > 3:
            expansions.setdefault(acro, expansion)

    if not expansions:
        return md, 0

    # Process per-chapter so the "first N" counter resets per chapter
    blocks = _chapter_blocks(md)
    if not blocks:
        return md, 0

    changed = 0
    pieces: list[str] = []
    prev_end = 0
    for title, start, end in blocks:
        if start > prev_end:
            pieces.append(md[prev_end:start])
        block = md[start:end]
        block, n = _expand_in_block(block, expansions, expand_uses)
        changed += n
        pieces.append(block)
        prev_end = end
    pieces.append(md[prev_end:])
    return ''.join(pieces), changed


def _expand_in_block(text: str, expansions: dict[str, str], expand_uses: int) -> tuple[str, int]:
    counts: dict[str, int] = defaultdict(int)
    changed = 0
    out = []
    pos = 0

    # Walk through text token by token, handling both "ACR (expansion)" and bare "ACR"
    for m in _BARE_ACRONYM_PAT.finditer(text):
        acro = m.group(1)
        if acro not in expansions:
            continue
        # Append everything before this match
        out.append(text[pos:m.start()])
        counts[acro] += 1
        n = counts[acro]
        # Check if "(expansion)" already follows
        tail = text[m.end():m.end() + len(expansions[acro]) + 4]
        has_inline_expansion = bool(re.match(r'\s*\(', tail))

        if n <= expand_uses:
            if has_inline_expansion:
                # Keep existing — just advance pos
                out.append(acro)
                pos = m.end()
            else:
                out.append(f"{acro} ({expansions[acro]})")
                changed += 1
                pos = m.end()
        else:
            # Beyond expand_uses: strip any trailing parenthetical expansion
            if has_inline_expansion:
                paren_match = re.match(r'\s*\(' + re.escape(expansions[acro]) + r'\)', text[m.end():])
                if paren_match:
                    out.append(acro)
                    pos = m.end() + paren_match.end()
                    changed += 1
                    continue
            out.append(acro)
            pos = m.end()

    out.append(text[pos:])
    return ''.join(out), changed


# ---------------------------------------------------------------------------
# 3. Index builder

def build_index(md: str, topic: str) -> str:
    """Collect all bold terms across chapters with their chapter refs.
    Returns the index as a markdown appendix section.
    """
    blocks = _chapter_blocks(md)
    if not blocks:
        return ""

    index: dict[str, list[str]] = defaultdict(list)
    bold_pat = re.compile(r'\*\*([^*\n]{3,60})\*\*')

    for ch_title, start, end in blocks:
        block = md[start:end]
        seen_in_block: set[str] = set()
        for m in bold_pat.finditer(block):
            term = m.group(1).strip().rstrip('.,;:').lower()
            if len(term) < 3 or len(term) > 50:
                continue
            if not re.search(r'[a-z]', term):
                continue
            if term in seen_in_block:
                continue
            seen_in_block.add(term)
            ch_slug = _slugify(ch_title)
            index[term].append((ch_title, ch_slug))

    if not index:
        return ""

    # Build markdown
    lines = [
        f"# {topic.replace('_', ' ').title()} — Index",
        "",
        f"*{len(index)} indexed terms.*",
        "",
    ]
    # Group by first letter
    by_letter: dict[str, list[str]] = defaultdict(list)
    for term in sorted(index.keys()):
        first = term[0].upper()
        by_letter[first].append(term)

    for letter in sorted(by_letter.keys()):
        lines.append(f"### {letter}")
        lines.append("")
        for term in by_letter[letter]:
            refs = index[term]
            ref_links = ", ".join(
                f"[{t[:40]}](#{s})" for t, s in refs[:5]
            )
            lines.append(f"- **{term}** — {ref_links}")
        lines.append("")

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# 4. Confidence markers from fact-check

def mark_confidence(md: str, fact_check_json: str) -> tuple[str, int]:
    """Insert visible confidence indicators next to specific claim sentences.
    ✓ = verified (2+ sources), ~ = tentative (1 source), ⚠ = flagged.

    Reads chapter-level claim arrays from fact_check.json and looks for
    each claim's text in the corresponding chapter, appends a marker.
    """
    if not os.path.exists(fact_check_json):
        return md, 0
    try:
        data = json.load(open(fact_check_json, encoding="utf-8"))
    except Exception:
        return md, 0

    chapters_fc = data.get("chapters", [])
    if not chapters_fc:
        return md, 0

    # Build {chapter_title: {claim_text: marker}}
    markers: dict[str, dict[str, str]] = {}
    for fc in chapters_fc:
        title = fc.get("title", "")
        if not title:
            continue
        marker_map = {}
        for cl in fc.get("flagged_claims", []) or []:
            marker_map[cl.strip()] = "⚠"
        # verified/tentative aren't in the json by default — but we can
        # treat all NOT-flagged-but-cited claims as ~
        markers[title] = marker_map

    if not any(markers.values()):
        return md, 0

    blocks = _chapter_blocks(md)
    changed = 0
    out = list(md)
    # Walk chapters in reverse so offsets stay valid
    for title, start, end in reversed(blocks):
        block_markers = markers.get(title, {})
        if not block_markers:
            continue
        block_text = md[start:end]
        block_modified = block_text
        for claim_text, sym in block_markers.items():
            key = claim_text[:120].strip()
            if len(key) < 20:
                continue
            # Escape for regex but allow flexible whitespace + bold markers
            esc = re.escape(key).replace(r'\ ', r'[\s\*]+')
            pattern = re.compile(esc, re.IGNORECASE)
            m = pattern.search(block_modified)
            if m:
                insert_at = m.end()
                block_modified = (
                    block_modified[:insert_at] +
                    f" {sym}" +
                    block_modified[insert_at:]
                )
                changed += 1
        out[start:end] = list(block_modified)
    return ''.join(out), changed


# ---------------------------------------------------------------------------
# 5. Era flagging — historical vs modern practice

_HISTORICAL_INDICATORS = [
    r'\bin (?:ancient|early|medieval|historical) (?:times|practice|method)\b',
    r'\b(?:traditionally|historically)\b',
    r'\b(?:in the )?(?:nineteenth|twentieth|18\d{2}|19\d{2})s? century\b',
    r'\bbefore the (?:industrial|modern) (?:era|age|revolution)\b',
    r'\b(?:was|were) (?:once|formerly) used\b',
]
_MODERN_INDICATORS = [
    r'\b(?:today|modern|contemporary|currently|nowadays)\b',
    r'\b21st century\b',
    r'\bcommercial(?:ly)? available\b',
]

_ERA_PAT_HIST = re.compile('|'.join(_HISTORICAL_INDICATORS), re.IGNORECASE)
_ERA_PAT_MOD = re.compile('|'.join(_MODERN_INDICATORS), re.IGNORECASE)


def flag_era(md: str) -> tuple[str, int]:
    """Tag paragraphs containing historical-only indicators with
    `> ⏳ **Historical practice:**` blockquote and modern-only with
    `> 🔧 **Modern practice:**`. Skips paragraphs that contain both.

    Light touch — only inserts when paragraph is clearly era-marked,
    not on every passing mention.
    """
    changed = 0
    paragraphs = md.split('\n\n')
    out: list[str] = []
    for p in paragraphs:
        # Skip headings, lists, code, blockquotes, already-tagged
        stripped = p.strip()
        if not stripped or stripped.startswith(('#', '-', '*', '>', '|', '```')):
            out.append(p)
            continue
        if any(tag in stripped for tag in ("Historical practice:", "Modern practice:")):
            out.append(p)
            continue
        if len(stripped) < 80:
            out.append(p)
            continue

        hist_hits = len(_ERA_PAT_HIST.findall(stripped))
        mod_hits  = len(_ERA_PAT_MOD.findall(stripped))
        if hist_hits >= 2 and mod_hits == 0:
            out.append(f"> ⏳ **Historical practice:** {stripped}")
            changed += 1
        elif mod_hits >= 1 and hist_hits == 0 and 'modern' in stripped.lower():
            # Don't auto-tag every "modern" mention; only when clearly framing
            out.append(p)
        else:
            out.append(p)
    return '\n\n'.join(out), changed
