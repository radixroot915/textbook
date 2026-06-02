"""RedditAgent — search topic-relevant subreddits via Reddit's public JSON API.

Reddit is a goldmine for craft/trade topics: r/Leathercraft, r/Blacksmith,
r/woodworking, r/Welding etc. have years of expert posts with concrete
techniques, troubleshooting, kit recommendations — the stuff books don't
cover. No API key needed for the JSON endpoints.
"""

import sys
import os
import time
import logging
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.base_source_agent import BaseSourceAgent, HEADERS

log = logging.getLogger(__name__)

SLEEP = 2.0          # Reddit asks ≥1 req/2s for unauthenticated
MAX_THREADS = 12     # threads pulled per node


# Per-topic subreddit hints. Falls back to a global craft/trade search
# if the topic key isn't here. Keys are matched via substring.
_SUBREDDIT_MAP = {
    "leather":     ["Leathercraft", "leatherworking"],
    "blacksmith":  ["Blacksmith", "metalworking"],
    "wood":        ["woodworking", "BeginnerWoodWorking"],
    "weld":        ["Welding"],
    "carpent":     ["Carpentry", "HomeImprovement"],
    "frame":       ["HomeImprovement", "Construction"],
    "powershell":  ["PowerShell"],
    "tool":        ["BeginnerWoodWorking", "tools", "ToolMaker"],
}


def _subreddits_for(topic: str) -> list[str]:
    tl = topic.replace('_', ' ').lower()
    subs: list[str] = []
    for key, sublist in _SUBREDDIT_MAP.items():
        if key in tl:
            subs.extend(sublist)
    if not subs:
        # Generic fallback — DIY/craft-adjacent communities
        subs = ["DIY", "howto"]
    # dedup
    seen: set = set()
    out = []
    for s in subs:
        if s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


class RedditAgent(BaseSourceAgent):
    source_name = "reddit"
    priority = 2
    min_hits = 1
    apply_html_filter = False
    fetch_sleep = SLEEP
    min_text_length = 800  # threads can be shorter than book chapters
    tier_affinity = {"foundational", "practical"}

    def search(self, node: str, topic: str, lexicon: list) -> list:
        subreddits = _subreddits_for(topic)
        node_q = node.replace("_", " ")
        candidates = []
        seen_ids: set = set()

        for sub in subreddits[:3]:
            url = f"https://www.reddit.com/r/{sub}/search.json"
            params = {
                "q": node_q,
                "restrict_sr": "on",
                "sort": "relevance",
                "limit": min(MAX_THREADS, 25),
                "t": "all",
            }
            try:
                r = requests.get(url, headers=HEADERS, params=params, timeout=20)
                if r.status_code != 200:
                    log.debug(f"[reddit] HTTP {r.status_code} on r/{sub}")
                    continue
                data = r.json()
                children = data.get("data", {}).get("children", []) or []
                for c in children:
                    post = c.get("data") or {}
                    pid = post.get("id") or post.get("name")
                    if not pid or pid in seen_ids:
                        continue
                    # Quality gate: comment count > 5 OR score > 10
                    if (post.get("num_comments") or 0) < 5 and (post.get("score") or 0) < 10:
                        continue
                    seen_ids.add(pid)
                    permalink = post.get("permalink", "")
                    if not permalink:
                        continue
                    candidates.append({
                        "identifier": f"reddit_{pid}",
                        "title": post.get("title", "")[:120],
                        "url": f"https://www.reddit.com{permalink}.json",
                        "permalink": f"https://www.reddit.com{permalink}",
                        "source": self.source_name,
                        "subreddit": sub,
                    })
                time.sleep(SLEEP)
            except Exception as e:
                log.debug(f"[reddit] r/{sub} search error: {e}")
                continue
        return self._cap_candidates(candidates)

    def fetch_text(self, candidate: dict) -> str:
        url = candidate.get("url", "")
        if not url:
            return ""
        try:
            time.sleep(self.fetch_sleep)
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                return ""
            data = r.json()
        except Exception as e:
            log.debug(f"[reddit] fetch error: {e}")
            return ""

        if not isinstance(data, list) or len(data) < 2:
            return ""

        parts: list[str] = []

        # Original post
        try:
            post = data[0]["data"]["children"][0]["data"]
            parts.append(f"# {post.get('title', '')}")
            parts.append(f"[r/{post.get('subreddit', '')} · "
                         f"{post.get('score', 0)} points · "
                         f"{post.get('num_comments', 0)} comments]")
            body = post.get("selftext", "")
            if body:
                parts.append(body)
        except Exception:
            pass

        # Comments
        try:
            comments = data[1]["data"]["children"]
            for ch in comments[:30]:
                cd = ch.get("data") or {}
                if cd.get("kind") == "more":
                    continue
                body = cd.get("body", "")
                score = cd.get("score", 0) or 0
                if not body or score < 1 or len(body) < 40:
                    continue
                author = cd.get("author", "?")
                parts.append(f"\n---\n**[{score} pts · {author}]**\n{body}")
        except Exception:
            pass

        return "\n\n".join(parts).strip()
