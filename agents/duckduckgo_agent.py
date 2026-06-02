import sys
import os
import time
import logging
import urllib.parse
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent, HEADERS

log = logging.getLogger(__name__)

DDG_URL = "https://html.duckduckgo.com/html/"
SLEEP = 1.5

# Domains handled by dedicated agents or not useful for text extraction
_SKIP_DOMAINS = frozenset({
    "youtube.com", "youtu.be",
    "amazon.com", "amazon.co.uk",
    "reddit.com",
    "pinterest.com",
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "ebay.com", "etsy.com",
    "wikipedia.org",  # dedicated agent
})


class DuckDuckGoAgent(BaseSourceAgent):
    source_name = "duckduckgo"
    priority = 3
    min_hits = 2
    apply_html_filter = True
    fetch_sleep = SLEEP
    min_text_length = 1500
    tier_affinity = {"foundational", "practical"}

    def search(self, node: str, topic: str, lexicon: list) -> list:
        node = node.replace("_", " ")
        topic = topic.replace("_", " ")
        queries = [
            f"{topic} {node} tutorial guide",
            f"{topic} {node} techniques manual",
            f'"{topic}" {node} instructions how-to',
        ]
        candidates = []
        seen_urls: set = set()
        for query in queries:
            try:
                r = requests.post(
                    DDG_URL,
                    data={"q": query, "kl": "us-en"},
                    headers={
                        **HEADERS,
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": "https://duckduckgo.com/",
                    },
                    timeout=20,
                )
                if r.status_code != 200:
                    log.debug(f"[duckduckgo] HTTP {r.status_code} for query: {query[:60]}")
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.select("a.result__a"):
                    href = a.get("href", "")
                    url = _extract_url(href)
                    if not url or url in seen_urls:
                        continue
                    domain = urllib.parse.urlparse(url).netloc.lstrip("www.")
                    if any(skip in domain for skip in _SKIP_DOMAINS):
                        continue
                    seen_urls.add(url)
                    candidates.append({
                        "identifier": f"ddg_{url[-40:]}",
                        "title": a.get_text(strip=True),
                        "url": url,
                        "source": self.source_name,
                    })
                time.sleep(SLEEP)
            except Exception as e:
                log.debug(f"[duckduckgo] search error: {e}")
                continue
            if len(candidates) >= 30:
                break
        return self._cap_candidates(candidates)

    def fetch_text(self, candidate: dict) -> str:
        url = candidate.get("url", "")
        if not url:
            return ""
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.status_code != 200:
                return ""
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)
        except Exception as e:
            log.debug(f"[duckduckgo] fetch error {url[:60]}: {e}")
            return ""


def _extract_url(href: str) -> str:
    if not href:
        return ""
    if "uddg=" in href:
        try:
            return urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
        except Exception:
            pass
    if href.startswith("http"):
        return href
    return ""
