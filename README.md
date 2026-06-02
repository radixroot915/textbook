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

**B. Remote — any OpenAI-compatible API** (OpenAI, Google Gemini, Groq, Together,
OpenRouter, Ollama Cloud, vLLM, LM Studio, …) — see the walkthrough below.

Without a working LLM backend, non-LLM crawler agents (Wikipedia, Gutenberg,
Archive, Stack Exchange, …) still run, but the topic-bootstrap, gap-node
expansion, curriculum planner, fact checker, glossary, and quality gate are
skipped.

### API onboarding (option B, step-by-step)

The harvester talks to any service that speaks OpenAI's `/chat/completions`
schema. Pick a provider, get a key, set four env vars, smoke-test.

**1. Pick a provider and get an API key**

| Provider     | Sign up                                 | Typical base URL                          | Example model            |
|--------------|-----------------------------------------|-------------------------------------------|--------------------------|
| OpenAI       | https://platform.openai.com/api-keys    | `https://api.openai.com/v1`               | `gpt-4o-mini`            |
| Groq (fast, free tier) | https://console.groq.com/keys | `https://api.groq.com/openai/v1`          | `llama-3.1-8b-instant`   |
| OpenRouter (multi-model) | https://openrouter.ai/keys  | `https://openrouter.ai/api/v1`            | `meta-llama/llama-3.1-8b-instruct` |
| Together AI  | https://api.together.xyz/settings/api-keys | `https://api.together.xyz/v1`           | `meta-llama/Llama-3.1-8B-Instruct-Turbo` |
| Ollama Cloud | https://ollama.com/settings/keys        | `https://ollama.com/v1`                   | `ministral-3:3b-cloud`   |
| Google Gemini | https://aistudio.google.com/apikey     | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-flash`       |

If you're unsure, **Groq** is the quickest to start (free tier, no credit card,
fast inference).

**2. Set the four env vars**

macOS / Linux (bash/zsh):

```bash
export LLM_PROVIDER=openai
export LLM_API_KEY=<paste-key-here>
export LLM_API_BASE=https://api.groq.com/openai/v1     # ← from the table above
export LLM_MODEL=llama-3.1-8b-instant                  # ← from the table above
```

Windows (PowerShell):

```powershell
$env:LLM_PROVIDER = "openai"
$env:LLM_API_KEY  = "<paste-key-here>"
$env:LLM_API_BASE = "https://api.groq.com/openai/v1"
$env:LLM_MODEL    = "llama-3.1-8b-instant"
```

To make it permanent, add the lines to your shell profile (`~/.zshrc`,
`~/.bashrc`) or — on Windows — use `setx LLM_API_KEY "..."` in a new
PowerShell window.

**3. Smoke-test the connection**

```bash
python -c "from llm.ollama_client import health_check, call; \
           assert health_check(); print(call('x','Say hi in 3 words.'))"
```

Expected: a one-line greeting. If you see `LLM_API_KEY not set`, the env var
didn't reach Python — re-export in the same shell you'll run `main.py` from.
If you see HTTP 401, the key is wrong. HTTP 404 usually means `LLM_API_BASE`
is missing the `/v1` (or the model name doesn't exist on this provider).

**4. Run a harvest**

```bash
python main.py "leatherworking" 50 3
```

The first log line should read `Ollama OK` *or* `LLM API reachable at
<your base>` — confirming the request hit your provider, not localhost.

**Cost / rate-limit notes**
- One harvest of `min_files=100` typically issues a few hundred LLM calls
  (bootstrap, per-node expansion, curriculum planning, fact-check, glossary,
  quality gate). Most prompts are small (<2K tokens).
- Free tiers (Groq, OpenRouter free models) are usually fine for one or two
  topics; for bulk runs use a paid tier or a local Ollama model.
- The client retries 429/503 with exponential backoff, so brief rate limits
  self-recover.

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

### Tuning `min_files` and `max_iterations`

The core loop is:

```python
while iteration < max_iterations and total_files < min_files:
    # fetch a batch of 6 nodes × ~18 source agents, dedup, filter, score
```

It also exits early if the researcher's frontier runs out of new nodes
("No new nodes found. Stopping.").

| Parameter        | What it controls                                          | Practical range |
|------------------|-----------------------------------------------------------|-----------------|
| `min_files`      | Target vault size before the loop is allowed to stop. Docs shorter than `MIN_TEXT_LENGTH` (default 2500 chars) don't count. | 20–500 |
| `max_iterations` | Hard cap on crawl iterations regardless of file count. Each iteration ≈ 6 nodes × 18 agents × up to `MAX_CANDIDATES` (80) HTTP requests. | 3–10 |
| `max_cycles`     | Outer cycles. After a cycle, the quality gate evaluates the textbook and, if unusable, gap nodes are injected and the loop runs again. | 1–3 |

There are no hard floors/ceilings in code:
- `min_files=0` or `max_iterations=0` → loop skipped; goes straight to
  synthesis/curriculum on the existing vault.
- Very high values are accepted, but the frontier usually exhausts after
  5–7 iterations on a narrow topic, and source rate limits will throttle
  you long before any internal cap.

Related knobs in `config.py`: `MAX_CANDIDATES` (per-source fetch cap),
`AGENT_CONCURRENCY` (parallel workers), `MIN_TEXT_LENGTH` (admission floor),
`SIMHASH_THRESHOLD` (dedup aggressiveness).

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
