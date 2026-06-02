"""
Cross-referencer — scans a compiled textbook markdown and injects
[-> Tool Library: tool name] citations where tool names appear.
Requires tool_index.json to exist in the tool_library curriculum folder.
"""

import os
import re
import json
import logging
from pathlib import Path

from config import VAULT_ROOT

log = logging.getLogger(__name__)

TOOL_INDEX_PATH = os.path.join(VAULT_ROOT, "tool_library", "curriculum", "tool_index.json")
_OLD_CITATION = re.compile(r'\s*\[->\s*Tool Library:[^\]]+\]')
_NEW_CITATION = re.compile(r'\[([^\]]+)\]\[tool:[^\]]+\]')
# Used for counting only — actual stripping handled by _strip_citations()
CITATION_PATTERN = re.compile(r'\[->\s*Tool Library:[^\]]+\]|\[([^\]]+)\]\[tool:[^\]]+\]')


def _strip_citations(text: str) -> str:
    """Remove any prior tool citations while preserving the underlying word.
    Old `[-> Tool Library: X]` markers vanish entirely; new
    `[word][tool:X]` reference-links collapse back to `word`.
    """
    text = _OLD_CITATION.sub("", text)
    text = _NEW_CITATION.sub(lambda m: m.group(1), text)
    return text


def load_tool_index() -> list[str]:
    """Return sorted tool names (longest first to avoid partial matches)."""
    if not os.path.exists(TOOL_INDEX_PATH):
        return []
    with open(TOOL_INDEX_PATH, encoding="utf-8") as f:
        data = json.load(f)
    names = list(data.get("tools", {}).keys())
    return sorted(names, key=len, reverse=True)


def cross_reference(md_path: str, tool_names: list[str]) -> str:
    """
    Read md_path, strip any existing citations, inject fresh ones, write back.
    Safe to call repeatedly — won't double-inject.
    Returns the path on success, empty string on skip.
    """
    if not tool_names or not os.path.exists(md_path):
        return ""

    with open(md_path, encoding="utf-8") as f:
        text = f.read()

    # Strip previous citations so recompile is always clean
    text = _strip_citations(text)

    result = _inject_citations(text, tool_names)

    # Append link-definitions block at end so [word][tool:X] resolves
    used_tools = sorted({m.group(1) for m in re.finditer(
        r'\[[^\]]+\]\[tool:([^\]]+)\]', result)})
    if used_tools:
        defs = "\n".join(f"[tool:{t}]: #tool-{t.replace(' ', '-')}" for t in used_tools)
        if "<!-- tool-refs -->" not in result:
            result += f"\n\n<!-- tool-refs -->\n{defs}\n"

    if result == text:
        log.info(f"[XREF] No tool citations added to {os.path.basename(md_path)}")
        return md_path

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(result)

    count = len(CITATION_PATTERN.findall(result)) - len(CITATION_PATTERN.findall(text))
    log.info(f"[XREF] {count} citations injected into {os.path.basename(md_path)}")
    return md_path


def _inject_citations(text: str, tool_names: list[str]) -> str:
    lines = text.split("\n")
    out = []
    for line in lines:
        # Skip headers, existing citations, code blocks
        if line.startswith("#") or CITATION_PATTERN.search(line) or line.startswith("    "):
            out.append(line)
            continue
        out.append(_cite_line(line, tool_names))
    return "\n".join(out)


def _cite_line(line: str, tool_names: list[str]) -> str:
    """Add at most one citation per tool name per line.

    Citations render as markdown reference-style links — invisible in
    prose-render mode and pluckable as a 'Tools Referenced' index later.
    """
    cited = set()
    result = line
    for name in tool_names:
        if name in cited:
            continue
        pattern = re.compile(
            r'(?<!\[)\b(' + re.escape(name) + r's?)\b(?!\])',
            re.IGNORECASE
        )
        match = pattern.search(result)
        if match:
            matched_text = match.group(1)
            replacement = f"[{matched_text}][tool:{name}]"
            result = result[:match.start()] + replacement + result[match.end():]
            cited.add(name)
    return result
