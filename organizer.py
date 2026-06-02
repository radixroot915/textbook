import re


def analyze_technical_density(text, lexicon):
    if not text or not lexicon:
        return 0, []
    text_lower = text.lower()
    # Substring match so multi-word lexicon terms ("flesh grain",
    # "edge bevel") actually count — bagging would only catch tokens.
    found = [w for w in lexicon if w.lower() in text_lower]
    return len(found), found


def post_scrape_organize(raw_data, carved_text, source_url, lexicon):
    score, markers = analyze_technical_density(carved_text, lexicon)
    return markers if score >= 3 else []


# ---------------------------------------------------------------------------
# Tier-1 quality gates
# ---------------------------------------------------------------------------

def _garbage_metrics(sample: str) -> dict:
    """Compute readability metrics for one text chunk."""
    words = re.findall(r'\S+', sample)
    if len(words) < 50:
        return {"valid": False}
    avg_len = sum(len(w) for w in words) / len(words)
    real = sum(
        1 for w in words
        if len(w) >= 2 and sum(c.isalpha() for c in w) >= len(w) * 0.6
    )
    lines = [l for l in sample.split('\n') if l.strip()]
    short_ratio = (sum(1 for l in lines if len(l.strip()) < 15) / len(lines)) if lines else 0
    return {
        "valid": True,
        "avg_len": avg_len,
        "real_ratio": real / len(words),
        "short_ratio": short_ratio,
    }


def detect_ocr_garbage(text: str) -> tuple[bool, str]:
    """Reject documents that are mostly OCR scan noise (punctuation,
    single characters, fragmented lines). Samples 3 windows and only
    rejects when garbage is SUSTAINED — a document with one bad page
    plus real content elsewhere passes through.
    """
    if len(text) < 3000:
        return False, ""

    mid = len(text) // 2
    chunks = [
        text[2000: 2000 + 6000],                          # past front matter
        text[mid - 3000: mid + 3000],                     # middle
        text[max(0, len(text) - 6000):],                  # tail
    ]
    bad = 0
    worst_reason = ""
    for c in chunks:
        m = _garbage_metrics(c)
        if not m["valid"]:
            continue
        if m["avg_len"] < 3.0:
            bad += 1
            worst_reason = f"avg-word-len={m['avg_len']:.1f}"
        elif m["real_ratio"] < 0.55:
            bad += 1
            worst_reason = f"alpha-word-ratio={m['real_ratio']:.2f}"
        elif m["short_ratio"] > 0.7:
            bad += 1
            worst_reason = f"short-line-ratio={m['short_ratio']:.2f}"

    # Reject only when at least 2 of 3 windows look like garbage —
    # tolerates partial OCR damage on otherwise-good scans
    if bad >= 2:
        return True, worst_reason
    return False, ""


def density_multi_window(text: str, lexicon: list, window_size: int = 5000) -> tuple[int, list]:
    """Sample three windows (start, middle, end). Robust aggregator that
    catches off-topic pollution without penalizing legitimate articles
    whose nav/refs sections score low.

    Rule: at least 2 of 3 windows must have at least one lexicon hit.
    Returned score is the sum of the top-2 window scores so that genuinely
    on-topic content easily clears thresholds even with one weak section.
    """
    if len(text) <= window_size * 3:
        return analyze_technical_density(text, lexicon)

    mid = len(text) // 2
    windows = [
        text[:window_size],
        text[mid - window_size // 2: mid + window_size // 2],
        text[-window_size:],
    ]

    per_window = [analyze_technical_density(w, lexicon) for w in windows]
    coverage = sum(1 for s, _ in per_window if s > 0)

    # Require lexicon hits in 2+ windows. Pollution that mentions the topic
    # vocabulary in only one section (e.g. nav/TOC/refs) gets rejected;
    # genuine articles have topic vocabulary distributed across the text.
    if coverage < 2:
        return 0, []

    markers_union: set = set()
    for _, m in per_window:
        markers_union.update(m)

    total = sum(s for s, _ in per_window)
    return total, list(markers_union)


_TOPIC_GATE_PROMPT = """Does the following text contain substantive instructional content about "{topic}"? Answer with one word: yes or no.

Text:
{sample}

Answer:"""


def llm_topic_gate(text: str, topic: str) -> bool | None:
    """Cheap LLM yes/no gate for borderline files. Returns True/False, or
    None if Ollama isn't available — caller should treat None as 'accept'.

    Sampled from the middle of the file so polluted files with on-topic
    front-matter can't pass.
    """
    try:
        from llm.ollama_client import call
        from config import RESEARCHER_MODEL
    except Exception:
        return None

    if len(text) < 500:
        return None

    mid = len(text) // 2
    sample = text[max(0, mid - 600): mid + 600]
    prompt = _TOPIC_GATE_PROMPT.format(topic=topic.replace('_', ' '), sample=sample)

    try:
        reply = call(RESEARCHER_MODEL, prompt, temperature=0.0,
                     timeout=30, num_ctx=2048, num_predict=8)
    except Exception:
        return None

    if not reply:
        return None
    return reply.strip().lower().startswith("y")
