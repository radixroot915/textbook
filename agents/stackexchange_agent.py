import sys
import os
import time
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent, HEADERS

SE_API = "https://api.stackexchange.com/2.3"
SITES = ["crafts", "diy", "woodworking"]
SLEEP = 1.0


class StackExchangeAgent(BaseSourceAgent):
    source_name = "stackexchange"
    priority = 2
    min_hits = 2
    apply_html_filter = False
    fetch_sleep = SLEEP
    min_text_length = 800

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

        for site in SITES:
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
