# harvester

A multi-agent pipeline that crawls open knowledge sources (Wikipedia, Wikibooks,
Project Gutenberg, Internet Archive, Stack Exchange, OSHA, DTIC, LibreTexts,
CORE, OpenLibrary, HathiTrust, YouTube transcripts, …), filters the harvested
text for topical and instructional density, deduplicates it, and compiles the
result into a textbook-style curriculum for a given topic.

## Requirements

- **Python 3.11+** (developed against 3.14)
- **[Ollama](https://ollama.com)** running locally on `http://localhost:11434`
  with a model that matches `RESEARCHER_MODEL` / `QUERY_MODEL` in `config.py`
  (defaults to `ministral-3:3b-cloud`). Without Ollama, LLM-driven bootstrap
  and gap-node expansion are skipped; non-LLM agents still run.
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
| `HARVESTER_BASE`  | Override the base directory (vault, fingerprints, logs land here) |
| `OLLAMA_BASE`     | Ollama generate endpoint (default `http://localhost:11434/api/generate`) |
| `OLLAMA_TAGS`     | Ollama tags endpoint                                         |
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
