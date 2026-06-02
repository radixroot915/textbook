"""
SkillsCommons agent — US Dept of Labor TAACCCT OER repository.

700+ community college workforce training materials (CC-licensed).
Search: skillscommons.org REST API (DSpace-based)
Fetch:  bitstream download URL for plain-text or PDF content

No auth required.
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

SEARCH_API   = "https://skillscommons.org/rest/items"
BITSTREAM_API = "https://skillscommons.org/rest/items/{id}/bitstreams"

SLEEP_BETWEEN = 1.0


class SkillsCommonsAgent(BaseSourceAgent):
    source_name = "skillscommons"
    tier_affinity = {"foundational", "practical"}
    min_hits = 2
    min_text_length = 1500

    def search(self, node: str, topic: str, lexicon: list) -> list:
        candidates = []
        seen_ids = set()

        queries = self._build_queries(node, topic)
        for query in queries:
            if len(candidates) >= MAX_CANDIDATES:
                break
            try:
                params = {
                    "query": query,
                    "limit": 20,
                    "offset": 0,
                    "expand": "metadata",
                }
                headers = {
                    "User-Agent": "harvester-bot/1.0",
                    "Accept": "application/json",
                }
                resp = requests.get(
                    SEARCH_API, params=params, headers=headers, timeout=25
                )
                if resp.status_code != 200:
                    # Try the search endpoint
                    resp = requests.get(
                        "https://skillscommons.org/rest/items/find-by-metadata-field",
                        json={"key": "dc.subject", "value": query},
                        headers=headers,
                        timeout=25,
                    )
                    if resp.status_code != 200:
                        continue

                items = resp.json() if isinstance(resp.json(), list) else []
                for item in items:
                    if len(candidates) >= MAX_CANDIDATES:
                        break
                    item_id = str(item.get("id", ""))
                    if not item_id or item_id in seen_ids:
                        continue

                    # Extract title from metadata
                    title = ""
                    for meta in item.get("metadata", []):
                        if meta.get("key") == "dc.title":
                            title = meta.get("value", "")
                            break
                    if not title:
                        title = item.get("name", "")

                    seen_ids.add(item_id)
                    candidates.append({
                        "identifier": item_id,
                        "title": title,
                        "url": f"https://skillscommons.org/handle/{item.get('handle', item_id)}",
                        "source": self.source_name,
                    })
            except Exception as e:
                log.debug(f"[skillscommons] search error for '{query}': {e}")
            time.sleep(SLEEP_BETWEEN)

        return candidates

    def fetch_text(self, candidate: dict) -> str:
        item_id = candidate.get("identifier", "")
        if not item_id:
            return ""

        try:
            resp = requests.get(
                BITSTREAM_API.format(id=item_id),
                headers={"User-Agent": "harvester-bot/1.0", "Accept": "application/json"},
                timeout=20,
            )
            if resp.status_code != 200:
                return ""

            bitstreams = resp.json()
            if not isinstance(bitstreams, list):
                return ""

            # Prefer txt, then pdf
            txt_stream = next(
                (b for b in bitstreams if b.get("mimeType", "").startswith("text/")), None
            )
            pdf_stream = next(
                (b for b in bitstreams
                 if "pdf" in b.get("mimeType", "").lower()), None
            )

            chosen = txt_stream or pdf_stream
            if not chosen:
                return ""

            dl_url = chosen.get("retrieveLink", "")
            if not dl_url:
                return ""

            if not dl_url.startswith("http"):
                dl_url = "https://skillscommons.org" + dl_url

            content_resp = requests.get(
                dl_url, timeout=30,
                headers={"User-Agent": "harvester-bot/1.0"},
                stream=True,
            )
            if content_resp.status_code != 200:
                return ""

            mime = chosen.get("mimeType", "")
            if "text" in mime:
                return content_resp.text.strip()

            # PDF
            pdf_bytes = content_resp.content
            try:
                from pdfminer.high_level import extract_text_to_fp
                from pdfminer.layout import LAParams
                import io
                output = io.StringIO()
                extract_text_to_fp(io.BytesIO(pdf_bytes), output, laparams=LAParams(), output_type="text", codec="utf-8")
                return output.getvalue().strip()
            except Exception:
                pass
            try:
                import pypdf
                import io
                reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
                parts = [p.extract_text() for p in reader.pages[:60] if p.extract_text()]
                return "\n\n".join(parts).strip()
            except Exception:
                pass

        except Exception as e:
            log.debug(f"[skillscommons] fetch error {item_id}: {e}")

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
