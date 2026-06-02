import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VAULT_ROOT, RESEARCHER_MODEL
from llm.ollama_client import call_json
from llm.prompts import CURRICULUM_PLAN_PROMPT
from curriculum.textbook_compiler import compile_textbook


def build_curriculum(topic: str, lexicon: list, grit: list):
    """Assemble the full curriculum package from harvested content and grit."""
    out_dir = os.path.join(VAULT_ROOT, topic, "curriculum")
    os.makedirs(out_dir, exist_ok=True)

    # Tool library uses a reference index compiler, not the narrative textbook path
    if topic == "tool_library":
        from curriculum.tool_library_compiler import compile_tool_library
        md_path, index_path = compile_tool_library(lexicon, grit)
        print(f"[CURRICULUM] Tool reference: {md_path}")
        print(f"[CURRICULUM] Tool index: {index_path}")
        return {"textbook": md_path, "gaps": "", "plan": "", "gap_nodes": []}

    print(f"[CURRICULUM] Building textbook (deep compile)...")
    textbook, gaps, gap_nodes = compile_textbook(topic, lexicon, grit)
    print(f"[CURRICULUM] Textbook: {textbook}")
    print(f"[CURRICULUM] Gap report: {gaps} ({len(gap_nodes)} nodes for re-harvest)")

    # Inject tool library citations if index exists
    from curriculum.cross_referencer import load_tool_index, cross_reference
    tool_names = load_tool_index()
    if tool_names and textbook and os.path.exists(textbook):
        print(f"[CURRICULUM] Cross-referencing against {len(tool_names)} tool library entries...")
        cross_reference(textbook, tool_names)

    print(f"[CURRICULUM] Building milestone plan...")
    plan = _build_plan(topic, grit, out_dir)

    return {"textbook": textbook, "gaps": gaps, "plan": plan, "gap_nodes": gap_nodes}



def _build_plan(topic: str, grit: list, out_dir: str) -> str:
    skill_summary = _summarize_skills(grit, topic)

    prompt = CURRICULUM_PLAN_PROMPT.format(topic=topic, skill_summary=skill_summary)
    result = call_json(RESEARCHER_MODEL, prompt, temperature=0.5, timeout=240)

    if not isinstance(result, dict):
        result = _fallback_plan(topic)

    out_path = os.path.join(out_dir, f"{topic}_curriculum.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"[CURRICULUM] Plan written: {out_path}")
    return out_path


# -------------------------------------------------------------------------
# Helpers


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
