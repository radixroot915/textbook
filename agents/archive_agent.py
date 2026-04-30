import sys
import os
import time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent
from config import MAX_CANDIDATES

SEARCH_MAX = 150
SLEEP = 1.0
COLLECTIONS = ["dtic", "army", "navy", "technical manual"]


class ArchiveAgent(BaseSourceAgent):
    source_name = "archive_org"
    priority = 4
    min_hits = 2
    apply_html_filter = True
    fetch_sleep = 2.0

    def search(self, node: str, topic: str, lexicon: list) -> list:
        node = node.replace('_', ' ')
        topic = topic.replace('_', ' ')
        queries = self._static_queries(node, topic)
        seen = set()
        candidates = []
        for query in queries:
            docs = self._search_api(query)
            for doc in docs:
                iid = doc.get("identifier")
                if not iid or iid in seen:
                    continue
                seen.add(iid)
                candidates.append({
                    "identifier": iid,
                    "title": doc.get("title", ""),
                    "url": f"https://archive.org/download/{iid}/{iid}_djvu.txt",
                    "source": self.source_name
                })
                if len(candidates) >= MAX_CANDIDATES:
                    return self._cap_candidates(candidates)
            time.sleep(SLEEP)
        return self._cap_candidates(candidates)

    def fetch_text(self, candidate: dict) -> str:
        return self._fetch_archive_text(candidate["identifier"])

    def _static_queries(self, node: str, topic: str) -> list:
        topic_words = [w for w in topic.split() if len(w) > 3]
        primary = " ".join(topic_words[:2]) if topic_words else topic
        base = [
            f'text:("{node}") AND mediatype:texts',
            f'text:("{primary}") AND text:("{node}") AND mediatype:texts',
            f'subject:("{primary}") AND mediatype:texts',
            f'title:("{primary}") AND mediatype:texts',
            f'text:("{primary}") AND text:("manual") AND mediatype:texts',
        ]
        return base + [f'collection:({c}) AND text:("{node}" OR "{primary}")' for c in COLLECTIONS]

    def _search_api(self, query: str) -> list:
        import logging
        log = logging.getLogger(__name__)
        try:
            r = requests.get(
                "https://archive.org/advancedsearch.php",
                params={"q": query, "fl[]": "identifier,title,collection", "rows": SEARCH_MAX, "output": "json"},
                timeout=30
            )
            if r.status_code == 200:
                return r.json().get("response", {}).get("docs", [])
            log.warning(f"archive.org search returned status {r.status_code}")
        except Exception as e:
            log.warning(f"archive.org search error: {e}")
        return []
