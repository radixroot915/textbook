# All LLM prompt templates. Llama3 format: [INST]...[/INST]
# TinyLlama format: <|system|>...<|user|>...<|assistant|>

SEED_PACKET_PROMPT = """[INST]Build a research seed for "{topic}".

Return a JSON object with two keys:
- "nodes": 10 strings — 6 topic-specific subtopics AND 4 foundational technique names that underpin this craft. Technique nodes should be searchable as standalone topics (e.g. for knife sheaths: both "fold-over sheath pattern construction" AND "saddle stitching leather", "leather skiving method", "edge beveling and burnishing"). Be concrete and specific — not "basics" or "overview".
- "lexicon": 12 domain-specific practitioner terms (real vocabulary used by craftspeople, not generic words like "technical", "standard", "process")

Output ONLY valid JSON. No markdown fences.[/INST]"""

FRONTIER_EXPANSION_PROMPT = """[INST]You are researching "{topic}" and found a document about "{current_node}".

Here is the start of the document (may contain a table of contents or chapter list):
---
{text_sample}
---

Current research nodes already in the frontier:
{existing_nodes}

Identify up to 5 NEW specific subtopics that appear in this document but are NOT in the frontier list above. These should be concrete technical subtopics worth searching for independently.

Output ONLY a JSON array of strings. If no new subtopics, output [].[/INST]"""

GAP_ANALYSIS_PROMPT = """[INST]You are building a complete offline knowledge base about "{topic}".

Documents collected so far cover these subtopics:
{covered_nodes}

Lexicon (domain terms confirmed present in collected material):
{lexicon}

Identify 3-5 important technical subtopics or skill areas that are MISSING from the collection above. Focus on gaps that a practitioner would need for real competency.

Output ONLY a JSON array of strings.[/INST]"""


TEXTBOOK_CHAPTER_PROMPT = """[INST]Write a technical textbook chapter about "{chapter_topic}" as part of a comprehensive guide to "{topic}".

Use this source material and extracted procedures:

SOURCE EXCERPTS:
{source_excerpts}

EXTRACTED PROCEDURES:
{grit_items}

Write a well-structured chapter with:
1. A brief theory/background section explaining the underlying principles
2. Step-by-step procedures with specific values where available
3. A safety section covering key hazards and controls
4. A summary of key points

Write in clear, direct technical prose. Use markdown headers (##, ###). Be specific — include actual values, measurements, and tool names from the source material. Aim for 600-900 words.

Output ONLY the markdown chapter content.[/INST]"""

CURRICULUM_PLAN_PROMPT = """[INST]Create a structured learning curriculum for "{topic}" as a self-taught skill.

Based on these skill areas and procedures:
{skill_summary}

Generate a JSON curriculum with this structure:
{{
  "topic": "{topic}",
  "timeline_weeks": <total weeks as integer>,
  "overview": "<2-3 sentence description of the learning journey>",
  "milestones": [
    {{
      "level": "beginner",
      "weeks": "<range like 1-6>",
      "focus": "<what the learner is building in this phase>",
      "skills": ["<specific skill 1>", "<specific skill 2>", ...],
      "projects": [
        {{"name": "<project name>", "description": "<1-2 sentences>", "skills_practiced": ["<skill>", ...]}},
        {{"name": "<project name>", "description": "<1-2 sentences>", "skills_practiced": ["<skill>", ...]}}
      ]
    }},
    {{
      "level": "intermediate",
      "weeks": "<range>",
      "focus": "...",
      "skills": [...],
      "projects": [...]
    }},
    {{
      "level": "advanced",
      "weeks": "<range>",
      "focus": "...",
      "skills": [...],
      "projects": [...]
    }}
  ]
}}

Make projects concrete and achievable. Beginner projects should be completable in a single session. Advanced projects should require multiple sessions and multiple skill combinations.

Output ONLY valid JSON.[/INST]"""

MATERIALS_PROMPT = """[INST]Create a complete tools and materials list for learning "{topic}" as a beginner on a budget.

Based on these procedures and tools mentioned in source material:
{tools_mentioned}

Generate a JSON object with this structure:
{{
  "topic": "{topic}",
  "notes": "<1-2 sentences about sourcing strategy, e.g. buy used, start minimal>",
  "tools": {{
    "essential": [{{"name": "<tool>", "purpose": "<what it does>", "budget_option": "<where/how to get cheaply>", "est_cost": "<price range>"}}],
    "upgrade_later": [{{"name": "<tool>", "purpose": "...", "est_cost": "..."}}],
    "luxury": [{{"name": "<tool>", "purpose": "...", "est_cost": "..."}}]
  }},
  "consumables": [{{"name": "<material>", "purpose": "...", "est_cost": "<per unit/quantity>"}}],
  "workspace": [{{"name": "<requirement>", "notes": "<minimum viable setup>"}}]
}}

Focus on budget-friendly real options. Essential = cannot start without. Upgrade later = improves quality/speed. Luxury = professional grade.

Output ONLY valid JSON.[/INST]"""

VIDEO_QUERIES_PROMPT = """[INST]Generate YouTube search queries for learning "{topic}" at three skill levels.

Based on these skill areas: {skill_areas}

For each skill level (beginner, intermediate, advanced), generate 4 specific search queries that would find the most useful how-to tutorial videos on YouTube.

Queries should be:
- Specific enough to find focused tutorials (not just "learn {topic}")
- Targeted at the skill level (beginner: fundamentals and setup; intermediate: technique refinement; advanced: complex projects and professional methods)
- Varied across different aspects of the skill

Output a JSON object:
{{
  "beginner": ["<query 1>", "<query 2>", "<query 3>", "<query 4>"],
  "intermediate": ["<query 1>", "<query 2>", "<query 3>", "<query 4>"],
  "advanced": ["<query 1>", "<query 2>", "<query 3>", "<query 4>"]
}}

Output ONLY valid JSON.[/INST]"""

