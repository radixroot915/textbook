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

DEEP_DIVE_FRONTIER_PROMPT = """[INST]The collection on "{topic}" already covers the foundational ground:
{covered_nodes}

Lexicon (confirmed domain terms):
{lexicon}

Now generate the DEEP-DIVE research frontier. Identify 5-8 NEW search terms specifically targeting INTERMEDIATE-to-ADVANCED depth that's missing — content a practitioner who already knows the basics would seek to truly master the craft.

Target these dimensions:
- **Material science**: chemistry, physics, fiber/grain structure, molecular behavior, why-it-works content
- **Advanced techniques**: specialist methods, expert variations, restoration / conservation, edge cases
- **Comparative methods**: A vs B, when to use which, evolution of techniques over time
- **Failure analysis**: how things go wrong at the materials level, root-cause investigation
- **Cross-disciplinary connections**: where this craft borrows from or feeds other fields

Each term must be a concrete, searchable phrase (3–6 words) that would land actual technical content — not vague concepts. Examples of GOOD terms:
- "collagen fiber orientation tanning"
- "advanced saddle stitch tension control"
- "vegetable vs chrome tanning chemistry"
- "leather conservation restoration techniques"

Avoid generic terms like "advanced techniques" or "materials science" without {topic}-specific framing.

Output ONLY a JSON array of strings. No prose, no markdown.[/INST]"""


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

CHAPTER_PLAN_PROMPT = """[INST]You are organizing a comprehensive technical textbook about "{topic}".

CRITICAL: Every chapter must be about "{topic}" SPECIFICALLY. The title must contain the word "{topic}" or a directly-related domain term — never a generic word like "Joinery" or "Finishing" without qualifier. Do not include chapters about unrelated trades or skills. If "{topic}" has its own terms for connections/finishing/joining, use those (e.g. for leatherworking use "Stitching and Lacing" not "Joinery").

Domain lexicon (terms confirmed present in the source material):
{lexicon}

Skill areas and procedures extracted from source material:
{grit_tasks}

Headings found in the collected source documents:
{source_headings}

Design a complete chapter plan for a textbook that covers "{topic}" from beginner to advanced level. Aim for 8-12 chapters.

REQUIRED ORDERING — chapters must be ordered so a learner can follow them sequentially:
1. Introduction to {topic} (history, scope, why this matters)
2. Safety and Hazards specific to {topic} — ALWAYS the second chapter, right after the introduction; never buried later
3. Materials and Types of {topic} — what the practitioner works with, before tools that work on it
4. Tools and Equipment for {topic} — tools come before the techniques that use them
5. Foundational Techniques (basic cuts/handling/preparation)
6+. Intermediate Techniques, Specialized Methods (in order of difficulty)
N-1. Hands-on Projects (apply everything; place LATE, never near the front)
N. Care, Maintenance, and Troubleshooting

Return a JSON array where each element is:
{{"title": "<{topic}-specific chapter title>", "topics": ["<specific term this chapter must cover>", ...]}}

Each "topics" list should contain 5-8 concrete, searchable terms drawn from the lexicon and source headings above.

Output ONLY the JSON array. No markdown fences. No explanation.[/INST]"""


DEEP_CHAPTER_PROMPT = """[INST]Write a comprehensive technical textbook chapter about "{chapter_title}" as part of a complete guide to "{topic}".

This chapter must cover these specific topics:
{expected_topics}

SOURCE MATERIAL — excerpts from collected reference documents:
{source_passages}

EXTRACTED PROCEDURES (grit items):
{grit_items}

Instructions:
- Write substantial, information-dense content. Aim for 800-1200 words.
- Draw directly from the source passages above — include specific values, measurements, process names, and technical terms that appear in the sources.
- Do not pad with generic statements. If a source gives a specific number or procedure, use it.
- Structure with markdown headers (##, ###). Use tables or lists where appropriate.
- Include: background/theory, practical procedures with specific values, safety considerations where relevant, and key takeaways.
- Write for a practitioner who wants real, usable knowledge — not a survey.
- Do NOT include any brand names, manufacturer names, or specific model numbers. Use generic tool and equipment names only.
- Do NOT wrap output in code fences or markdown blocks. Output plain markdown prose only.

PEDAGOGICAL STRUCTURE — every chapter must follow this opening:
- Start with a `## Learning Outcomes` section containing 3-5 bullets, each beginning with "By the end of this chapter, you will be able to [verb] [object]". Use concrete action verbs (identify, select, apply, perform, troubleshoot) — not vague verbs like "understand" or "appreciate".
- Each procedure step should briefly note WHY that step matters, not just WHAT to do.

CALL-OUT FORMATTING — render special content as markdown blockquotes so they stand out visually:
- Safety warnings: `> **⚠ Safety:** [warning text]`
- Tips / efficiency notes: `> **💡 Tip:** [tip text]`
- Common mistakes / troubleshooting: `> **⚠ Common mistake:** [description and how to avoid]`
Do NOT bury safety and mistake content in plain prose paragraphs — always use the blockquote form.

STRICT GROUNDING RULE: Do not include any specific number, temperature, measurement, standard identifier, alloy composition, or named procedure that does not appear in the source passages above. If the sources do not provide a specific value, write in general terms (e.g. "temperatures vary by material" rather than inventing a figure). It is better to write accurate general statements than specific invented ones. If the source material is thin for a topic, say so briefly and move on — do not fill gaps from outside knowledge.

CRITICAL TOPIC ANCHORING: This chapter is part of a textbook about "{topic}". Every example, tool, material, and technique you mention must be specifically about "{topic}" — NOT a related but different craft. If the chapter title contains a generic word (e.g. "Joinery" or "Finishing"), interpret it strictly through the lens of {topic} (e.g. "Joinery" for leatherworking means stitching/lacing/riveting leather, NOT wood joints; "Finishing" for blacksmithing means heat treatment and surface treatment of metal, NOT wood finishes). If the source passages don't cover this specific sub-topic for {topic}, write a brief honest acknowledgement instead of importing analogous content from a different craft.

TABLE FORMATTING: Do NOT use markdown tables (`| col | col |`) anywhere. Render every tabular-style content as bulleted lists with bold lead terms instead. Examples:
- `- **Cracks/splits** — Caused by over-drying. *Prevention:* store in 50–70% humidity.`
- For multi-attribute comparisons: `- **Vegetable-tanned:** *Hardness:* firm. *Best for:* tooling, belts. *Care:* condition with neatsfoot oil.`
This applies even if the data feels "tabular" — tables consistently misalign and render badly. Bullets always look clean.

CONCLUSIONS: Every chapter must end with a `## Summary` section: 3-5 bulleted takeaways the reader should remember. Place this AFTER the body content and AFTER any Worked Examples, but BEFORE Try This and Review Questions. Do NOT scatter "key takeaways" mid-chapter.

Output ONLY the markdown chapter content, starting after the chapter title. No preamble, no code fences.[/INST]"""


CLAIM_EXTRACT_PROMPT = """[INST]Extract specific verifiable factual claims from the following {topic} textbook chapter.

CHAPTER: {chapter_title}

TEXT:
{chapter_text}

Extract ONLY claims that contain specific, checkable facts: numbers, temperatures, measurements, named procedures, material specifications, safety thresholds, tool settings, or step-by-step instructions. Do NOT extract general statements like "welding is important" or "safety is critical".

Return a JSON array. Each element:
{{"claim": "<the specific claim sentence>", "type": "<specification|procedure|safety|material>", "keywords": ["<2-4 specific terms or values from the claim that would appear in a source document>"]}}

Aim for 5-15 claims. Output ONLY valid JSON.[/INST]"""


CLAIM_DRIVEN_CHAPTER_PROMPT = """[INST]Write a textbook chapter about "{chapter_title}" for a {topic} guide. Be a teacher — explain, synthesize, draw connections. The reader needs to walk away understanding the subject, not just reading bullet points.

TWO RULES OPERATING TOGETHER — anchor for prose, railroad for specifics:

**RAILROAD RULE (zero freedom):** Any of the following MUST appear verbatim or near-verbatim in a claim from the list below — no exceptions:
  - Numeric values with units (temperatures, times, percentages, lengths, weights, pressures)
  - Year dates, historical periods, century markers
  - Brand names, model numbers, product names
  - Standards / specification identifiers (ASTM-XYZ, AISI-XXX, etc.)
  - Named procedures, named techniques (capitalized proper-noun-style names)
  - Exact tool sizes / grade numbers / part specs

If the claims do NOT contain a specific you want to state, you have two choices: (a) write in general terms ("time varies with leather thickness", "temperatures depend on the tanning method") or (b) omit the topic. You may NOT write "approximately 30°C" or "around 5%" or "in the 19th century" without explicit claim support — even if you "know" it from training, even if it's standard, even if other authors say it. Inventing plausible-sounding specifics is the failure mode this rule exists to prevent.

**ANCHOR RULE (full freedom):** For everything that is NOT a specific value — explanations, principles, analogies, "why it works", connective prose, comparisons between concepts, pedagogical structure, synthesis across multiple claims — you have full creative freedom. Be a teacher. Explain. Draw connections. The claims anchor your facts; your prose explains them.

Claims tagged [low-trust source] are community practice — hedge them ("according to common practice", "some practitioners use", "anecdotally", not "the standard is"). Never elevate a low-trust claim to confident statement. A low-trust claim MUST NOT be the sole basis for any factual specific (number, brand, technique name, procedure step) — if no high-trust claim corroborates it, omit the specific rather than state it on forum/web evidence alone.

**ATTRIBUTION RULE (mandatory):** Every sentence that states a specific value — number+unit, year, century, named technique, standard ID — MUST be tagged inline with the source claim ID in square brackets, e.g. `[C3]` at the end of the relevant phrase. If multiple claims support the same statement, tag them together: `... two needles [C2, C7] passing through ...` or `... in 1922 [C12] [C18]`. Untagged sentences that contain specifics will be flagged downstream as unverifiable. Connective prose without specifics does NOT need tags. The markers will be stripped before the reader sees the chapter — they exist only so the fact-checker can verify by direct lookup instead of guessing.

VERIFIED CLAIMS (numbered for attribution — cite as `[C{{N}}]`):
{claims_block}

Chapter sub-topics to address (the reader's takeaways):
{expected_topics}

PEDAGOGICAL STRUCTURE — every chapter must follow this opening:
- Start with a `## Learning Outcomes` section: 3-5 bullets, each beginning "By the end of this chapter, you will be able to [verb] [object]". Use concrete action verbs (identify, select, apply, perform, troubleshoot).

CONTENT RULES:
- 700-1100 words. Accurate before long. Better to under-cover a topic than invent facts.
- Group claims into logical sub-sections with `###` headings.
- For multi-step procedures, write numbered steps. Each step may briefly note WHY it matters (use existing claim information).
- Use bulleted lists with bold lead terms for material/tool/spec catalogs (no markdown tables).
- Render safety as `> **⚠ Safety:** ...` blockquotes.
- Render tips as `> **💡 Tip:** ...` blockquotes.
- End with `## Summary` — 3-5 bulleted takeaways.
- Do NOT include any brand names, manufacturer names, or model numbers.
- Do NOT wrap output in code fences.

OUTPUT: ONLY the markdown chapter content. No preamble like "Here's the chapter:", no commentary.[/INST]"""


PEDAGOGY_ENRICH_PROMPT = """[INST]You are adding pedagogical scaffolding to a textbook chapter that is factually accurate but reads like a reference document. Do NOT add new facts, claims, or specifics that are not already in the chapter.

CHAPTER TITLE: {chapter_title}
TOPIC: {topic}

CHAPTER CONTENT:
{chapter_content}

Your task — return the chapter with these additions, preserving all existing content:

1. RATIONALES: Where the chapter contains numbered procedures or step lists, add ONE short rationale sentence per step explaining why that step matters or what failure mode it prevents. Use existing chapter information — do not invent new specifics. Format as: "Step 3: Apply the wax in thin coats. *Thin coats prevent uneven buildup and cracking as the leather flexes.*"

2. TRY THIS — append at the end of the chapter, before any "Key Takeaways" or "Review Questions" section, a markdown block:
```
> **🔧 Try This:** [One concrete 5–15 minute exercise a reader can do at home with scrap material or basic tools to internalize this chapter's main skill. Be specific about what they'll do and how to tell if they succeeded.]
```

3. REVIEW QUESTIONS — append at chapter end (after Summary if present), with an Answer Key immediately following:
```
## Review Questions

1. **Recall:** [a factual question testing knowledge of a key term or concept from the chapter]
2. **Troubleshoot:** [a scenario-based question — "You've done X and Y happens — what likely went wrong?"]
3. **Apply:** [a synthesis question asking the reader to choose between options or design a small workflow for a specific use case]

### Answer Key

1. [1-2 sentence answer drawn from chapter content]
2. [1-2 sentence troubleshooting answer]
3. [1-2 sentence answer naming the right approach and why]
```

4. PROJECT WALKTHROUGH FORMATTING: If the chapter contains a beginner project / hands-on exercise, restructure it as:
   - **Tools needed:** (bulleted list)
   - **Materials needed:** (bulleted list with quantities/specs)
   - **Time estimate:** (rough range)
   - **Numbered steps** (each with a one-line rationale, as in rule 1)
   - **Common failure:** (one short blockquote noting the most likely mistake)
   - **Success check:** (how the finished piece should look/feel)
   Apply this template to ANY project, exercise, or walkthrough section.

RULES:
- Do NOT invent new facts, measurements, temperatures, or procedures.
- Use information already present in the chapter.
- Preserve all existing structure (headings, tables, lists, blockquotes).
- Do NOT wrap output in code fences. Output plain markdown.
- If the chapter is purely descriptive (history, theory) without procedures, you may skip the rationale step but ALWAYS add Try This and Review Questions.

Output ONLY the enriched chapter markdown.[/INST]"""


REGROUND_CHAPTER_PROMPT = """[INST]Rewrite the following textbook chapter about "{chapter_title}". The previous draft contained the specific unsupported claims listed below — these must be removed or rewritten in general terms.

TOPIC: {topic}

SOURCE PASSAGES (these are the ONLY permitted source of specific facts):
{source_passages}

FLAGGED CLAIMS — the previous draft contained these specific sentences that could not be verified against any source. Remove them entirely or rewrite without the unverified specifics:
{flagged_claims}

PREVIOUS DRAFT (for structure reference only — do not copy unsupported claims):
{chapter_draft}

REWRITE RULES:
1. Every specific value, measurement, temperature, percentage, standard, or named procedure you write MUST appear in the source passages above. If it is not there, do not write it.
2. The flagged claims above are KNOWN to be unsupported — do not reproduce them. Either omit the topic, or write a general statement without specifics.
3. Preserve the chapter structure (headings, sections) from the draft where it makes sense.
4. PRESERVE WORKED EXAMPLES from the source passages — if a source describes a complete procedure with steps, materials, and concrete actions, KEEP that example in the chapter even if some individual claims within it can't be atomically verified. A teaching textbook needs concrete examples; a chapter that lists tools without showing them used is useless.
5. Where two source passages give different specific values for the same thing (e.g. different soak times), pick ONE and note the variation in general terms — do not present contradictory specifics.
6. Remove ALL brand names, model numbers, and manufacturer names.
7. Do NOT wrap output in code fences. Output plain markdown prose only.
8. Aim for 800-1200 words. Accuracy AND completeness — a chapter of 500 words that just names tools is not acceptable. Include at least one concrete worked example or step-by-step procedure drawn from the sources.

Output ONLY the rewritten chapter markdown. No preamble, no explanation.[/INST]"""


EDIT_PASS_PROMPT = """[INST]You are a technical editor reviewing a chapter of a {topic} textbook.

CHAPTER TITLE: {chapter_title}

CHAPTER DRAFT:
{chapter_draft}

KNOWN ISSUES TO FIX:
{issues}

Your task — edit the chapter in place. Specifically:
1. Correct any technical inaccuracies you can identify.
2. Replace vague or padded sentences with specific, concrete information.
3. Ensure all procedures are numbered and include specific values (temperatures, amps, distances, times) where appropriate.
4. Remove any repeated content or redundant sentences.
5. Improve readability: short paragraphs, active voice, clear topic sentences.
6. Do NOT add new topics not already present in the draft — stay in scope.
7. Remove ALL brand names, manufacturer names, and model numbers — replace with generic equipment names.
8. Do NOT wrap output in code fences or markdown blocks. Output plain markdown prose only.

Output ONLY the improved chapter markdown, starting after the chapter title. No preamble, no code fences.[/INST]"""


TOOL_EXTRACT_PROMPT = """[INST]Read the following text and extract every distinct hand tool, power tool, or measuring/layout tool mentioned.

TEXT:
{text}

Return ONLY a JSON array of tool name strings. Use the most common generic name for each tool (e.g. "claw hammer", "marking gauge", "block plane"). No brands. No materials. No consumables. If no tools are mentioned, return [].

Output ONLY valid JSON.[/INST]"""


TOOL_ENTRY_PROMPT = """[INST]Write a concise reference entry for the following tool: "{tool_name}"

Source passages for context:
{passages}

Write the entry in this exact format — no extra sections, no brand names, no model numbers, no prices:

**What it is:** One sentence describing the tool.

**What it does:** One or two sentences on its primary function and when you reach for it.

**Variants:** A short list of the main generic types (e.g. rip, crosscut, back saw — not brand names). If there are no meaningful variants, write "None significant."

**Basic use:** 3-5 short sentences covering how to hold it, set it up, and use it correctly. Be practical and specific without referencing any particular product.

**Care:** 2-3 sentences on keeping it functional — cleaning, storage, sharpening or adjustment if applicable.

Output ONLY the entry text, starting with "**What it is:**". No title, no headers above it.[/INST]"""


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

