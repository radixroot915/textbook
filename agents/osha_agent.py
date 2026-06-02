"""OSHATechManualAgent — searches the OSHA Technical Manual + safety
content via DuckDuckGo site-restricted queries, then fetches HTML or PDF.

OSHA content is US federal public domain — no license restrictions. The
Technical Manual covers industrial hygiene, welding, machinery, electrical
work, chemicals — exactly the kind of safety + procedural detail that
craft/trade textbooks need for the safety chapters.
"""
import sys
import os
import time
import logging
import urllib.parse
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent, HEADERS, _extract_pdf_text

log = logging.getLogger(__name__)

DDG = "https://html.duckduckgo.com/html/"
OSHA_DOMAINS = ("osha.gov", "cdc.gov/niosh")
SLEEP = 1.5


class OSHATechManualAgent(BaseSourceAgent):
    source_name = "osha"
    priority = 2
    min_hits = 1
    apply_html_filter = True
    fetch_sleep = SLEEP
    min_text_length = 1500
    tier_affinity = {"safety", "specialized", "reference"}

    def search(self, node: str, topic: str, lexicon: list) -> list:
        node = node.replace("_", " ")
        topic = topic.replace("_", " ")
        queries = [
            f"site:osha.gov {topic} {node}",
            f"site:osha.gov technical manual {node}",
            f"site:cdc.gov/niosh {topic} {node}",
        ]
        candidates = []
        seen: set = set()
        for query in queries:
            try:
                r = requests.post(
                    DDG,
                    data={"q": query, "kl": "us-en"},
                    headers={**HEADERS,
                             "Content-Type": "application/x-www-form-urlencoded",
                             "Referer": "https://duckduckgo.com/"},
                    timeout=20,
                )
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.select("a.result__a"):
                    href = a.get("href", "")
                    url = _extract_url(href)
                    if not url:
                        continue
                    domain = urllib.parse.urlparse(url).netloc.lower()
                    if not any(d in domain for d in OSHA_DOMAINS):
                        continue
                    if url in seen:
                        continue
                    seen.add(url)
                    candidates.append({
                        "identifier": f"osha_{url[-40:]}",
                        "title": a.get_text(strip=True),
                        "url": url,
                        "source": self.source_name,
                    })
                time.sleep(SLEEP)
            except Exception as e:
                log.debug(f"[osha] search error: {e}")
                continue
            if len(candidates) >= 20:
                break
        return self._cap_candidates(candidates)

    def fetch_text(self, candidate: dict) -> str:
        url = candidate.get("url", "")
        if not url:
            return ""
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                return ""
            ct = r.headers.get("Content-Type", "").lower()
            if url.lower().endswith(".pdf") or "application/pdf" in ct:
                return _extract_pdf_text(r.content)
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)
        except Exception as e:
            log.debug(f"[osha] fetch error {url[-60:]}: {e}")
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
