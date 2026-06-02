import sys
import os
import time
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent, HEADERS
from config import MAX_CANDIDATES

SE_API = "https://api.stackexchange.com/2.3"
SLEEP = 1.0

# Keyword → Stack Exchange site(s) mapping. First match wins; fallback to _SE_DEFAULT.
_SE_SITE_MAP = [
    (["weld", "metal", "forge", "machin", "lathe", "mill", "cast", "fabricat"],
     ["engineering", "diy", "crafts"]),
    (["wood", "carpent", "cabinet", "furniture", "join", "timber"],
     ["woodworking", "diy", "crafts"]),
    (["leather", "sew", "textile", "fabric", "knit", "crochet", "embroid"],
     ["crafts", "diy"]),
    (["electron", "circuit", "arduino", "raspberry", "microcontrol", "solder", "pcb"],
     ["electronics", "electrical-engineering", "diy"]),
    (["cook", "bak", "food", "recipe", "bread", "brew", "ferment"],
     ["cooking", "homebrewing"]),
    (["garden", "plant", "soil", "grow", "horticultur"],
     ["gardening", "outdoors"]),
    (["bike", "cycl", "bicycle"],
     ["bicycles", "outdoors"]),
    (["plumb", "hvac", "roofing", "concrete", "masonry", "tile"],
     ["home-improvement", "diy"]),
    (["outdoor", "camp", "hike", "hunt", "fish", "surviv"],
     ["outdoors", "diy"]),
    (["photo", "camera", "lens", "darkroom"],
     ["photo", "diy"]),
    (["print", "3d print", "laser", "cnc"],
     ["3dprinting", "engineering", "diy"]),
]
_SE_DEFAULT = ["crafts", "diy", "engineering", "home-improvement"]


def _pick_sites(topic: str) -> list:
    tl = topic.lower()
    for keywords, sites in _SE_SITE_MAP:
        if any(kw in tl for kw in keywords):
            return sites
    return _SE_DEFAULT


class StackExchangeAgent(BaseSourceAgent):
    source_name = "stackexchange"
    priority = 2
    min_hits = 2
    apply_html_filter = False
    fetch_sleep = SLEEP
    min_text_length = 800
    tier_affinity = {"practical"}

    def search(self, node: str, topic: str, lexicon: list) -> list:
        node = node.replace('_', ' ')
        topic = topic.replace('_', ' ')
        topic_words = [w for w in topic.split() if len(w) > 3]
        node_words = [w for w in node.split() if len(w) > 3]
        # Short, broad queries — SE full-text search is strict about long phrases
        queries = list(dict.fromkeys(filter(None, [
            " ".join(topic_words[:2]),
            " ".join(node_words[:2]) if node_words else None,
        ])))

        candidates = []
        seen = set()
        quota_exhausted = False
        sites = _pick_sites(topic)

        for site in sites:
            if quota_exhausted:
                break
            for query in queries:
                try:
                    r = requests.get(f"{SE_API}/search/advanced", params={
                        "q": query,
                        "site": site,
                        "sort": "votes",
                        "order": "desc",
                        "pagesize": 20,
                        "filter": "withbody",
                    }, timeout=15)
                    if r.status_code != 200:
                        continue
                    data = r.json()

                    for item in data.get("items", []):
                        if item.get("score", 0) < 1:
                            continue
                        qid = str(item.get("question_id", ""))
                        key = f"{site}_{qid}"
                        if key in seen:
                            continue
                        seen.add(key)

                        body_html = item.get("body", "")
                        body_text = BeautifulSoup(body_html, "html.parser").get_text(separator="\n") if body_html else ""

                        candidates.append({
                            "identifier": f"se_{key}",
                            "title": item.get("title", ""),
                            "question_id": qid,
                            "site": site,
                            "source": self.source_name,
                            "_question_text": body_text,
                        })

                    time.sleep(SLEEP)
                    if data.get("quota_remaining", 999) < 10:
                        quota_exhausted = True
                        break
                except Exception:
                    continue

        return self._cap_candidates(candidates)

    def fetch_text(self, candidate: dict) -> str:
        qid = candidate.get("question_id")
        site = candidate.get("site", "crafts")
        if not qid:
            return ""

        parts = [candidate.get("title", ""), candidate.get("_question_text", "")]

        try:
            r = requests.get(f"{SE_API}/questions/{qid}/answers", params={
                "site": site,
                "sort": "votes",
                "order": "desc",
                "pagesize": 5,
                "filter": "withbody",
            }, timeout=15)
            if r.status_code == 200:
                for answer in r.json().get("items", [])[:3]:
                    if answer.get("score", 0) >= 1:
                        body_html = answer.get("body", "")
                        text = BeautifulSoup(body_html, "html.parser").get_text(separator="\n")
                        parts.append(text.strip())
        except Exception:
            pass

        return "\n\n".join(p for p in parts if p)
