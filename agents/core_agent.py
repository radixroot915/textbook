"""
CORE.ac.uk agent — 25M+ open-access research papers.

API:  api.core.ac.uk/v3  (free tier, no key required for basic search)
      POST /search/works  with JSON body
      GET  /works/{id}    for metadata + links

Full text is retrieved either from fullTextLink or from the PDF if available.
Rate limit: ~10 req/min on free tier — sleep generously.
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

CORE_SEARCH = "https://api.core.ac.uk/v3/search/works"
CORE_WORK   = "https://api.core.ac.uk/v3/works/{id}"

SLEEP_BETWEEN = 2.0   # free tier is rate-limited


class COREAgent(BaseSourceAgent):
    source_name = "core"
    tier_affinity = {"theoretical", "specialized", "reference"}
    min_hits = 2
    min_text_length = 2000

    def search(self, node: str, topic: str, lexicon: list) -> list:
        candidates = []
        seen_ids = set()

        queries = self._build_queries(node, topic)
        for query in queries:
            if len(candidates) >= MAX_CANDIDATES:
                break
            try:
                payload = {
                    "q": query,
                    "limit": 15,
                    "offset": 0,
                    "filters": {"yearPublished": {"gte": 1980}},
                }
                headers = {
                    "User-Agent": "harvester-bot/1.0",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                resp = requests.post(
                    CORE_SEARCH, json=payload, headers=headers, timeout=25
                )
                if resp.status_code not in (200, 201):
                    log.debug(f"[core] search HTTP {resp.status_code} for '{query}'")
                    continue
                data = resp.json()
                results = data.get("results", [])
                for item in results:
                    if len(candidates) >= MAX_CANDIDATES:
                        break
                    work_id = str(item.get("id", ""))
                    if not work_id or work_id in seen_ids:
                        continue
                    # Prefer works with fulltext
                    has_full = item.get("fullText") or item.get("downloadUrl")
                    if not has_full:
                        # Still queue it — we'll try to get fulltext via work detail
                        pass
                    seen_ids.add(work_id)
                    title = item.get("title", "")
                    url = item.get("sourceFulltextUrls", [None])[0] or \
                          f"https://core.ac.uk/works/{work_id}"
                    candidates.append({
                        "identifier": work_id,
                        "title": title,
                        "url": url,
                        "download_url": item.get("downloadUrl", ""),
                        "full_text": item.get("fullText", ""),
                        "source": self.source_name,
                    })
            except Exception as e:
                log.debug(f"[core] search error for '{query}': {e}")
            time.sleep(SLEEP_BETWEEN)

        return candidates

    def fetch_text(self, candidate: dict) -> str:
        # Prefer pre-fetched fulltext from search result
        full_text = candidate.get("full_text", "")
        if full_text and len(full_text) > 500:
            return full_text.strip()

        work_id = candidate.get("identifier", "")
        if not work_id:
            return ""

        try:
            # Fetch work detail to get fulltext or download link
            resp = requests.get(
                CORE_WORK.format(id=work_id),
                headers={"User-Agent": "harvester-bot/1.0"},
                timeout=20,
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()

            full_text = data.get("fullText", "")
            if full_text and len(full_text) > 500:
                return full_text.strip()

            # Try PDF download
            pdf_url = data.get("downloadUrl", "")
            if pdf_url:
                return self._fetch_pdf(pdf_url)

        except Exception as e:
            log.debug(f"[core] fetch error {work_id}: {e}")

        return ""

    def _fetch_pdf(self, url: str) -> str:
        try:
            resp = requests.get(
                url, timeout=30,
                headers={"User-Agent": "harvester-bot/1.0"},
                stream=True,
            )
            if resp.status_code != 200:
                return ""
            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower() and "octet" not in content_type.lower():
                return ""

            pdf_bytes = resp.content
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
            log.debug(f"[core] pdf fetch error {url}: {e}")
        return ""

    def _build_queries(self, node: str, topic: str) -> list:
        queries = []
        node_clean = re.sub(r'[^\w\s]', ' ', node).strip()
        topic_clean = re.sub(r'[^\w\s]', ' ', topic.replace('_', ' ')).strip()

        queries.append(node_clean)
        if topic_clean.lower() not in node_clean.lower():
            queries.append(f"{topic_clean} {node_clean}")
        queries.append(f"{topic_clean} techniques")
        return queries
