import os
import sys
import json
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VAULT_ROOT, RESEARCHER_MODEL
from llm.ollama_client import call_json
from llm.prompts import VIDEO_QUERIES_PROMPT

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Harvester/1.0)"}


def build_video_guide(topic: str, grit: list) -> str:
    out_dir = os.path.join(VAULT_ROOT, topic, "curriculum")
    os.makedirs(out_dir, exist_ok=True)

    print("[VIDEO] Generating YouTube search queries...")
    youtube_entries = _build_youtube_entries(topic, grit)

    print("[VIDEO] Scraping Instructables...")
    instructables = _scrape_instructables(topic)

    print("[VIDEO] Scraping WikiHow...")
    wikihow = _scrape_wikihow(topic)

    output = {
        "topic": topic,
        "note": "YouTube links are search result pages — open to browse tutorials at your level.",
        "youtube": youtube_entries,
        "instructables": instructables,
        "wikihow": wikihow
    }

    out_path = os.path.join(out_dir, f"{topic}_videos.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"[VIDEO] Video guide written: {out_path}")
    return out_path


def _build_youtube_entries(topic: str, grit: list) -> dict:
    skill_areas = _extract_skill_areas(grit, topic)
    prompt = VIDEO_QUERIES_PROMPT.format(topic=topic, skill_areas=", ".join(skill_areas[:15]))
    result = call_json(RESEARCHER_MODEL, prompt, temperature=0.4, timeout=120)

    if not isinstance(result, dict):
        result = _fallback_queries(topic)

    entries = {}
    for level in ["beginner", "intermediate", "advanced"]:
        queries = result.get(level, [])
        entries[level] = []
        for q in queries:
            encoded = urllib.parse.quote_plus(q)
            entries[level].append({
                "query": q,
                "url": f"https://www.youtube.com/results?search_query={encoded}",
                "level": level
            })
    return entries


def _scrape_instructables(topic: str) -> list:
    results = []
    try:
        url = f"https://www.instructables.com/search/?q={urllib.parse.quote_plus(topic)}&projects=all"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return results
        soup = BeautifulSoup(r.text, "html.parser")
        for card in soup.select("div.thumbnail-title")[:10]:
            a = card.find("a")
            if not a:
                continue
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if href and title:
                full_url = f"https://www.instructables.com{href}" if href.startswith("/") else href
                results.append({"title": title, "url": full_url, "source": "instructables"})
        time.sleep(1)
    except Exception as e:
        print(f"[VIDEO] Instructables scrape failed: {e}")
    return results


def _scrape_wikihow(topic: str) -> list:
    results = []
    try:
        search_url = f"https://www.wikihow.com/wikiHow-to-{urllib.parse.quote(topic.replace(' ', '-'))}"
        r = requests.get(search_url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            # Get article title
            title_el = soup.find("h1", class_="firstHeading")
            if title_el:
                results.append({
                    "title": title_el.get_text(strip=True),
                    "url": search_url,
                    "source": "wikihow"
                })
        # Also search wikihow
        search = f"https://www.wikihow.com/search.php?search_term={urllib.parse.quote_plus(topic)}"
        r2 = requests.get(search, headers=HEADERS, timeout=15)
        if r2.status_code == 200:
            soup2 = BeautifulSoup(r2.text, "html.parser")
            for a in soup2.select("a.result_link")[:8]:
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if title and href:
                    results.append({"title": title, "url": href, "source": "wikihow"})
        time.sleep(1)
    except Exception as e:
        print(f"[VIDEO] WikiHow scrape failed: {e}")
    return results


def _extract_skill_areas(grit: list, topic: str) -> list:
    areas = set()
    for item in grit:
        task = item.get("task", "")
        if task:
            # Take first 3 words as skill area descriptor
            areas.add(" ".join(task.split()[:4]))
    if not areas:
        areas = {f"{topic} basics", f"{topic} technique", f"{topic} safety", f"{topic} projects"}
    return list(areas)


def _fallback_queries(topic: str) -> dict:
    return {
        "beginner": [
            f"{topic} for beginners complete guide",
            f"how to start {topic} beginner tutorial",
            f"{topic} basics setup and safety",
            f"learning {topic} from scratch"
        ],
        "intermediate": [
            f"{topic} intermediate techniques",
            f"improving {topic} skills tutorial",
            f"{topic} tips and tricks",
            f"common {topic} mistakes and fixes"
        ],
        "advanced": [
            f"advanced {topic} techniques professional",
            f"{topic} complex project build",
            f"professional {topic} methods",
            f"{topic} masterclass advanced skills"
        ]
    }
