# Harvester — Project Context

Automated knowledge base builder for craft/trade skills. Given a topic (e.g. "stair construction"), it scrapes public domain sources, deduplicates content, and synthesizes a curriculum using a local LLM.

---

## Stack
- **Python 3.9+** — `requests`, `beautifulsoup4`
- **Ollama (local)** — `llama3.2:3b` at `http://localhost:11434`
- **Run script** — `run.ps1` (requires PowerShell 7 / `pwsh`)

## How to run
```powershell
pwsh -File D:\harvester\run.ps1 -Topic "your topic"
# or from inside pwsh:
.\run.ps1 -Topic "your topic" -MinFiles 100 -MaxIterations 5
```
Ollama must be running first: `ollama serve`

---

## File Map
| File | Role |
|------|------|
| `main.py` | Entry point; health-checks Ollama, starts Coordinator |
| `config.py` | All config: paths, model names, thresholds |
| `run.ps1` | PowerShell runner: checks Python, deps, Ollama, then calls main.py |
| `cleanup.py` | Post-run dedup: removes near-duplicate vault files |
| `bouncer.py` | SimHash deduplication (64-bit, hamming dist ≤ 4) |
| `organizer.py` | Density scoring — counts lexicon hits in document |
| `selector.py` | HTML detector + letter-ratio filter for OCR quality |
| `agents/coordinator.py` | Orchestrates 6 async workers across all sources |
| `agents/researcher_agent.py` | Manages knowledge map, frontier expansion, grit extraction |
| `agents/base_source_agent.py` | Base class: fetch, validate, dedup, save |
| `agents/gutenberg_agent.py` | Project Gutenberg (Gutendex API) |
| `agents/openlibrary_agent.py` | Open Library + archive.org DJVU |
| `agents/archive_agent.py` | Internet Archive full-text/OCR search |
| `agents/wikisource_agent.py` | Wikisource MediaWiki API |
| `agents/wikibooks_agent.py` | Wikibooks (same as wikisource, different URL) |
| `agents/stackexchange_agent.py` | Crafts/DIY/woodworking StackExchange |
| `llm/ollama_client.py` | HTTP client for Ollama; `call()`, `call_json()`, `health_check()` |
| `llm/prompts.py` | All LLM prompt templates (Llama3 [INST] format) |
| `curriculum/builder.py` | Generates textbook + milestone plan markdown |
| `curriculum/materials.py` | Extracts tool/consumable lists via LLM |
| `curriculum/video_finder.py` | YouTube query gen + Instructables/WikiHow scraping |

---

## Data Flow (short version)
1. `ResearcherAgent` seeds nodes + lexicon from LLM
2. 6 async workers pull (node, source) pairs and scrape
3. Each doc: density check → HTML filter → length check → SimHash dedup → save to `vault/{topic}/`
4. After each iteration: frontier expanded from saved docs, gaps identified
5. Once `min_files` reached: grit extracted, curriculum built, outputs written

## Outputs (in `vault/{topic}/curriculum/`)
- `{topic}_textbook.md` — LLM-generated multi-chapter guide
- `{topic}_curriculum.json` — Beginner/intermediate/advanced milestone plan
- `{topic}_materials.json` — Tools, consumables, workspace requirements
- `{topic}_videos.json` — YouTube URLs + Instructables/WikiHow links
- `{topic}_grit.json` — Raw extracted procedures

## Key config values (`config.py`)
| Setting | Value |
|---------|-------|
| Model (all roles) | `llama3.2:3b` |
| Concurrency | 6 workers |
| Min text length | 2500 bytes |
| SimHash threshold | 4 (hamming distance) |
| Max candidates/source | 80 |
| LLM timeout | 180s (ollama_client.py) |

## State files
- `knowledge_map.json` — per-topic nodes, lexicon, frontier scores
- `fingerprints.txt` — SimHash values for all saved docs (max 50k)

## External sources hit
Gutendex, Open Library, archive.org, Wikisource, Wikibooks, StackExchange (crafts/diy/woodworking), Instructables, WikiHow

---

## Known issues / recent changes
- `run.ps1` requires PowerShell 7 (`pwsh`) — uses `?.` null-conditional operator
- Ollama timeout raised from 60s → 180s (model slow to load on first call)
