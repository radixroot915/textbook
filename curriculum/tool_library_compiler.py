"""
Tool library compiler — produces a reference index instead of a narrative textbook.
Each entry covers one tool: what it is, what it does, generic variants,
basic use, basic care. No brands, no models, no prices.
"""

import os
import re
import sys
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VAULT_ROOT, RESEARCHER_MODEL
from llm.ollama_client import call as llm_call, call_json
from llm.prompts import TOOL_EXTRACT_PROMPT, TOOL_ENTRY_PROMPT

log = logging.getLogger(__name__)

TOPIC = "tool_library"


def compile_tool_library(lexicon: list, grit: list) -> tuple[str, str]:
    """
    Build tool_library_reference.md and tool_index.json.
    Returns (reference_md_path, index_json_path).
    """
    out_dir = os.path.join(VAULT_ROOT, TOPIC, "curriculum")
    os.makedirs(out_dir, exist_ok=True)

    vault_dir = os.path.join(VAULT_ROOT, TOPIC)
    sources   = _load_sources(vault_dir)
    log.info(f"[TOOL_LIB] {len(sources)} source files loaded")

    # --- Extract tool names from all sources + grit ---
    tool_names = _extract_tool_names(sources, grit)
    log.info(f"[TOOL_LIB] {len(tool_names)} unique tools identified")

    # --- Fetch images for each tool ---
    images_dir = os.path.join(out_dir, "images")
    os.makedirs(images_dir, exist_ok=True)
    try:
        from curriculum.image_fetcher import fetch_tool_image
        image_fetch_available = True
    except Exception:
        image_fetch_available = False

    # --- Generate one reference entry per tool ---
    entries = {}
    images = {}   # name -> [img_path, ...]
    consecutive_fails = 0
    MAX_CONSECUTIVE_FAILS = 8

    for i, name in enumerate(sorted(tool_names), 1):
        log.info(f"[TOOL_LIB] [{i}/{len(tool_names)}] {name}")
        relevant = _find_relevant_passages(name, sources)
        entry = _generate_entry(name, relevant)
        if entry:
            consecutive_fails = 0
            entries[name] = entry
            if image_fetch_available:
                imgs = []
                # Primary: tool overview image
                p1 = fetch_tool_image(name, images_dir)
                if p1:
                    imgs.append(p1)
                # Secondary: "using <tool>" — technique/in-use image
                p2 = fetch_tool_image(f"using {name} woodworking", images_dir)
                if p2 and p2 != p1:
                    imgs.append(p2)
                if imgs:
                    images[name] = imgs
        else:
            consecutive_fails += 1
            if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                log.error(
                    f"[TOOL_LIB] {consecutive_fails} consecutive LLM failures at "
                    f"[{i}/{len(tool_names)}] — Ollama appears down. Stopping early."
                )
                raise RuntimeError(
                    f"Ollama unresponsive: {consecutive_fails} consecutive empty responses "
                    f"(stopped at entry {i}/{len(tool_names)})"
                )

    # --- Write reference markdown ---
    md_path = os.path.join(out_dir, "tool_library_reference.md")
    _write_reference_md(entries, md_path, images)
    log.info(f"[TOOL_LIB] Reference written: {md_path}")

    # --- Write tool index (name -> anchor) ---
    index = {name: _anchor(name) for name in entries}
    index_path = os.path.join(out_dir, "tool_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump({"tools": index}, f, indent=2)
    log.info(f"[TOOL_LIB] Index written: {index_path} ({len(index)} tools)")

    return md_path, index_path


# ---------------------------------------------------------------------------
# Source loading

def _load_sources(vault_dir: str) -> list[dict]:
    sources = []
    if not os.path.exists(vault_dir):
        return sources
    for fname in os.listdir(vault_dir):
        if not fname.endswith(".txt"):
            continue
        path = os.path.join(vault_dir, fname)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            sources.append({"file": fname, "text": text[:30000]})
        except Exception as e:
            log.warning(f"[TOOL_LIB] Could not read {fname}: {e}")
    return sources


# ---------------------------------------------------------------------------
# Tool name extraction

def _extract_tool_names(sources: list[dict], grit: list) -> set[str]:
    names: set[str] = set()

    # From grit tool lists
    for item in grit:
        for t in item.get("tools", []):
            cleaned = t.strip().lower()
            if 2 < len(cleaned) < 60 and not _is_generic(cleaned):
                names.add(cleaned)

    # From each source via LLM (sample first 8000 chars per file)
    for src in sources:
        sample = src["text"][:8000]
        prompt = TOOL_EXTRACT_PROMPT.format(text=sample)
        result = call_json(RESEARCHER_MODEL, prompt, temperature=0.1, timeout=60)
        if isinstance(result, list):
            for t in result:
                if isinstance(t, str):
                    cleaned = t.strip().lower()
                    if 2 < len(cleaned) < 60 and not _is_generic(cleaned):
                        names.add(cleaned)

    names = _normalize_plurals(names)
    return names


_SKIP_EXACT = {
    "tool", "tools", "equipment", "material", "materials", "item", "items",
    "supply", "supplies", "hardware", "fastener", "fasteners", "brass", "steel",
    "wood", "metal", "plastic", "rubber", "leather", "fiber", "wire", "rope",
    "broom", "brooms", "mop", "bucket", "bicycle pump", "bicycle stand",
    "artistic brush", "spray paint", "heat", "hose", "hoop", "fit", "flints",
    "book", "books", "seal", "seals", "shops", "shop", "abrasive",
    "space shuttle", "catapult", "automated machine", "shell-mode",
}

# Single-word verbs and non-tool nouns to reject
_SKIP_VERBS = {
    "assemble", "attach", "cut", "drill", "fasten", "glue", "sand", "shape",
    "finish", "measure", "mark", "clamp", "hold", "drive", "turn", "pull",
    "push", "strike", "sharpen", "hone", "grind", "file", "scrape", "plane",
    "saw", "hammer", "nail", "screw", "bolt", "weld", "solder", "bend",
    "fit", "join", "connect", "fix", "repair", "install", "remove", "adjust",
}

_NOT_A_TOOL_SUFFIXES = {
    "ing", "ment", "tion", "ness", "ity", "ance", "ence",
}

# Words in a tool name that signal software / computer / non-workshop
_SOFTWARE_WORDS = {
    "compiler", "interpreter", "kernel", "daemon", "software", "utility",
    "script", "program", "emulator", "debugger", "assembler", "linker",
    "parser", "runtime", "framework", "library", "package", "module",
    "plugin", "extension", "application", "cli", "gui", "api", "sdk", "ide",
    "satellite", "sonar", "radar", "lidar", "ispell", "fsck", "emacs",
    "vim", "gcc", "gdb", "bash", "python", "linux", "unix", "gnu", "latex",
    "lisp", "git", "grep", "awk", "sed", "curl", "wget",
}

# Non-workshop items that sneak through other filters
_NON_WORKSHOP = {
    "gps", "global positioning system", "landsat", "garden gloves",
    "garden hose", "fire extinguisher", "extension cord",
    "inhaler", "illuminator", "extruder", "grading scale",
    "grog maker", "large metal arcs", "hard and cutting flints",
    "emerald", "flat sawing machine", "laser rangefinder",
}


def _is_generic(name: str) -> bool:
    words = name.split()
    # Too long or too short
    if len(words) > 5 or len(name) < 3:
        return True
    # Exact blocklist
    if name in _SKIP_EXACT:
        return True
    # Non-workshop exact matches
    if name in _NON_WORKSHOP:
        return True
    # Any word signals software / computer domain
    if any(w in _SOFTWARE_WORDS for w in words):
        return True
    # Parenthetical expansion of an acronym — likely technical jargon
    # e.g. "gps (global positioning system)", "gcc (gnu compiler collection)"
    if "(" in name and ")" in name:
        return True
    # Single-word verbs
    if len(words) == 1 and name in _SKIP_VERBS:
        return True
    # Words ending in verb/abstract suffixes with no second noun word
    if len(words) == 1 and any(name.endswith(sfx) for sfx in _NOT_A_TOOL_SUFFIXES):
        return True
    # Pure materials (single word, no qualifier)
    _MATERIALS = {"iron", "copper", "tin", "stone", "glass", "fabric",
                  "sand", "oil", "grease", "paint", "glue", "resin"}
    if len(words) == 1 and name in _MATERIALS:
        return True
    return False


def _normalize_plurals(names: set) -> set:
    """Collapse obvious plural duplicates: keep singular, drop plural form."""
    result = set(names)
    for name in list(names):
        # Simple trailing-s plural
        if name.endswith("s") and name[:-1] in names:
            result.discard(name)
        # -es plural
        elif name.endswith("es") and name[:-2] in names:
            result.discard(name)
    return result


# ---------------------------------------------------------------------------
# Passage retrieval

def _find_relevant_passages(tool_name: str, sources: list[dict], max_chars: int = 6000) -> str:
    passages = []
    total = 0
    pattern = re.compile(re.escape(tool_name), re.IGNORECASE)
    for src in sources:
        for para in src["text"].split("\n\n"):
            if pattern.search(para) and len(para) > 80:
                snip = para.strip()[:600]
                passages.append(snip)
                total += len(snip)
                if total >= max_chars:
                    break
        if total >= max_chars:
            break
    return "\n\n".join(passages[:12]) if passages else ""


# ---------------------------------------------------------------------------
# Entry generation

def _generate_entry(tool_name: str, passages: str) -> str:
    prompt = TOOL_ENTRY_PROMPT.format(tool_name=tool_name, passages=passages or "(no source passages found)")
    try:
        result = llm_call(RESEARCHER_MODEL, prompt, temperature=0.3, timeout=120)
        if result and len(result.strip()) > 80:
            return result.strip()
    except Exception as e:
        log.warning(f"[TOOL_LIB] Entry generation failed for '{tool_name}': {e}")
    return ""


# ---------------------------------------------------------------------------
# Output

def _anchor(name: str) -> str:
    return re.sub(r"[^\w-]", "-", name.lower()).strip("-")


def _write_reference_md(entries: dict, path: str, images: dict = None):
    images = images or {}
    base_dir = os.path.dirname(path)
    lines = ["# Tool Library Reference\n",
             "_General reference only. No brand names or specific models._\n",
             "---\n"]

    current_letter = ""
    for name in sorted(entries.keys()):
        letter = name[0].upper()
        if letter != current_letter:
            current_letter = letter
            lines.append(f"\n## {letter}\n")
        anchor = _anchor(name)
        lines.append(f"\n### {name.title()} {{#{anchor}}}\n")

        tool_imgs = images.get(name, [])

        # First image: overview — goes right after the heading
        if tool_imgs:
            img_rel = os.path.relpath(tool_imgs[0], base_dir)
            lines.append(f"\n![{name.title()}]({img_rel})\n")

        # Inject entry text, inserting second image before "**Basic use:**"
        entry_text = entries[name]
        if len(tool_imgs) >= 2:
            img_rel2 = os.path.relpath(tool_imgs[1], base_dir)
            in_use_md = f"\n![{name.title()} — in use]({img_rel2})\n"
            entry_text = entry_text.replace(
                "**Basic use:**",
                f"{in_use_md}\n**Basic use:**",
                1,
            )

        lines.append(entry_text)
        lines.append("\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
