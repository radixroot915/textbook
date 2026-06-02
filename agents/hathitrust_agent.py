"""
HathiTrust agent — public-domain technical books via HathiTrust Data API.

Search:  Catalog Bibliographic API  https://catalog.hathitrust.org/api/volumes/
Fetch:   Data API (page OCR text)   https://babel.hathitrust.org/cgi/htd/
Access:  Full-view public domain only — no auth required.

Volume text is fetched as concatenated page OCR.  Pages are requested in
batches to stay under timeout; the agent stops when it has enough text.
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

CATALOG_API   = "https://catalog.hathitrust.org/api/volumes/brief/json/"
PAGE_API      = "https://babel.hathitrust.org/cgi/htd/pageocr/{htid}/{seq}"
META_API      = "https://babel.hathitrust.org/cgi/htd/volume/meta/{htid}/json"

MAX_PAGES     = 120     # pages to fetch per volume (≈ 60–90k chars of OCR)
PAGE_BATCH    = 20      # pages per request burst before sleeping
SLEEP_BETWEEN = 0.8     # seconds between page fetches
SEARCH_SLEEP  = 1.0


class HathiTrustAgent(BaseSourceAgent):
    source_name = "hathitrust"
    tier_affinity = {"theoretical", "practical", "reference"}
    min_hits = 2
    min_text_length = 3000

    def search(self, node: str, topic: str, lexicon: list) -> list:
        candidates = []
        seen_ids = set()

        queries = self._build_queries(node, topic)
        for query in queries:
            if len(candidates) >= MAX_CANDIDATES:
                break
            try:
                url = CATALOG_API + f"keyword/{requests.utils.quote(query)}.json"
                resp = requests.get(url, timeout=20, headers={"User-Agent": "harvester-bot/1.0"})
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for htid, item_data in data.items():
                    if len(candidates) >= MAX_CANDIDATES:
                        break
                    if htid in seen_ids:
                        continue
                    # Only full-view (public domain) items
                    items = item_data.get("items", [])
                    full_view = [i for i in items if i.get("usRightsString") == "Full view"]
                    if not full_view:
                        continue
                    rec = item_data.get("records", {})
                    title = ""
                    if rec:
                        first_rec = next(iter(rec.values()))
                        title = first_rec.get("titles", [""])[0]
                    seen_ids.add(htid)
                    candidates.append({
                        "identifier": htid,
                        "title": title,
                        "url": f"https://babel.hathitrust.org/cgi/pt?id={htid}",
                        "source": self.source_name,
                    })
            except Exception as e:
                log.debug(f"[hathitrust] search error for '{query}': {e}")
            time.sleep(SEARCH_SLEEP)

        return candidates

    def fetch_text(self, candidate: dict) -> str:
        htid = candidate.get("identifier", "")
        if not htid:
            return ""
        try:
            # Get page count from metadata
            meta_url = META_API.format(htid=requests.utils.quote(htid, safe=""))
            meta = requests.get(meta_url, timeout=15,
                                headers={"User-Agent": "harvester-bot/1.0"})
            if meta.status_code != 200:
                return ""
            info = meta.json()
            total_pages = info.get("htd:numpages", 0)
            if not total_pages:
                return ""

            pages_to_fetch = min(total_pages, MAX_PAGES)
            parts = [candidate.get("title", "")]
            fetched = 0

            for seq in range(1, pages_to_fetch + 1):
                try:
                    url = PAGE_API.format(
                        htid=requests.utils.quote(htid, safe=""), seq=seq
                    )
                    r = requests.get(url, timeout=12,
                                     headers={"User-Agent": "harvester-bot/1.0"})
                    if r.status_code == 200 and r.text.strip():
                        parts.append(r.text.strip())
                        fetched += 1
                except Exception:
                    pass

                if fetched % PAGE_BATCH == 0 and fetched > 0:
                    time.sleep(SLEEP_BETWEEN)

            return "\n\n".join(parts)
        except Exception as e:
            log.debug(f"[hathitrust] fetch error {htid}: {e}")
            return ""

    # -------------------------------------------------------------------------

    def _build_queries(self, node: str, topic: str) -> list:
        queries = []
        node_clean = re.sub(r'[^\w\s]', ' ', node).strip()
        topic_clean = re.sub(r'[^\w\s]', ' ', topic.replace('_', ' ')).strip()

        queries.append(node_clean)
        if topic_clean.lower() not in node_clean.lower():
            queries.append(f"{topic_clean} {node_clean}")
        # Broader topic sweep
        queries.append(topic_clean)
        return queries
