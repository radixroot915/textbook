"""CitedURLAgent — fetches URLs found in already-collected vault documents.

Compounds the corpus across cycles: every saved file is mined for outbound
URLs (bibliographies, "See also" links, inline citations). Those URLs go
into `cited_urls.json` keyed by topic and are served back as candidates
in the next cycle.

Filters out social media, search engines, and other low-value domains.
"""

import sys
import os
import re
import json
import time
import logging
import urllib.parse
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent, HEADERS, _extract_pdf_text
from config import BASE_DIR, VAULT_ROOT

log = logging.getLogger(__name__)

_CITED_URLS_PATH = os.path.join(BASE_DIR, "cited_urls.json")
_URL_PAT = re.compile(r'https?://[^\s\]\)\>\"\'\\]+', re.IGNORECASE)

_SKIP_DOMAINS = frozenset({
    "youtube.com", "youtu.be",
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "tiktok.com", "pinterest.com",
    "amazon.com", "amazon.co.uk", "ebay.com", "etsy.com",
    "google.com", "bing.com", "duckduckgo.com",
    "wikipedia.org",            # dedicated agent
    "archive.org",              # dedicated agent
    "gutenberg.org",            # dedicated agent
    "reddit.com",               # dedicated agent
    "github.com",               # often hub-targeted separately
    "doi.org",                  # paywalled DOIs aren't accessible directly
    "stackexchange.com", "stackoverflow.com",  # dedicated agent
})

_VALID_TLD_RE = re.compile(r'\.(com|org|net|edu|gov|int|info|io|co|uk|ca)(/|$|\?|#)', re.IGNORECASE)


def _load_cited(topic: str) -> list[dict]:
    try:
        with open(_CITED_URLS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get(topic, [])
    except Exception:
        return []


def _save_cited(topic: str, entries: list[dict]):
    try:
        try:
            with open(_CITED_URLS_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        data[topic] = entries
        with open(_CITED_URLS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning(f"[cited] save error: {e}")


def harvest_citations_from_vault(topic: str) -> int:
    """Scan vault/<topic>/*.txt for outbound URLs, store unique entries.
    Returns count of NEW URLs added.
    """
    vault = os.path.join(VAULT_ROOT, topic)
    if not os.path.exists(vault):
        return 0

    existing = _load_cited(topic)
    seen_urls = {e["url"] for e in existing}
    new_urls: list[dict] = []

    for fname in os.listdir(vault):
        if not fname.endswith(".txt"):
            continue
        try:
            with open(os.path.join(vault, fname), encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception:
            continue

        for m in _URL_PAT.finditer(text):
            url = m.group(0).rstrip('.,;:').rstrip(')]')
            if url in seen_urls:
                continue
            if len(url) < 12 or len(url) > 500:
                continue
            if not _VALID_TLD_RE.search(url):
                continue
            domain = urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
            if any(skip in domain for skip in _SKIP_DOMAINS):
                continue
            seen_urls.add(url)
            new_urls.append({
                "url": url,
                "source_file": fname,
                "domain": domain,
                "consumed": False,
            })

    if new_urls:
        existing.extend(new_urls)
        _save_cited(topic, existing)
        log.info(f"[cited] {len(new_urls)} new URLs harvested for {topic} "
                 f"(total queued: {len(existing)})")
    return len(new_urls)


class CitedURLAgent(BaseSourceAgent):
    source_name = "cited"
    priority = 1
    min_hits = 1
    apply_html_filter = True
    fetch_sleep = 1.0
    min_text_length = 1200

    def search(self, node: str, topic: str, lexicon: list) -> list:
        """Return up to N un-consumed cited URLs as candidates. Doesn't
        actually filter on the node — citation URLs are pre-screened by
        having appeared in already-relevant docs.
        """
        all_cited = _load_cited(topic)
        unconsumed = [e for e in all_cited if not e.get("consumed")]
        # Take up to 5 per search call so they spread across nodes
        batch = unconsumed[:5]

        if not batch:
            return []

        # Mark consumed so subsequent search() calls get different URLs
        urls_taken = {e["url"] for e in batch}
        for e in all_cited:
            if e["url"] in urls_taken:
                e["consumed"] = True
        _save_cited(topic, all_cited)

        candidates = []
        for e in batch:
            url = e["url"]
            candidates.append({
                "identifier": f"cited_{url[-40:]}",
                "title": f"[cited from {e.get('source_file', '?')[:30]}] {e.get('domain', url)[:60]}",
                "url": url,
                "source": self.source_name,
            })
        return candidates

    def fetch_text(self, candidate: dict) -> str:
        url = candidate.get("url", "")
        if not url:
            return ""
        try:
            time.sleep(self.fetch_sleep)
            r = requests.get(url, headers=HEADERS, timeout=25,
                             allow_redirects=True)
            if r.status_code != 200:
                return ""
            ct = r.headers.get("Content-Type", "").lower()
            if url.lower().endswith(".pdf") or "application/pdf" in ct:
                return _extract_pdf_text(r.content)
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer",
                             "header", "aside", "form"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)
        except Exception as e:
            log.debug(f"[cited] fetch error {url[-50:]}: {e}")
            return ""
