"""
DTIC agent — Defense Technical Information Center (4.7M military technical reports).

Search:  discover.dtic.mil full-text search API
Fetch:   Direct PDF pattern apps.dtic.mil/sti/pdfs/{id}.pdf

No auth required for unclassified/unlimited reports.
PDF text is extracted with pdfminer.six (falls back to empty on failure).
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

SEARCH_URL = "https://discover.dtic.mil/technical-reports/?search={query}&page={page}"
SEARCH_API  = "https://discover.dtic.mil/wp-json/dtic/v1/search"
PDF_URL     = "https://apps.dtic.mil/sti/pdfs/{id}.pdf"
DETAIL_URL  = "https://apps.dtic.mil/sti/citations/{id}"

SLEEP_BETWEEN = 1.2


class DTICAgent(BaseSourceAgent):
    source_name = "dtic"
    tier_affinity = {"theoretical", "specialized"}
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
                params = {
                    "q": query,
                    "rows": 20,
                    "start": 0,
                    "fq": "accessibility_s:Public",
                }
                headers = {
                    "User-Agent": "harvester-bot/1.0",
                    "Accept": "application/json",
                }
                resp = requests.get(
                    SEARCH_API, params=params, headers=headers, timeout=20
                )
                if resp.status_code != 200:
                    # Fallback: scrape HTML search page
                    candidates.extend(self._scrape_search(query, seen_ids))
                    continue

                data = resp.json()
                docs = data.get("response", {}).get("docs", [])
                if not docs:
                    docs = data.get("docs", [])

                for doc in docs:
                    if len(candidates) >= MAX_CANDIDATES:
                        break
                    accession = doc.get("accession_number") or doc.get("id", "")
                    if not accession or accession in seen_ids:
                        continue
                    # Skip non-public or classified
                    access = doc.get("accessibility_s", "") or doc.get("access", "")
                    if access and "public" not in access.lower():
                        continue
                    title = doc.get("title", "")
                    seen_ids.add(accession)
                    candidates.append({
                        "identifier": accession,
                        "title": title,
                        "url": DETAIL_URL.format(id=accession),
                        "pdf_url": PDF_URL.format(id=accession),
                        "source": self.source_name,
                    })
            except Exception as e:
                log.debug(f"[dtic] search error for '{query}': {e}")

            time.sleep(SLEEP_BETWEEN)

        return candidates

    def _scrape_search(self, query: str, seen_ids: set) -> list:
        """HTML scrape fallback — parses accession numbers from search result page."""
        results = []
        try:
            url = f"https://discover.dtic.mil/technical-reports/?search={requests.utils.quote(query)}"
            resp = requests.get(url, timeout=20, headers={"User-Agent": "harvester-bot/1.0"})
            if resp.status_code != 200:
                return []
            # Accession numbers look like AD123456 or ADA123456
            ids = re.findall(r'\b(AD[A-Z]?\d{6,9})\b', resp.text)
            for accession in ids:
                if accession not in seen_ids:
                    seen_ids.add(accession)
                    results.append({
                        "identifier": accession,
                        "title": accession,
                        "url": DETAIL_URL.format(id=accession),
                        "pdf_url": PDF_URL.format(id=accession),
                        "source": self.source_name,
                    })
        except Exception as e:
            log.debug(f"[dtic] scrape fallback error: {e}")
        return results

    def fetch_text(self, candidate: dict) -> str:
        pdf_url = candidate.get("pdf_url", "")
        if not pdf_url:
            accession = candidate.get("identifier", "")
            if not accession:
                return ""
            pdf_url = PDF_URL.format(id=accession)

        try:
            resp = requests.get(
                pdf_url, timeout=30,
                headers={"User-Agent": "harvester-bot/1.0"},
                stream=True,
            )
            if resp.status_code != 200:
                return ""

            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower() and "octet" not in content_type.lower():
                return ""

            pdf_bytes = resp.content
            return self._extract_pdf_text(pdf_bytes)
        except Exception as e:
            log.debug(f"[dtic] fetch error {candidate.get('identifier')}: {e}")
            return ""

    def _extract_pdf_text(self, pdf_bytes: bytes) -> str:
        try:
            from pdfminer.high_level import extract_text_to_fp
            from pdfminer.layout import LAParams
            import io

            output = io.StringIO()
            extract_text_to_fp(
                io.BytesIO(pdf_bytes),
                output,
                laparams=LAParams(),
                output_type="text",
                codec="utf-8",
            )
            return output.getvalue().strip()
        except ImportError:
            log.debug("[dtic] pdfminer not installed — trying fallback")
        except Exception as e:
            log.debug(f"[dtic] pdfminer error: {e}")

        # Fallback: pypdf
        try:
            import pypdf
            import io
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            parts = []
            for page in reader.pages[:80]:
                t = page.extract_text()
                if t:
                    parts.append(t)
            return "\n\n".join(parts).strip()
        except Exception as e:
            log.debug(f"[dtic] pypdf error: {e}")

        return ""

    def _build_queries(self, node: str, topic: str) -> list:
        queries = []
        node_clean = re.sub(r'[^\w\s]', ' ', node).strip()
        topic_clean = re.sub(r'[^\w\s]', ' ', topic.replace('_', ' ')).strip()

        queries.append(node_clean)
        if topic_clean.lower() not in node_clean.lower():
            queries.append(f"{topic_clean} {node_clean}")
        queries.append(f"{topic_clean} manual")
        queries.append(f"{topic_clean} handbook")
        return queries
