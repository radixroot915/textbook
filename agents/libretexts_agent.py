"""
LibreTexts Workforce agent — open trades & vocational textbooks.

LibreTexts hosts 13 libraries; the Workforce library covers:
  welding, HVAC, carpentry, plumbing, electrical, automotive, etc.

API:  MediaWiki API at workforce.libretexts.org
      - action=query&list=search to find pages
      - action=query&prop=revisions&rvprop=content to get wikitext
      - action=parse&prop=wikitext|text for rendered HTML

Full content is CC-licensed — no auth needed.
"""

import re
import sys
import os
import time
import logging
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent
from config import MAX_CANDIDATES

log = logging.getLogger(__name__)

LIBRETEXTS_LIBRARIES = [
    "workforce",
    "trades",
]

# MediaWiki API endpoint for the Workforce library
MW_API = "https://workforce.libretexts.org/api.php"
# Also check eng and chem for technical overlap
EXTRA_APIS = [
    "https://eng.libretexts.org/api.php",
]

SLEEP_BETWEEN = 0.8


class LibreTextsAgent(BaseSourceAgent):
    source_name = "libretexts"
    tier_affinity = {"foundational", "theoretical", "reference"}
    min_hits = 2
    min_text_length = 1500

    def search(self, node: str, topic: str, lexicon: list) -> list:
        candidates = []
        seen_ids = set()

        queries = self._build_queries(node, topic)
        apis = [MW_API] + EXTRA_APIS

        for api in apis:
            for query in queries:
                if len(candidates) >= MAX_CANDIDATES:
                    break
                try:
                    params = {
                        "action": "query",
                        "list": "search",
                        "srsearch": query,
                        "srlimit": 20,
                        "srnamespace": 0,
                        "format": "json",
                    }
                    resp = requests.get(
                        api, params=params, timeout=20,
                        headers={"User-Agent": "harvester-bot/1.0"}
                    )
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    results = data.get("query", {}).get("search", [])
                    for item in results:
                        if len(candidates) >= MAX_CANDIDATES:
                            break
                        page_id = str(item.get("pageid", ""))
                        uid = f"{api}:{page_id}"
                        if uid in seen_ids:
                            continue
                        seen_ids.add(uid)
                        title = item.get("title", "")
                        base = api.replace("/api.php", "")
                        url = f"{base}/index.php?curid={page_id}"
                        candidates.append({
                            "identifier": uid,
                            "page_id": page_id,
                            "api": api,
                            "title": title,
                            "url": url,
                            "source": self.source_name,
                        })
                except Exception as e:
                    log.debug(f"[libretexts] search error for '{query}' at {api}: {e}")
                time.sleep(SLEEP_BETWEEN)

        return candidates

    def fetch_text(self, candidate: dict) -> str:
        api = candidate.get("api", MW_API)
        page_id = candidate.get("page_id", "")
        if not page_id:
            return ""

        try:
            # Fetch rendered HTML then strip tags — cleaner than raw wikitext
            params = {
                "action": "parse",
                "pageid": page_id,
                "prop": "text",
                "disablelimitreport": 1,
                "format": "json",
            }
            resp = requests.get(
                api, params=params, timeout=20,
                headers={"User-Agent": "harvester-bot/1.0"}
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
            html = data.get("parse", {}).get("text", {}).get("*", "")
            if not html:
                return ""

            # Strip HTML tags
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'&[a-z]+;', ' ', text)
            text = re.sub(r'\s{3,}', '\n\n', text)
            title = candidate.get("title", "")
            return f"{title}\n\n{text.strip()}"
        except Exception as e:
            log.debug(f"[libretexts] fetch error {page_id}: {e}")
            return ""

    def _build_queries(self, node: str, topic: str) -> list:
        queries = []
        node_clean = re.sub(r'[^\w\s]', ' ', node).strip()
        topic_clean = re.sub(r'[^\w\s]', ' ', topic.replace('_', ' ')).strip()

        queries.append(node_clean)
        if topic_clean.lower() not in node_clean.lower():
            queries.append(f"{topic_clean} {node_clean}")
        queries.append(topic_clean)
        return queries
