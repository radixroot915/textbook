import sys
import os
import time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent, HEADERS

GUTENDEX = "https://gutendex.com/books"
SLEEP = 0.5


class GutenbergAgent(BaseSourceAgent):
    source_name = "gutenberg"
    priority = 1
    min_hits = 3
    apply_html_filter = False
    fetch_sleep = SLEEP

    def search(self, node: str, topic: str, lexicon: list) -> list:
        node = node.replace('_', ' ')
        topic = topic.replace('_', ' ')
        candidates = []
        seen = set()
        # Gutenberg search is title/author level — use broad topic keywords
        # not specific node phrases which never appear in pre-1928 book titles
        topic_words = [w for w in topic.split() if len(w) > 3]
        node_suffix = node
        if node.lower().startswith(topic.lower()):
            node_suffix = node[len(topic):].strip()
        search_terms = list(dict.fromkeys(filter(None, [
            topic,
            " ".join(topic_words[:2]) if len(topic_words) > 1 else topic,
            node_suffix if node_suffix != node else None,
        ])))
        for term in search_terms:
            url = GUTENDEX
            params = {"search": term, "languages": "en"}
            pages = 0
            while url and pages < 3:
                try:
                    r = requests.get(url, params=params if pages == 0 else None,
                                     headers=HEADERS, timeout=20)
                    if r.status_code != 200:
                        break
                    data = r.json()
                    for book in data.get("results", []):
                        bid = str(book.get("id", ""))
                        if bid in seen:
                            continue
                        text_url = self._find_text_url(book.get("formats", {}))
                        if not text_url:
                            continue
                        seen.add(bid)
                        candidates.append({
                            "identifier": f"gutenberg_{bid}",
                            "title": book.get("title", ""),
                            "url": text_url,
                            "source": self.source_name
                        })
                    url = data.get("next")
                    pages += 1
                    time.sleep(SLEEP)
                except Exception:
                    break
        return self._cap_candidates(candidates)

    def fetch_text(self, candidate: dict) -> str:
        return self._fetch_raw(candidate["url"], timeout=60)

    def _find_text_url(self, formats: dict) -> str:
        for key in ["text/plain; charset=utf-8", "text/plain; charset=us-ascii", "text/plain"]:
            if key in formats:
                return formats[key]
        return ""
