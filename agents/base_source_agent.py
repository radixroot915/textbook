import os
import sys
import logging
import time
import gzip
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VAULT_ROOT, DENSITY_SAMPLE_BYTES, DENSITY_TOPIC_KW_MIN, MAX_CANDIDATES, MIN_TEXT_LENGTH
from bouncer import Bouncer
from organizer import analyze_technical_density, detect_ocr_garbage, density_multi_window, llm_topic_gate
from selector import select_instructional_content
from watchdog import wd

log = logging.getLogger(__name__)

_guard = Bouncer()
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Harvester/1.0)"}


def _expand_topic_keywords(topic: str) -> list[str]:
    """Expand a topic name into searchable keyword variants.

    "leatherworking" → ["leatherworking", "leather working", "leather work",
                        "leather"]
    "blacksmithing" → ["blacksmithing", "black smithing", "smithing",
                        "blacksmith"]
    Catches compounds that real source documents express as space-separated
    or shorter root forms.
    """
    base = topic.replace('_', ' ').strip().lower()
    kws: list[str] = []
    seen: set[str] = set()

    def add(w: str):
        w = w.strip()
        if w and len(w) > 3 and w not in seen:
            seen.add(w)
            kws.append(w)

    add(base)
    # Split on spaces
    for w in base.split():
        if len(w) > 4:
            add(w)
    # Compound-word splitting: try common craft/trade suffixes
    for w in base.split():
        if len(w) > 7:
            for suf in ("working", "smithing", "making", "crafting", "ing"):
                if w.endswith(suf) and len(w) > len(suf) + 3:
                    stem = w[:-len(suf)]
                    add(stem)
                    # Two-word form: "leather working", "black smithing"
                    add(f"{stem} {suf}")
                    # Drop -ing for noun form: "leather work"
                    if suf.endswith("ing"):
                        add(f"{stem} {suf[:-3]}")
                    break
    return kws


def _extract_pdf_text(content: bytes) -> str:
    """Extract text from PDF bytes. Returns empty string on failure."""
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(parts).strip()
    except Exception as e:
        log.debug(f"[pdf] extract error: {e}")
        return ""


def set_topic_bouncer(slug: str):
    """Switch the module-level dedup guard to a per-topic fingerprint file.
    Call this once before starting workers for a given topic so that each
    topic's vault has independent dedup and documents aren't blocked because
    they were already saved under a different topic."""
    from config import topic_hash_path
    global _guard
    _guard = Bouncer(path=topic_hash_path(slug))

_TITLE_BLOCKLIST = frozenset({
    # Religious texts
    "bible", "holy bible", "testament", "old testament", "new testament",
    "gospel", "gospels", "scripture", "scriptures", "quran", "koran",
    "torah", "talmud", "psalms", "proverbs", "ecclesiastes", "leviticus",
    "deuteronomy", "genesis", "exodus", "revelations",
    # Fiction markers
    "a romance", "a novel", "a tale", "a story", "poems", "poetry",
    "sonnets", "ballads", "fairy tales",
})


class BaseSourceAgent:
    source_name = "base"
    priority = 99
    min_hits = 1
    apply_html_filter = False
    fetch_sleep = 1.0
    min_text_length = None  # None = use MIN_TEXT_LENGTH from config
    # tier_affinity declares which skill tiers this agent serves well.
    # Tiers: foundational, practical, theoretical, specialized, reference.
    # Empty/None = serves all tiers (backwards-compatible default).
    tier_affinity: set = None

    def search(self, node: str, topic: str, lexicon: list) -> list:
        raise NotImplementedError

    def _cap_candidates(self, candidates: list) -> list:
        return candidates[:MAX_CANDIDATES]

    def fetch_text(self, candidate: dict) -> str:
        raise NotImplementedError

    def validate_and_save(self, text: str, node: str, topic: str, lexicon: list, candidate: dict) -> str | None:
        src = self.source_name
        title = candidate.get("title", "?")
        title_lower = title.lower()
        if any(marker in title_lower for marker in _TITLE_BLOCKLIST):
            log.info(f"    [{src}] SKIP blocked title — {title[:50]}")
            wd.emit("harvest", "gate_reject", gate="title_block", source=src)
            return None
        title = title[:40]

        if self.apply_html_filter:
            text = select_instructional_content(text)

        length_floor = self.min_text_length if self.min_text_length is not None else MIN_TEXT_LENGTH
        if len(text) < length_floor:
            log.info(f"    [{src}] SKIP short ({len(text)}c) — {title}")
            wd.emit("harvest", "gate_reject", gate="length", source=src)
            return None

        # Gate 1: OCR garbage detector — kills Archive.org scan noise
        is_garbage, reason = detect_ocr_garbage(text)
        if is_garbage:
            log.info(f"    [{src}] SKIP garbage ({reason}) — {title}")
            wd.emit("harvest", "gate_reject", gate="ocr_garbage", source=src, reason=reason)
            return None

        # Gate 2: Multi-window density — catches off-topic pollution that
        # happens to have on-topic vocabulary only in the opening
        score, markers = density_multi_window(text, lexicon)
        if score < self.min_hits or not markers:
            # Fallback: at least N distinct topic keywords in body.
            # Expand compounds like "leatherworking" into stems and split forms
            # so documents that use "leather working", "leather work", or
            # plain "leather" still match.
            topic_kw = _expand_topic_keywords(topic)
            ts, tm = analyze_technical_density(text, topic_kw)
            required = DENSITY_TOPIC_KW_MIN if len(topic_kw) >= 2 else 1
            if ts >= required:
                score, markers = ts, tm
            else:
                log.info(f"    [{src}] SKIP density {score}/{self.min_hits} — {title}")
                wd.emit("harvest", "gate_reject", gate="drift", source=src)
                return None

        # Gate 3: LLM topic-validation, only on borderline cases (low marker
        # diversity). Returns None when Ollama is unavailable — treat as accept.
        if len(markers) < 3:
            verdict = llm_topic_gate(text, topic)
            if verdict is False:
                log.info(f"    [{src}] SKIP llm-gate (off-topic) — {title}")
                wd.emit("harvest", "gate_reject", gate="drift", source=src, reason="llm-gate")
                return None

        if _guard.is_duplicate(text):
            log.info(f"    [{src}] SKIP dup — {title}")
            wd.emit("harvest", "gate_reject", gate="dedup", source=src)
            return None

        topic_path = os.path.join(VAULT_ROOT, topic)
        os.makedirs(topic_path, exist_ok=True)

        safe_node = "".join(c if c.isalnum() or c == " " else "" for c in node).replace(" ", "_")
        identifier = candidate.get("identifier", candidate.get("url", "unknown"))
        id_slug = "".join(c for c in str(identifier)[-12:] if c.isalnum())
        file_name = f"VOL_{safe_node}_{id_slug}.txt"
        full_path = os.path.join(topic_path, file_name)

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(text)

        _guard.register_fingerprint(text)
        wd.emit("harvest", "save", source=src, size=len(text))
        try:
            from agent_stats import record_saved
            record_saved(topic, self.source_name, file_name)
        except Exception:
            pass
        try:
            from classifier import classify_file
            classify_file(file_name, text, topic)
        except Exception as e:
            log.debug(f"[classify] error on {file_name}: {e}")
        try:
            from claims_db import extract_and_store_claims
            extract_and_store_claims(topic, file_name, self.source_name, text)
        except Exception as e:
            log.debug(f"[claims] error on {file_name}: {e}")
        try:
            from drift_monitor import log_save
            from classifier import get_classification
            cls = get_classification(file_name)
            # Topic density: occurrences of topic root per 100 words
            root = topic.replace('_', ' ').split()[0].lower()
            words_count = max(len(text.split()), 1)
            density = (text.lower().count(root) / words_count) * 100
            log_save(topic, file_name, cls, topic_density=density)
        except Exception as e:
            log.debug(f"[drift] log error: {e}")
        return file_name

    def _fetch_raw(self, url: str, timeout: int = 20) -> str:
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code != 200:
                log.debug(f"[fetch] HTTP {r.status_code} — {url[-60:]}")
                return ""
            if url.endswith(".gz"):
                return gzip.decompress(r.content).decode("utf-8", errors="ignore")
            content_type = r.headers.get("Content-Type", "").lower()
            if url.lower().endswith(".pdf") or "application/pdf" in content_type:
                return _extract_pdf_text(r.content)
            return r.text
        except Exception as e:
            log.debug(f"[fetch] error ({type(e).__name__}) — {url[-60:]}")
            return ""

    def _fetch_abbyy(self, identifier: str) -> str:
        """Extract plain text from abbyy.gz XML (last resort — very slow on large files)."""
        url = f"https://archive.org/download/{identifier}/{identifier}_abbyy.gz"
        try:
            r = requests.get(url, headers=HEADERS, timeout=40)
            if r.status_code != 200:
                return ""
            xml = gzip.decompress(r.content).decode("utf-8", errors="ignore")
            soup = BeautifulSoup(xml, "html.parser")
            return " ".join(t.get_text(" ") for t in soup.find_all("line"))
        except Exception:
            return ""

    def _fetch_archive_text(self, identifier: str) -> str:
        # Ask metadata API which text files actually exist — avoids wasted 404 fetches
        available = self._archive_text_suffixes(identifier)
        if not available:
            log.debug(f"[archive] no text files for {identifier}")
            return ""
        base = f"https://archive.org/download/{identifier}/{identifier}"
        for suffix in available:
            if suffix == "_abbyy.gz":
                text = self._fetch_abbyy(identifier)
            else:
                text = self._fetch_raw(base + suffix, timeout=40)
            if text:
                return text
            time.sleep(0.5)
        return ""

    def _archive_text_suffixes(self, identifier: str) -> list:
        """Return ordered list of available text suffixes, or [] if access-restricted."""
        _SUFFIXES = ["_djvu.txt", "_hocr_searchtext.txt.gz", "_text.txt"]
        try:
            r = requests.get(
                f"https://archive.org/metadata/{identifier}",
                headers=HEADERS, timeout=10
            )
            if r.status_code != 200:
                return _SUFFIXES  # fall back to trying all
            data = r.json()
            if data.get("metadata", {}).get("access-restricted-item"):
                return []  # borrow-only — download will 403
            files = {f["name"] for f in data.get("files", [])}
            # Filenames sometimes use spaces/different casing — match by suffix only
            available_suffixes = {s for f in files for s in _SUFFIXES if f.endswith(s)}
            return [s for s in _SUFFIXES if s in available_suffixes]
        except Exception:
            return _SUFFIXES
