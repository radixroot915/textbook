"""
EverySpec agent — MIL-HDBK, MIL-STD, and other defense specifications.

everyspec.com hosts 45k+ public domain military handbooks and standards.
Many are directly relevant to trades: MIL-HDBK-1038 (Welding), etc.

Search:  everyspec.com search form (HTML scrape)
Fetch:   Direct PDF link from detail page

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

SEARCH_URL = "http://everyspec.com/search/?q={query}"
BASE_URL   = "http://everyspec.com"

SLEEP_BETWEEN = 1.5


class EverySpecAgent(BaseSourceAgent):
    source_name = "everyspec"
    tier_affinity = {"specialized", "reference"}
    min_hits = 1
    min_text_length = 2000

    def search(self, node: str, topic: str, lexicon: list) -> list:
        candidates = []
        seen_ids = set()

        queries = self._build_queries(node, topic)
        for query in queries:
            if len(candidates) >= MAX_CANDIDATES:
                break
            try:
                url = SEARCH_URL.format(query=requests.utils.quote(query))
                resp = requests.get(
                    url, timeout=20,
                    headers={"User-Agent": "harvester-bot/1.0"},
                )
                if resp.status_code != 200:
                    continue

                # Parse result links — pattern: /MIL-HDBK-XXXX/ or /MIL-STD-XXXX/
                links = re.findall(
                    r'href="(/[^"]+(?:MIL-|DOD-|ARMY-|NAVY-|USMC-)[^"]+/)"',
                    resp.text, re.IGNORECASE
                )
                titles = re.findall(
                    r'<(?:td|h[23])[^>]*>\s*(MIL-[^\s<]+|DOD-[^\s<]+)[^<]*</(?:td|h[23])>',
                    resp.text, re.IGNORECASE
                )

                for i, link in enumerate(links):
                    if len(candidates) >= MAX_CANDIDATES:
                        break
                    uid = link.strip("/")
                    if uid in seen_ids:
                        continue
                    seen_ids.add(uid)
                    title = titles[i] if i < len(titles) else uid
                    candidates.append({
                        "identifier": uid,
                        "title": title,
                        "url": BASE_URL + link,
                        "source": self.source_name,
                    })

            except Exception as e:
                log.debug(f"[everyspec] search error for '{query}': {e}")
            time.sleep(SLEEP_BETWEEN)

        return candidates

    def fetch_text(self, candidate: dict) -> str:
        detail_url = candidate.get("url", "")
        if not detail_url:
            return ""

        try:
            resp = requests.get(
                detail_url, timeout=20,
                headers={"User-Agent": "harvester-bot/1.0"},
            )
            if resp.status_code != 200:
                return ""

            # Find PDF download link
            pdf_links = re.findall(r'href="([^"]+\.pdf[^"]*)"', resp.text, re.IGNORECASE)
            if not pdf_links:
                # Try looking for a direct download link
                pdf_links = re.findall(r'href="([^"]+/download/[^"]+)"', resp.text, re.IGNORECASE)

            if not pdf_links:
                return ""

            pdf_url = pdf_links[0]
            if not pdf_url.startswith("http"):
                pdf_url = BASE_URL + pdf_url

            pdf_resp = requests.get(
                pdf_url, timeout=40,
                headers={"User-Agent": "harvester-bot/1.0"},
                stream=True,
            )
            if pdf_resp.status_code != 200:
                return ""

            pdf_bytes = pdf_resp.content

            try:
                from pdfminer.high_level import extract_text_to_fp
                from pdfminer.layout import LAParams
                import io
                output = io.StringIO()
                extract_text_to_fp(io.BytesIO(pdf_bytes), output, laparams=LAParams(), output_type="text", codec="utf-8")
                text = output.getvalue().strip()
                if text:
                    return f"{candidate.get('title', '')}\n\n{text}"
            except Exception:
                pass

            try:
                import pypdf
                import io
                reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
                parts = [p.extract_text() for p in reader.pages[:100] if p.extract_text()]
                text = "\n\n".join(parts).strip()
                if text:
                    return f"{candidate.get('title', '')}\n\n{text}"
            except Exception:
                pass

        except Exception as e:
            log.debug(f"[everyspec] fetch error {candidate.get('identifier')}: {e}")

        return ""

    def _build_queries(self, node: str, topic: str) -> list:
        queries = []
        node_clean = re.sub(r'[^\w\s]', ' ', node).strip()
        topic_clean = re.sub(r'[^\w\s]', ' ', topic.replace('_', ' ')).strip()

        # MIL-HDBK queries tend to be most fruitful
        queries.append(f"{topic_clean} handbook")
        queries.append(node_clean)
        if topic_clean.lower() not in node_clean.lower():
            queries.append(f"{topic_clean} {node_clean}")
        return queries
