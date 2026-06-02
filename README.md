# harvester

A multi-agent pipeline that crawls open knowledge sources (Wikipedia, Wikibooks,
Project Gutenberg, Internet Archive, Stack Exchange, OSHA, DTIC, LibreTexts,
CORE, OpenLibrary, HathiTrust, YouTube transcripts, …), filters the harvested
text for topical and instructional density, deduplicates it, and compiles the
result into a textbook-style curriculum for a given topic.

## Requirements

You need **one** LLM backend — pick A or B:

**A. Local — Ollama** (default, no API key, runs offline)
- Install [Ollama](https://ollama.com/download) and start it (`ollama serve`).
- Pull a model and set its name in env, e.g.:
  ```bash
  ollama pull llama3.1:8b
  export LLM_MODEL=llama3.1:8b   # or edit RESEARCHER_MODEL in config.py
  ```

**B. Remote — any OpenAI-compatible API** (OpenAI, Groq, Together, OpenRouter,
Ollama Cloud, vLLM, LM Studio, …)
```bash
export LLM_PROVIDER=openai
export LLM_API_KEY=sk-...
export LLM_API_BASE=https://api.openai.com/v1   # or your provider's base
export LLM_MODEL=gpt-4o-mini                    # or any model the provider serves
```

Without a working LLM backend, non-LLM crawler agents (Wikipedia, Gutenberg,
Archive, Stack Exchange, …) still run, but the topic-bootstrap, gap-node
expansion, curriculum planner, fact checker, glossary, and quality gate are
skipped.

Other requirements:
- **Python 3.11+** (developed against 3.14)
- See `requirements.txt` for Python deps.

## Setup

```bash
git clone https://github.com/mostlyyhomeless-design/textbook.git harvester
cd harvester
python -m venv .venv
# Windows:  .venv\Scripts\Activate.ps1
# Unix:     source .venv/bin/activate
pip install -r requirements.txt
```

Optional environment variables:

| Variable          | Purpose                                                      |
|-------------------|--------------------------------------------------------------|
| `LLM_PROVIDER`    | `ollama` (default) or `openai` for any OpenAI-compatible API |
| `LLM_API_KEY`     | API key when `LLM_PROVIDER=openai`                           |
| `LLM_API_BASE`    | API base URL (default `https://api.openai.com/v1`)           |
| `LLM_MODEL`       | Model name — overrides `RESEARCHER_MODEL`/`QUERY_MODEL`      |
| `OLLAMA_BASE`     | Ollama generate endpoint (default `http://localhost:11434/api/generate`) |
| `OLLAMA_TAGS`     | Ollama tags endpoint                                         |
| `HARVESTER_BASE`  | Override the base directory (vault, fingerprints, logs land here) |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key (enables YouTube transcript agent)   |

## Running

Windows (PowerShell):

```powershell
.\run.ps1 -Topic "leatherworking" -MinFiles 100 -MaxIterations 5
```

macOS / Linux:

```bash
./run.sh leatherworking 100 5
```

Direct invocation (no wrapper):

```bash
python main.py "<topic>" [min_files] [max_iterations] [max_cycles] [--skip-compile]
```

Output lands in `vault/<topic>/`, with the compiled textbook under
`vault/<topic>/curriculum/`.

## Repo layout

```
agents/        per-source crawlers, all subclassing BaseSourceAgent
curriculum/    textbook compiler, fact-checker, glossary, quality gate, export
core/          shared schemas, benchmarks, probes
llm/           Ollama client + prompts
tests/         pytest suite (run with `pytest`)
main.py        entry point — orchestrates harvest cycles
workflow.py    higher-level CLI subcommands
config.py      tunables (concurrency, density thresholds, model names, …)
```

## Notes

- Runtime artefacts (`vault/`, `runs/`, `*_run.log`, `fingerprints_*.txt`,
  per-topic JSON state) are gitignored — do not commit them.
- Tests assume the optional deps in `requirements.txt` are installed.
