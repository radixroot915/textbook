import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VAULT_ROOT, RESEARCHER_MODEL
from llm.ollama_client import call, call_json
from llm.prompts import TEXTBOOK_CHAPTER_PROMPT, CURRICULUM_PLAN_PROMPT
from organizer import analyze_technical_density


def build_curriculum(topic: str, lexicon: list, grit: list):
    """Assemble the full curriculum package from harvested content and grit."""
    out_dir = os.path.join(VAULT_ROOT, topic, "curriculum")
    os.makedirs(out_dir, exist_ok=True)

    print(f"[CURRICULUM] Building textbook...")
    textbook = _build_textbook(topic, lexicon, grit, out_dir)

    print(f"[CURRICULUM] Building milestone plan...")
    plan = _build_plan(topic, grit, out_dir)

    return {"textbook": textbook, "plan": plan}


def _build_textbook(topic: str, lexicon: list, grit: list, out_dir: str) -> str:
    topic_path = os.path.join(VAULT_ROOT, topic)
    vault_files = []
    if os.path.exists(topic_path):
        vault_files = [f for f in os.listdir(topic_path) if f.endswith(".txt")]

    if not grit:
        chapters = [f"# {topic.replace('_', ' ').title()}: Technical Reference\n\n*Content being compiled from harvested sources.*"]
    else:
        # Precompute all vault file texts once to avoid re-reading per chapter
        file_texts = {}
        for fname in vault_files:
            try:
                with open(os.path.join(topic_path, fname), "r", encoding="utf-8", errors="ignore") as f:
                    file_texts[fname] = f.read()
            except Exception:
                pass

        chapters = []
        chunk_size = 5
        for i in range(0, len(grit), chunk_size):
            grit_chunk = grit[i:i + chunk_size]
            excerpt = _find_excerpt(topic_path, vault_files, grit_chunk, lexicon, file_texts)
            grit_text = json.dumps(grit_chunk, indent=2)
            chapter_topic = _infer_chapter_topic(grit_chunk, i, topic)
            prompt = TEXTBOOK_CHAPTER_PROMPT.format(
                topic=topic,
                chapter_topic=chapter_topic,
                source_excerpts=excerpt[:1500],
                grit_items=grit_text[:1000]
            )
            chapter_md = call(RESEARCHER_MODEL, prompt, temperature=0.4, timeout=60)
            if chapter_md:
                chapters.append(f"# Chapter {i // chunk_size + 1}: {chapter_topic}\n\n{chapter_md}")

        if not chapters:
            chapters = [f"# {topic.replace('_', ' ').title()}: Technical Reference\n\n*Content being compiled from harvested sources.*"]

    textbook_content = f"# {topic.replace('_', ' ').title()} — Complete Technical Guide\n\n"
    textbook_content += "\n\n---\n\n".join(chapters)

    out_path = os.path.join(out_dir, f"{topic}_textbook.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(textbook_content)

    print(f"[CURRICULUM] Textbook written: {out_path}")
    return out_path


def _build_plan(topic: str, grit: list, out_dir: str) -> str:
    skill_summary = _summarize_skills(grit, topic)

    prompt = CURRICULUM_PLAN_PROMPT.format(topic=topic, skill_summary=skill_summary)
    result = call_json(RESEARCHER_MODEL, prompt, temperature=0.5, timeout=60)

    if not isinstance(result, dict):
        result = _fallback_plan(topic)

    out_path = os.path.join(out_dir, f"{topic}_curriculum.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"[CURRICULUM] Plan written: {out_path}")
    return out_path


# -------------------------------------------------------------------------
# Helpers

def _find_excerpt(topic_path: str, vault_files: list, grit_chunk: list, lexicon: list, file_texts: dict = None) -> str:
    """Find a vault file excerpt relevant to this grit chunk."""
    grit_terms = set()
    for item in grit_chunk:
        for tool in item.get("tools", []):
            grit_terms.update(tool.lower().split())
        task_words = item.get("task", "").lower().split()
        grit_terms.update(task_words[:5])

    best_score = 0
    best_excerpt = ""
    for fname in vault_files:
        try:
            text = file_texts[fname] if file_texts and fname in file_texts else open(
                os.path.join(topic_path, fname), "r", encoding="utf-8", errors="ignore").read()
            score, _ = analyze_technical_density(text, list(grit_terms))
            if score > best_score:
                best_score = score
                best_excerpt = text[:2000]
        except Exception:
            continue
    return best_excerpt


def _infer_chapter_topic(grit_chunk: list, index: int, topic: str) -> str:
    if not grit_chunk:
        return f"{topic.replace('_', ' ').title()} Fundamentals"
    tasks = [item.get("task", "") for item in grit_chunk if item.get("task")]
    if tasks:
        # Use first task words as chapter hint
        words = tasks[0].split()[:4]
        return " ".join(w.title() for w in words)
    return f"{topic.replace('_', ' ').title()} — Part {index + 1}"


def _summarize_skills(grit: list, topic: str) -> str:
    if not grit:
        return f"General {topic} skills and techniques"
    tasks = [item.get("task", "") for item in grit[:20] if item.get("task")]
    tools = set()
    for item in grit:
        tools.update(item.get("tools", []))
    summary = f"Tasks covered:\n" + "\n".join(f"- {t}" for t in tasks)
    if tools:
        summary += f"\n\nTools/materials mentioned:\n" + "\n".join(f"- {t}" for t in list(tools)[:20])
    return summary


def _fallback_plan(topic: str) -> dict:
    return {
        "topic": topic,
        "timeline_weeks": 24,
        "overview": f"A progressive self-study path for {topic}, moving from foundational concepts through professional-level skills.",
        "milestones": [
            {"level": "beginner", "weeks": "1-6", "focus": "Fundamentals and basic technique",
             "skills": ["safety procedures", "basic tool use", "foundational techniques"],
             "projects": [{"name": "Starter project", "description": f"A simple {topic} project to practice fundamentals.", "skills_practiced": ["basic technique", "safety"]}]},
            {"level": "intermediate", "weeks": "7-16", "focus": "Refinement and more complex work",
             "skills": ["intermediate techniques", "material selection", "quality assessment"],
             "projects": [{"name": "Intermediate project", "description": f"A multi-step {topic} project requiring planning.", "skills_practiced": ["planning", "intermediate technique"]}]},
            {"level": "advanced", "weeks": "17-24", "focus": "Advanced methods and independent projects",
             "skills": ["advanced techniques", "troubleshooting", "project design"],
             "projects": [{"name": "Capstone project", "description": f"A complex {topic} project demonstrating full competency.", "skills_practiced": ["all skills combined"]}]}
        ]
    }
