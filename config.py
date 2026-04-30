import os

BASE_DIR = os.environ.get("HARVESTER_BASE", os.path.dirname(os.path.abspath(__file__)))
VAULT_ROOT = os.path.join(BASE_DIR, "vault")
SCRATCH_PATH = os.path.join(BASE_DIR, "_scratch")
HASH_PATH = os.path.join(BASE_DIR, "fingerprints.txt")
MAP_PATH = os.path.join(BASE_DIR, "knowledge_map.json")

OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434/api/generate")
OLLAMA_TAGS = os.environ.get("OLLAMA_TAGS", "http://localhost:11434/api/tags")
SEED_MODEL = "llama3.2:3b"

RESEARCHER_MODEL = "llama3.2:3b"
QUERY_MODEL = "llama3.2:3b"
AGENT_CONCURRENCY = 6
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


def init_dirs():
    for path in [VAULT_ROOT, SCRATCH_PATH]:
        os.makedirs(path, exist_ok=True)
