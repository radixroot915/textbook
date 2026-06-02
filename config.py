import os

BASE_DIR = os.environ.get("HARVESTER_BASE", os.path.dirname(os.path.abspath(__file__)))
VAULT_ROOT = os.path.join(BASE_DIR, "vault")
SCRATCH_PATH = os.path.join(BASE_DIR, "_scratch")
HASH_PATH = os.path.join(BASE_DIR, "fingerprints.txt")
MAP_PATH = os.path.join(BASE_DIR, "knowledge_map.json")

OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434/api/generate")
OLLAMA_TAGS = os.environ.get("OLLAMA_TAGS", "http://localhost:11434/api/tags")

# LLM backend selection.
#   "ollama" (default): talks to a local Ollama server at OLLAMA_BASE.
#   "openai": talks to any OpenAI-compatible /chat/completions endpoint
#             (OpenAI, Groq, Together, OpenRouter, Ollama Cloud, vLLM, …).
# When provider == "openai", LLM_API_KEY is required, LLM_API_BASE points at
# the API root (default https://api.openai.com/v1), and LLM_MODEL overrides
# the model names below if set.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama").lower()
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "")

# Optional API keys — agents degrade gracefully when absent
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
SEED_MODEL = LLM_MODEL or "ministral-3:3b-cloud"

RESEARCHER_MODEL = LLM_MODEL or "ministral-3:3b-cloud"
QUERY_MODEL = LLM_MODEL or "ministral-3:3b-cloud"
AGENT_CONCURRENCY = 3
SCRAPE_SLEEP_SECONDS = 2
LEXICON_MIN_HITS_CLEAN = 2
LEXICON_MIN_HITS_OCR = 1

# Density check
DENSITY_SAMPLE_BYTES = 20000       # first N bytes used for density scoring
DENSITY_TOPIC_KW_MIN = 2           # min topic-keyword hits for fallback acceptance

# Search
MAX_CANDIDATES = 80                # max candidates returned per source per node

# Bouncer / dedup
MAX_FINGERPRINTS = 50_000
MIN_TEXT_LENGTH = 2500
SIMHASH_THRESHOLD = 4              # hamming distance below which docs are dupes

# Selector
MIN_LETTER_RATIO = 0.5
HTML_CHECK_WINDOW = 2000


def topic_hash_path(slug: str) -> str:
    """Per-topic fingerprint file so each vault has independent dedup."""
    return os.path.join(BASE_DIR, f"fingerprints_{slug}.txt")


def init_dirs():
    for path in [VAULT_ROOT, SCRATCH_PATH]:
        os.makedirs(path, exist_ok=True)
