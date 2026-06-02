"""
Image fetcher — pulls CC-licensed reference images from Wikimedia Commons.

Strategy:
  - Run up to MAX_QUERIES different search queries per request
  - For each query, score ALL candidates (up to CANDIDATES_PER_QUERY)
  - Accept the highest-scoring candidate that clears MIN_SCORE
  - If nothing passes after all queries, cache None and move on

Scoring (per candidate file):
  +4  filename contains every word from the search term
  +2  filename contains the primary noun (longest word) of the search term
  +1  Commons description mentions the search term
  +2  image is a photograph (JPG/PNG, not SVG-only icon)
  +1  original image is large enough to be a real photo (>= MIN_ORIGINAL_PX wide)
  -4  filename contains noise words (logo, icon, flag, map, seal, emblem, coat, badge, portrait)
  -3  filename contains schema/schematic/diagram (technical diagrams ok, but deprioritised)
  -2  original image is tiny (< MIN_ORIGINAL_PX) — likely an icon or thumbnail
  -2  filename ends in .svg and contains "icon" or "logo"
"""

import os
import re
import json
import logging
import urllib.request
import urllib.parse

log = logging.getLogger(__name__)

COMMONS_API      = "https://commons.wikimedia.org/w/api.php"
CACHE_INDEX      = "image_cache.json"
MAX_IMAGE_WIDTH  = 800    # px for downloaded thumbnail
MIN_ORIGINAL_PX  = 300    # original image must be at least this wide
MIN_SCORE        = 3      # candidate must reach this score to be accepted
CANDIDATES_PER_QUERY = 8  # how many results to evaluate per query
MAX_QUERIES      = 5      # max query variations to try before giving up

ALLOWED_LICENSES = {"pd", "cc0", "cc-by", "cc-by-sa", "public domain"}

_NOISE_WORDS = {
    "logo", "icon", "flag", "map", "seal", "emblem", "coat", "badge",
    "portrait", "symbol", "sign", "sticker", "stamp", "button", "banner",
    "crest", "insignia", "watermark", "template", "placeholder",
}

# Words that should not anchor an image match — common adjectives/prepositions
# that appear in search terms but are too generic to distinguish a relevant image
_SCORE_STOP_WORDS = {
    "for", "the", "and", "with", "of", "in", "a", "an", "to", "by",
    "essential", "basic", "advanced", "general", "standard", "complete",
    "common", "simple", "typical", "traditional", "modern", "classic",
    "introduction", "overview", "guide", "handbook", "manual",
    "using", "how", "what", "when", "all", "new", "old", "good",
}


# ---------------------------------------------------------------------------
# Public API

def fetch_tool_image(term: str, cache_dir: str) -> str | None:
    """
    Find and download the best-matching CC image for `term`.
    Returns local file path or None. Results are cached permanently.
    """
    os.makedirs(cache_dir, exist_ok=True)
    index_path = os.path.join(cache_dir, CACHE_INDEX)
    cache = _load_cache(index_path)

    key = term.lower().strip()
    if key in cache:
        cached = cache[key]
        if cached and os.path.exists(cached):
            return cached
        if cached is None:
            return None  # searched before, nothing usable found

    log.info(f"[IMG] Searching: {term!r}")
    result = _search_best(term, cache_dir)

    cache[key] = result
    _save_cache(index_path, cache)
    if result:
        log.info(f"[IMG] -> {os.path.basename(result)}")
    else:
        log.debug(f"[IMG] -> no acceptable image found for {term!r}")
    return result


# ---------------------------------------------------------------------------
# Core search logic

def _search_best(term: str, cache_dir: str) -> str | None:
    """Try several query variations, score all candidates, return the best."""
    queries = _build_queries(term)
    best_path = None
    best_score = MIN_SCORE - 1  # must beat this

    for query in queries[:MAX_QUERIES]:
        candidates = _fetch_candidates(query, CANDIDATES_PER_QUERY)
        for candidate in candidates:
            score = _score_candidate(candidate, term)
            if score > best_score:
                # Try to download before committing — skip if download fails
                url = candidate.get("thumburl") or candidate.get("url")
                if not url:
                    continue
                path = _download_image(url, candidate["title"], cache_dir)
                if path:
                    best_score = score
                    best_path = path
                    log.debug(f"[IMG] New best (score={score}): {candidate['title']}")

    return best_path


def _build_queries(term: str) -> list[str]:
    """Generate query variations, biased toward technical diagrams and
    illustrations over photographs."""
    t = term.strip()
    words = t.split()
    primary = max(words, key=len) if words else t

    queries = [
        f"{t} diagram",
        f"{t} illustration",
        f"{primary} diagram filetype:svg",
        f"{t} schematic",
        t,                                    # bare term — fallback
        primary,                              # broadest fallback
    ]
    # Deduplicate while preserving order
    seen = set()
    result = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            result.append(q)
    return result


def _fetch_candidates(query: str, limit: int) -> list[dict]:
    """
    Search Commons for `query`, return candidate dicts enriched with
    imageinfo (url, size, metadata) for scoring.
    """
    # Step 1: text search for file titles
    search_params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": query,
        "srlimit": str(limit),
        "srnamespace": "6",
    }
    try:
        data = _api_get(COMMONS_API, search_params)
    except Exception as e:
        log.debug(f"[IMG] Search error for {query!r}: {e}")
        return []

    results = data.get("query", {}).get("search", [])
    titles = [
        r["title"] for r in results
        if re.search(r'\.(jpg|jpeg|png|svg)$', r.get("title", ""), re.IGNORECASE)
    ]
    if not titles:
        return []

    # Step 2: batch-fetch imageinfo for all titles
    info_params = {
        "action": "query",
        "format": "json",
        "titles": "|".join(titles[:limit]),
        "prop": "imageinfo",
        "iiprop": "url|size|extmetadata",
        "iiurlwidth": str(MAX_IMAGE_WIDTH),
    }
    try:
        info_data = _api_get(COMMONS_API, info_params)
    except Exception as e:
        log.debug(f"[IMG] Imageinfo error: {e}")
        return []

    candidates = []
    pages = info_data.get("query", {}).get("pages", {})
    for page in pages.values():
        title = page.get("title", "")
        info_list = page.get("imageinfo", [])
        if not info_list:
            continue
        info = info_list[0]

        # License gate — hard filter before scoring
        meta = info.get("extmetadata", {})
        license_short = meta.get("LicenseShortName", {}).get("value", "").lower()
        if not _license_ok(license_short):
            continue

        description = (
            meta.get("ImageDescription", {}).get("value", "") +
            meta.get("ObjectName", {}).get("value", "")
        ).lower()

        candidates.append({
            "title":       title,
            "url":         info.get("url", ""),
            "thumburl":    info.get("thumburl", ""),
            "width":       info.get("width", 0),
            "height":      info.get("height", 0),
            "description": description,
            "license":     license_short,
        })

    return candidates


# ---------------------------------------------------------------------------
# Scoring

def _score_candidate(c: dict, term: str) -> int:
    title_lower = c["title"].lower()
    fname = re.sub(r'^file:', '', title_lower)
    fname_words = set(re.findall(r'\b\w+\b', fname))
    term_words = set(
        w.lower() for w in term.split()
        if len(w) > 2 and w.lower() not in _SCORE_STOP_WORDS
    )
    primary = max(term_words, key=len) if term_words else ""

    score = 0
    content_score = 0   # points from content-relevance only (not photo/size bonuses)

    # --- Positive signals ---
    if term_words and term_words.issubset(fname_words):
        content_score += 4   # every term word in filename
    elif primary and primary in fname:
        content_score += 2   # at least the main noun matches

    if term.lower() in c["description"]:
        content_score += 1   # description explicitly mentions the search term

    score = content_score

    # --- Bias toward diagrams and illustrations ---
    if fname.endswith('.svg'):
        score += 3   # SVG diagrams are ideal — scalable, line-art
    elif re.search(r'(diagram|illustration|drawing|cross.section|schematic|cutaway|exploded)', fname):
        score += 3
    elif "diagram" in c["description"] or "illustration" in c["description"]:
        score += 2

    if c["width"] >= MIN_ORIGINAL_PX:
        score += 1

    # Hard gate: no content match = reject
    if content_score == 0:
        return -99

    # --- Negative signals ---
    noise_in_fname = fname_words & _NOISE_WORDS
    if noise_in_fname:
        score -= 4

    # Heavy bias against photographic content when a diagram is available
    if re.search(r'\.(jpe?g)$', fname):
        score -= 2

    if any(w in fname for w in ("photograph", "photo")):
        score -= 2

    if fname.endswith('.svg') and any(w in fname for w in ("icon", "logo")):
        score -= 3

    if c["width"] < MIN_ORIGINAL_PX and c["width"] > 0:
        score -= 2

    return score


# ---------------------------------------------------------------------------
# Helpers

def _license_ok(s: str) -> bool:
    if not s:
        return False
    return any(a in s for a in ALLOWED_LICENSES)


def _download_image(url: str, file_title: str, cache_dir: str) -> str | None:
    safe = re.sub(r'[^\w.-]', '_', file_title.replace("File:", ""))
    if not re.search(r'\.(jpg|jpeg|png|svg)$', safe, re.IGNORECASE):
        safe += ".jpg"
    local = os.path.join(cache_dir, safe)
    if os.path.exists(local):
        return local
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HarvesterBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            with open(local, "wb") as f:
                f.write(resp.read())
        return local
    except Exception as e:
        log.debug(f"[IMG] Download failed {url}: {e}")
        return None


def _api_get(url: str, params: dict) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": "HarvesterBot/1.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _load_cache(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(path: str, cache: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
