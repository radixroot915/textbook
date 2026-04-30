import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VAULT_ROOT, RESEARCHER_MODEL
from llm.ollama_client import call_json
from llm.prompts import MATERIALS_PROMPT


def build_materials_list(topic: str, grit: list) -> str:
    out_dir = os.path.join(VAULT_ROOT, topic, "curriculum")
    os.makedirs(out_dir, exist_ok=True)

    tools_mentioned = _extract_tools(grit)
    prompt = MATERIALS_PROMPT.format(
        topic=topic,
        tools_mentioned="\n".join(f"- {t}" for t in tools_mentioned)
    )

    result = call_json(RESEARCHER_MODEL, prompt, temperature=0.4, timeout=180)
    if not isinstance(result, dict):
        result = _fallback_materials(topic, tools_mentioned)

    out_path = os.path.join(out_dir, f"{topic}_materials.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"[MATERIALS] Materials list written: {out_path}")
    return out_path


def _extract_tools(grit: list) -> list:
    tools = set()
    for item in grit:
        for tool in item.get("tools", []):
            if tool and len(tool) > 2:
                tools.add(tool)
    return sorted(tools)


def _fallback_materials(topic: str, tools: list) -> dict:
    essential = [{"name": t, "purpose": "Required for core procedures", "budget_option": "Buy used or from local suppliers", "est_cost": "Varies"} for t in tools[:8]]
    return {
        "topic": topic,
        "notes": f"Start with the minimum essential tools. Buy used where possible for significant savings. Add tools as your skill level grows.",
        "tools": {
            "essential": essential or [{"name": f"Basic {topic} toolkit", "purpose": "Core operations", "budget_option": "Starter kit from hardware store", "est_cost": "$50-150"}],
            "upgrade_later": [],
            "luxury": []
        },
        "consumables": [],
        "workspace": [{"name": "Adequate workspace", "notes": f"Minimum space for safe {topic} practice with ventilation if required"}]
    }
