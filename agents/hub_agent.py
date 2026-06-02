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

_SLICE = 10


class HubAgent(BaseSourceAgent):
    """Generic seed-URL hub agent.

    Fetches one hub/index page (e.g. an awesome-list, wiki, or curated
    directory), extracts all outbound links, filters them by topic keywords,
    and feeds them into the existing pipeline as candidates.

    One instance per seed URL. Link pool is built once on first search() call
    then consumed slice-by-slice so each node search gets fresh candidates.
    """

    source_name = "hub"
    priority = 2
    min_hits = 1
    apply_html_filter = True
    fetch_sleep = 1.0
    min_text_length = 800
    tier_affinity = {"foundational", "practical"}

    def __init__(self, seed_url: str, topic_keywords: list[str] | None = None):
        self.seed_url = seed_url
        self.topic_keywords = [kw.lower() for kw in (topic_keywords or [])]
        self._pool: list[dict] = []
        self._pool_index: int = 0
        self._pool_loaded: bool = False
        seed_domain = urllib.parse.urlparse(seed_url).netloc
        self.source_name = f"hub:{seed_domain}"

    # ------------------------------------------------------------------
    # BaseSourceAgent interface

    def search(self, node: str, topic: str, lexicon: list) -> list:
        if not self._pool_loaded:
            self._build_pool(topic, lexicon)

        if self._pool_index >= len(self._pool):
            return []

        batch = self._pool[self._pool_index: self._pool_index + _SLICE]
        self._pool_index += _SLICE
        log.info(
            f"  [{self.source_name}] yielding links "
            f"{self._pool_index - _SLICE}–{self._pool_index} of {len(self._pool)}"
        )
        return batch

    def fetch_text(self, candidate: dict) -> str:
        url = candidate.get("url", "")
        if not url:
            return ""
        try:
            time.sleep(self.fetch_sleep)
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.status_code != 200:
                log.debug(f"[{self.source_name}] HTTP {r.status_code} — {url[-60:]}")
                return ""
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)
        except Exception as e:
            log.debug(f"[{self.source_name}] fetch error {url[-60:]}: {e}")
            return ""

    # ------------------------------------------------------------------

    def _build_pool(self, topic: str, lexicon: list):
        self._pool_loaded = True
        log.info(f"  [{self.source_name}] Fetching seed: {self.seed_url}")
        try:
            r = requests.get(self.seed_url, headers=HEADERS, timeout=25)
            if r.status_code != 200:
                log.warning(f"  [{self.source_name}] seed fetch HTTP {r.status_code}")
                return
        except Exception as e:
            log.warning(f"  [{self.source_name}] seed fetch error: {e}")
            return

        soup = BeautifulSoup(r.text, "html.parser")
        seed_netloc = urllib.parse.urlparse(self.seed_url).netloc

        topic_words = [w.lower() for w in topic.replace("_", " ").split() if len(w) > 3]
        filter_kws = list(dict.fromkeys(
            self.topic_keywords + topic_words + [kw.lower() for kw in lexicon[:20]]
        ))

        seen: set[str] = set()
        candidates: list[dict] = []

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            url = urllib.parse.urljoin(self.seed_url, href)
            parsed = urllib.parse.urlparse(url)

            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc == seed_netloc and not parsed.path.rstrip("/"):
                continue
            if url in seen:
                continue
            seen.add(url)

            anchor = a.get_text(strip=True).lower()
            slug = (parsed.path + " " + parsed.query).lower()
            combined = anchor + " " + slug

            if filter_kws and not any(kw in combined for kw in filter_kws):
                continue

            title = a.get_text(strip=True) or parsed.path.split("/")[-1] or url
            candidates.append({
                "identifier": f"hub_{url[-40:]}",
                "title": title,
                "url": url,
                "source": self.source_name,
            })

        log.info(
            f"  [{self.source_name}] {len(candidates)} filtered links "
            f"(from {len(seen)} total outbound)"
        )
        self._pool = candidates
