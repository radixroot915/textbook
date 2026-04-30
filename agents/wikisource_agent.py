import sys
import os
import time
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent, HEADERS

SLEEP = 0.5


class WikiSourceAgent(BaseSourceAgent):
    source_name = "wikisource"
    api_url = "https://en.wikisource.org/w/api.php"
    priority = 3
    min_hits = 2
    apply_html_filter = False
    fetch_sleep = SLEEP

    def search(self, node: str, topic: str, lexicon: list) -> list:
        node = node.replace('_', ' ')
        topic = topic.replace('_', ' ')
        candidates = []
        seen = set()
        node_words = [w for w in node.split() if w.lower() not in {"and","the","of","a","an","for","in","with","to"}]
        combined = f"{topic} {' '.join(node_words[:3])}".strip()
        for term in [combined, " ".join(node_words[:2])] if node_words else [topic]:
            try:
                r = requests.get(self.api_url, params={
                    "action": "query", "list": "search",
                    "srsearch": term, "srnamespace": "0",
                    "srlimit": 20, "format": "json"
                }, headers=HEADERS, timeout=15)
                if r.status_code != 200:
                    continue
                for result in r.json().get("query", {}).get("search", []):
                    pid = str(result.get("pageid", ""))
                    title = result.get("title", "")
                    if pid in seen:
                        continue
                    seen.add(pid)
                    candidates.append({
                        "identifier": f"{self.source_name}_{pid}",
                        "title": title,
                        "pageid": pid,
                        "source": self.source_name
                    })
                time.sleep(SLEEP)
            except Exception:
                continue
        return self._cap_candidates(candidates)

    def fetch_text(self, candidate: dict) -> str:
        pageid = candidate.get("pageid")
        if not pageid:
            return ""
        try:
            r = requests.get(self.api_url, params={
                "action": "parse", "pageid": pageid,
                "prop": "text", "format": "json",
                "disableeditsection": "1"
            }, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                return ""
            html = r.json().get("parse", {}).get("text", {}).get("*", "")
            return self._clean_html(html)
        except Exception:
            return ""

    def _clean_html(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        # Remove noise elements before extracting text
        for tag in soup.select("table, script, style, header, footer, nav, .sister-wikipedia, .noprint"):
            tag.decompose()
        content = soup.select_one("#mw-content-text") or soup.body or soup
        return content.get_text(separator="\n").strip()
