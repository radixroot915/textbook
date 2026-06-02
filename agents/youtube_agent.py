import sys
import os
import time
import logging
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent, HEADERS
from config import YOUTUBE_API_KEY

log = logging.getLogger(__name__)

YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
SLEEP = 0.5


class YouTubeTranscriptAgent(BaseSourceAgent):
    source_name = "youtube"
    priority = 2
    min_hits = 2
    apply_html_filter = False
    fetch_sleep = SLEEP
    min_text_length = 1000
    tier_affinity = {"foundational", "practical"}

    def search(self, node: str, topic: str, lexicon: list) -> list:
        if not YOUTUBE_API_KEY:
            log.debug("[youtube] YOUTUBE_API_KEY not set — skipping")
            return []
        node = node.replace("_", " ")
        topic = topic.replace("_", " ")
        queries = list(dict.fromkeys([
            f"{topic} {node} tutorial",
            f"{topic} {node} technique",
            f"{node} how to",
        ]))
        candidates = []
        seen = set()
        for query in queries:
            try:
                r = requests.get(YT_SEARCH, params={
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "key": YOUTUBE_API_KEY,
                    "maxResults": 10,
                    "relevanceLanguage": "en",
                    "videoCaption": "closedCaption",
                }, timeout=15)
                if r.status_code == 403:
                    log.warning("[youtube] API quota exceeded or key invalid")
                    break
                if r.status_code != 200:
                    log.debug(f"[youtube] search HTTP {r.status_code}")
                    continue
                for item in r.json().get("items", []):
                    vid = item.get("id", {}).get("videoId")
                    if not vid or vid in seen:
                        continue
                    seen.add(vid)
                    snippet = item.get("snippet", {})
                    candidates.append({
                        "identifier": f"yt_{vid}",
                        "title": snippet.get("title", ""),
                        "video_id": vid,
                        "description": snippet.get("description", ""),
                        "source": self.source_name,
                    })
                time.sleep(SLEEP)
            except Exception as e:
                log.debug(f"[youtube] search exception: {e}")
                continue
        return self._cap_candidates(candidates)

    def fetch_text(self, candidate: dict) -> str:
        vid = candidate.get("video_id")
        if not vid:
            return ""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            transcript = YouTubeTranscriptApi.get_transcript(vid, languages=["en"])
            body = " ".join(seg["text"] for seg in transcript)
            parts = [candidate.get("title", ""), candidate.get("description", ""), body]
            return "\n\n".join(p for p in parts if p.strip())
        except ImportError:
            log.warning("[youtube] youtube-transcript-api not installed: pip install youtube-transcript-api")
            return ""
        except Exception as e:
            log.debug(f"[youtube] transcript unavailable for {vid}: {e}")
            return ""
