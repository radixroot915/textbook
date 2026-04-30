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
from organizer import analyze_technical_density
from selector import select_instructional_content

log = logging.getLogger(__name__)

_guard = Bouncer()
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Harvester/1.0)"}

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
            return None
        title = title[:40]

        if self.apply_html_filter:
            text = select_instructional_content(text)

        length_floor = self.min_text_length if self.min_text_length is not None else MIN_TEXT_LENGTH
        if len(text) < length_floor:
            log.info(f"    [{src}] SKIP short ({len(text)}c) — {title}")
            return None

        sample = text[:DENSITY_SAMPLE_BYTES]
        score, markers = analyze_technical_density(sample, lexicon)
        if score < self.min_hits or not markers:
            # Fallback: accept only if at least 2 distinct topic keywords are present
            topic_kw = [w for w in topic.replace('_', ' ').split() if len(w) > 4]
            ts, tm = analyze_technical_density(text, topic_kw)
            required = DENSITY_TOPIC_KW_MIN if len(topic_kw) >= 2 else 1
            if ts >= required:
                score, markers = ts, tm
            else:
                log.info(f"    [{src}] SKIP density {score}/{self.min_hits} — {title}")
                return None

        if _guard.is_duplicate(text):
            log.info(f"    [{src}] SKIP dup — {title}")
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
        return file_name

    def _fetch_raw(self, url: str, timeout: int = 20) -> str:
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code != 200:
                log.debug(f"[fetch] HTTP {r.status_code} — {url[-60:]}")
                return ""
            if url.endswith(".gz"):
                return gzip.decompress(r.content).decode("utf-8", errors="ignore")
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
