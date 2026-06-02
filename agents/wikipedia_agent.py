import sys
import os
import time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent, HEADERS

API = "https://en.wikipedia.org/w/api.php"
SLEEP = 0.5


class WikipediaAgent(BaseSourceAgent):
    source_name = "wikipedia"
    priority = 1
    min_hits = 2
    apply_html_filter = False
    fetch_sleep = SLEEP
    tier_affinity = {"foundational", "practical", "reference"}

    def search(self, node: str, topic: str, lexicon: list) -> list:
        node = node.replace("_", " ")
        topic = topic.replace("_", " ")
        queries = list(dict.fromkeys(filter(None, [
            f"{topic} {node}",
            node,
            topic,
        ])))
        candidates = []
        seen = set()
        for query in queries:
            try:
                r = requests.get(API, params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": 15,
                    "srnamespace": 0,
                    "format": "json",
                    "utf8": 1,
                }, headers=HEADERS, timeout=15)
                if r.status_code != 200:
                    continue
                for item in r.json().get("query", {}).get("search", []):
                    pid = str(item["pageid"])
                    if pid in seen:
                        continue
                    seen.add(pid)
                    candidates.append({
                        "identifier": f"wikipedia_{pid}",
                        "title": item["title"],
                        "pageid": pid,
                        "source": self.source_name,
                    })
                time.sleep(SLEEP)
            except Exception:
                continue
        return self._cap_candidates(candidates)

    def fetch_text(self, candidate: dict) -> str:
        pid = candidate.get("pageid")
        if not pid:
            return ""
        try:
            r = requests.get(API, params={
                "action": "query",
                "prop": "extracts",
                "explaintext": True,
                "pageids": pid,
                "format": "json",
                "utf8": 1,
            }, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                return ""
            pages = r.json().get("query", {}).get("pages", {})
            return pages.get(str(pid), {}).get("extract", "")
        except Exception:
            return ""
