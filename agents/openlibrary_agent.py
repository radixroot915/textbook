import sys
import os
import time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent, HEADERS

OL_SEARCH = "https://openlibrary.org/search.json"
SLEEP = 1.0


class OpenLibraryAgent(BaseSourceAgent):
    source_name = "openlibrary"
    priority = 2
    min_hits = 2
    apply_html_filter = True
    fetch_sleep = SLEEP

    def search(self, node: str, topic: str, lexicon: list) -> list:
        node = node.replace('_', ' ')
        topic = topic.replace('_', ' ')
        # Build broader subject search terms — OL subject search uses LoC headings
        topic_words = [w for w in topic.split() if len(w) > 3]
        broad_topic = " ".join(topic_words[:2]) if topic_words else topic
        candidates = []
        seen = set()
        for term in list(dict.fromkeys([node, topic, broad_topic])):
            try:
                r = requests.get(
                    OL_SEARCH,
                    params={"subject": term, "limit": 50, "fields": "key,title,ia,public_scan_b"},
                    headers=HEADERS,
                    timeout=20
                )
                if r.status_code != 200:
                    continue
                for doc in r.json().get("docs", []):
                    if not doc.get("public_scan_b"):
                        continue
                    ia = doc.get("ia")
                    if not ia:
                        continue
                    # ia can be a list or string
                    if isinstance(ia, list):
                        ia = ia[0]
                    if ia in seen:
                        continue
                    seen.add(ia)
                    candidates.append({
                        "identifier": ia,
                        "title": doc.get("title", ""),
                        "url": f"https://archive.org/download/{ia}/{ia}_djvu.txt",
                        "source": self.source_name
                    })
                time.sleep(SLEEP)
            except Exception:
                continue
        return self._cap_candidates(candidates)

    def fetch_text(self, candidate: dict) -> str:
        return self._fetch_archive_text(candidate["identifier"])
