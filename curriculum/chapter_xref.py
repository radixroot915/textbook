"""chapter_xref — detect mentions of terms that are headers in OTHER
chapters and link them via markdown anchors. Lets readers navigate
"see Chapter 5" connections automatically.
"""

import re
import logging

log = logging.getLogger(__name__)

# Match "## N. Title" or "## Title" chapter headers
_CHAPTER_HDR = re.compile(r'^##\s+(?:\d+[\.\)]\s*)?(.+?)\s*$', re.MULTILINE)


def _slugify(title: str) -> str:
    s = title.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'\s+', '-', s)
    return s


def _key_terms_for(title: str) -> list[str]:
    """Phrases worth linking when they appear in other chapter bodies.
    Includes the full lowercased title and a stopword-trimmed variant.
    """
    cleaned = re.sub(r'[^\w\s]', ' ', title.lower()).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    if not cleaned:
        return []

    words = cleaned.split()
    content_words = [w for w in words if len(w) > 3 and w not in _STOPWORDS]
    if not content_words:
        return []

    candidates = [cleaned]

    trimmed = list(words)
    while trimmed and (trimmed[0] in _STOPWORDS or len(trimmed[0]) <= 3):
        trimmed.pop(0)
    while trimmed and (trimmed[-1] in _STOPWORDS or len(trimmed[-1]) <= 3):
        trimmed.pop()
    if trimmed:
        short = " ".join(trimmed)
        if short != cleaned and len(short) >= 8:
            candidates.append(short)

    # Longest first so greedy substring replace matches the most specific form
    seen: set = set()
    out = []
    for c in sorted(candidates, key=len, reverse=True):
        if c not in seen and len(c) >= 8:
            seen.add(c)
            out.append(c)
    return out


_STOPWORDS = frozenset({
    "introduction", "chapter", "guide", "topic", "section",
    "with", "from", "this", "that", "into", "their", "your",
    "general", "basic", "fundamental", "advanced", "and",
})


def strip_broken_anchors(markdown: str) -> tuple[str, int]:
    """Remove markdown links of the form `[text](#slug)` when the slug
    doesn't match any heading in the document. Replaces the link with
    its plain text. Returns (cleaned_markdown, count_stripped).
    """
    valid_slugs: set[str] = set()
    for m in re.finditer(r'^(#{1,6})\s+(.+?)\s*$', markdown, re.MULTILINE):
        valid_slugs.add(_slugify(m.group(2)))

    count = 0

    def unlink(m):
        nonlocal count
        text, slug = m.group(1), m.group(2)
        if slug in valid_slugs:
            return m.group(0)
        count += 1
        return text

    out = re.sub(r'\[([^\]]+?)\]\(#([^\)]+?)\)', unlink, markdown)
    return out, count


def link_chapters(markdown: str) -> tuple[str, int]:
    """Find chapter titles in the markdown and insert markdown anchor
    links to them wherever their key phrases appear in OTHER chapter bodies.
    Returns (updated_markdown, link_count).
    """
    chapter_titles = _CHAPTER_HDR.findall(markdown)
    if len(chapter_titles) < 2:
        return markdown, 0

    title_to_slug = {t: _slugify(t) for t in chapter_titles}

    # Split markdown into chapter blocks so we don't self-link
    chapter_offsets = [(m.group(1), m.start(), m.end())
                       for m in _CHAPTER_HDR.finditer(markdown)]
    chapter_offsets.append(("__END__", len(markdown), len(markdown)))

    out = []
    last_end = 0
    link_count = 0

    for i in range(len(chapter_offsets) - 1):
        title, hdr_start, hdr_end = chapter_offsets[i]
        next_start = chapter_offsets[i + 1][1]

        # Chapter body = from end of this header to start of next header
        body = markdown[hdr_end:next_start]

        # Link to OTHER chapters' phrases within this body
        for other_title, slug in title_to_slug.items():
            if other_title == title:
                continue
            for phrase in _key_terms_for(other_title):
                # Avoid matching inside existing links or headers
                pattern = re.compile(
                    r'(?<![\[\w-])(' + re.escape(phrase) + r')(?![\w\]-])',
                    re.IGNORECASE,
                )
                # Limit to one link per phrase per chapter body
                new_body, n = pattern.subn(
                    lambda m: f"[{m.group(1)}](#{slug})",
                    body,
                    count=1,
                )
                if n:
                    body = new_body
                    link_count += 1

        # Append unchanged prefix + header + (possibly modified) body
        out.append(markdown[last_end:hdr_end])
        out.append(body)
        last_end = next_start

    out.append(markdown[last_end:])
    return "".join(out), link_count
