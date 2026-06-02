# Harvester — Dev Log

---

## 2026-05-16 / 2026-05-17

### Textbook compiler — quality fixes (`curriculum/textbook_compiler.py`)

**Duplicate chapter headings**
The LLM chapter output started with `# **Chapter Title**` (H1). The compiler also wraps each chapter with `## N. Chapter Title` when assembling the document, producing a duplicate heading. Fixed in `_clean_chapter_output()`: strip any leading H1 line from chapter content before assembly (`_LEADING_H1` regex).

**Bold markers in H2/H3 headings**
LLM output used `## **Introduction**`, `### **Procedures**` etc. Added `_normalize_headings()` called in `_write_textbook()` to strip `**...**` markers and leading sub-section numbers from all H2+ headings.

**Truncated chapters**
`CHAPTER_PREDICT` raised from 2048 → 4096 to prevent mid-sentence truncation.

**Code fences / LLM preamble in output**
LLM wrapped chapter text in ` ```markdown ``` ` blocks and sometimes prepended "Here is the improved chapter:". `_clean_chapter_output()` now strips code fences (`_CODE_FENCE` / `_CODE_FENCE_CLOSE`), preamble commentary (`_LLM_PREAMBLE`), and `[⚠ UNVERIFIED]` annotation markers (`_UNVERIFIED_ANNOTATION`).

**Edit pass — unconditional issues**
Brand name removal, code fence removal, and historical-padding removal were conditional. Now unconditional in every edit pass regardless of detected issues.

**Fallback chapter structure hardcoded to welding**
`_fallback_chapters()` was hardcoded to welding topics. Replaced with a generic topic-aware fallback built from grit tasks and lexicon.

**Chapter plan prompt**
Tightened `CHAPTER_PLAN_PROMPT` with explicit topic enforcement and example JSON format to prevent off-topic chapter planning.

---

### Image pipeline — new (`curriculum/image_fetcher.py`, `curriculum/textbook_compiler.py`, `curriculum/export_pdf.py`)

**image_fetcher.py** — new file. Searches Wikimedia Commons, scores candidates, caches permanently.

Scoring per candidate:
- `+4` all term words in filename
- `+2` primary noun in filename
- `+1` Commons description mentions term
- `+2` is a JPG/PNG (not SVG icon)
- `+1` original width ≥ 300px
- `-4` noise words (logo, icon, flag, map, seal, emblem, …)
- `-3` schema/schematic in filename
- `-2` SVG + icon/logo
- `-2` tiny image

Content gate (added mid-session): if `content_score == 0` (no filename or description match), hard-reject regardless of photo quality. Prevents "large JPG of unrelated subject" false positives (e.g. James Gillray political cartoon matching "Introduction to Carpentry").

Stop-word filter: common adjectives (`essential`, `basic`, `advanced`, `general`, `standard`, `complete`, …) excluded from `term_words` when scoring filename matches. Prevents "Essential" in chapter title matching a military preparedness photo.

**textbook_compiler.py — section image injection**

`_inject_section_images()` per chapter:
- Intro image before first `##` heading, using `_intro_image_terms()` (see below)
- Per-`###` section images for headings matching `_TECHNIQUE_KEYWORDS`
- `MAX_IMAGES_PER_CHAPTER = 5`

`_strip_md_inline()`: strips `**bold**`, `*italic*`, numbered/decimal prefixes (`1.`, `4.2.`) from heading text before using as image search term.

`_intro_image_terms()`: extracts core nouns from chapter title by stripping structural noise words (`introduction`, `overview`, `basic`, `advanced`, `to`, `the`, `and`, `of`, …) before building search terms. Prevents long generic titles from matching unrelated images.

`_NUMBERED_PREFIX` regex updated to cover decimal sub-section numbers (`4.2`, `4.3`, `1.2.3`).

**export_pdf.py** — fpdf2 backend: `figure()` method embeds image centered with italic grey caption. weasyprint backend: replaces `![alt](path)` with `<figure><img/><figcaption>`. Both backends accept `images_dir` param for resolving relative image paths.

---

### Tool library compiler — new + fixes (`curriculum/tool_library_compiler.py`)

New module. Builds `tool_library_reference.md` and `tool_index.json` from vault sources.

**Wrong-domain sources**: original vault contained Linux sysadmin, LaTeX, and IoT content due to bad seed packet from the topic name "tool_library". Fixed by wiping vault and manually pre-seeding knowledge map with 25 real workshop tool nodes before re-harvest.

**`_is_generic()` filter** — blocks junk tool names extracted by LLM:
- `_SKIP_EXACT`: raw materials, non-tools, verbs-as-nouns
- `_SKIP_VERBS`: single-word action verbs (`assemble`, `cut`, `drill`, …)
- `_NOT_A_TOOL_SUFFIXES`: abstract noun endings (`-ing`, `-ment`, `-tion`, …)
- `_MATERIALS`: pure material words (`iron`, `copper`, `stone`, …)
- `_SOFTWARE_WORDS`: computer/software vocabulary (`compiler`, `kernel`, `daemon`, `emacs`, `gcc`, `fsck`, `bash`, `ispell`, …)
- `_NON_WORKSHOP`: specific non-workshop items (`gps`, `landsat`, `space shuttle`, …)
- Parenthetical acronym expansions rejected: `if "(" in name and ")" in name`

**`_normalize_plurals()`**: collapses obvious plural duplicates (`dividers` → `divider`, `chisels` → `chisel`) after extraction.

**Consecutive-failure shutdown**: compile loop tracks `consecutive_fails`. After 8 straight empty LLM responses (Ollama down/overloaded), raises `RuntimeError` instead of burning through remaining entries at 40s/timeout each.

**Two images per tool**: primary overview image + `"using {tool} woodworking"` in-use image. Both embedded in reference markdown around the entry text.

---

### Ollama client — reliability (`llm/ollama_client.py`)

`CONNECT_TIMEOUT = 10` separated from read timeout. `requests.post(..., timeout=(CONNECT_TIMEOUT, timeout))` — connect failures now fast-fail in 10s instead of waiting the full read timeout.

429/503 responses handled with backoff: `time.sleep(15 * (attempt + 1))`.

`ConnectTimeout` handled separately with its own backoff.

---

### Results

| Topic | Output | Notes |
|-------|--------|-------|
| carpentry | 12 chapters, ~11k words, PDF, 5 images | Heading fixes applied, image content gate in place |
| tool_library | 187 tool entries, reference.md + tool_index.json | Software/junk filter working, images embedded |

