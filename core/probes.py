"""core.probes — small, isolated verification questions for architectural pivots.

A probe asks ONE specific question about system behavior. Probes are cheap
(~30s-2min each), deterministic enough to be useful, and live separately
from the integration tests so they can be re-run on demand without coupling
to the full pipeline. Use them BEFORE committing to an expensive change —
de-risk the bet.

Usage:
    python workflow.py probe list                      # list available probes
    python workflow.py probe claim-attribution         # run one probe
    python workflow.py probe ollama-latency

Each probe returns a `ProbeResult` and exits 0 (PASS), 1 (FAIL), or 2
(INCONCLUSIVE). Probes are NOT pytest tests — they hit live services
(LLM, network) and produce a verdict for a human to act on.
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ProbeResult:
    name: str
    verdict: str                       # "PASS" | "FAIL" | "INCONCLUSIVE"
    metrics: dict = field(default_factory=dict)
    summary: str = ""
    notes: list = field(default_factory=list)


# Registry — populated by @probe decorator below
_REGISTRY: dict[str, dict] = {}


def probe(name: str, description: str = ""):
    """Decorator: register a probe under `name`. The wrapped function
    receives the parsed argparse Namespace and returns a ProbeResult."""
    def decorator(fn: Callable) -> Callable:
        _REGISTRY[name] = {"fn": fn, "description": description}
        return fn
    return decorator


def registry() -> dict[str, dict]:
    return dict(_REGISTRY)


def get(name: str):
    return _REGISTRY.get(name)


# ---------------------------------------------------------------------------
# Probe implementations

_SPECIFIC_RE = re.compile(
    r'\b\d+(?:[\.,]\d+)?\s*(?:°[CF]|°|%|mm|cm|m|km|in|inch|inches|ft|kg|g|lb|oz|psi|amps?|hz|hp|rpm|sec|min|hr|hour|day|week|month|year|gauge|grit)\b'
    r'|\b1[5-9]\d{2}\b|\b20[0-2]\d\b'
    r'|\b\d+(?:st|nd|rd|th)\s+century\b'
    r'|\b(?:ASTM|ANSI|AISI|ISO|EN|DIN|JIS|MIL|NFPA|OSHA|NIOSH|UL|CE)[\s-]?[A-Z]?\d+[A-Z0-9-]*\b',
    re.IGNORECASE,
)
_CITE_RE = re.compile(r'\[C(\d+)\]')
_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z\d])')


@probe(
    "claim-attribution",
    "Does the writer LLM reliably attribute [CN] claim IDs in its prose?"
)
def claim_attribution(args) -> ProbeResult:
    """Sends a realistic claim list to the writer LLM and measures whether
    specific-bearing sentences are tagged with the source claim IDs.

    PASS: ≥ 70% of specific sentences attributed, 0 hallucinated IDs
    INCONCLUSIVE: 40-70%, or zero specifics in output
    FAIL: < 40% attributed, or any hallucinated [CN] beyond the claim range
    """
    from llm.ollama_client import call
    from config import RESEARCHER_MODEL
    from claims_db import _load as _load_claims

    topic = getattr(args, "topic", "leatherworking")
    n = getattr(args, "claims", 40)

    db = _load_claims(topic)
    all_claims = db.get("claims", [])
    if len(all_claims) < n:
        return ProbeResult(
            name="claim-attribution",
            verdict="INCONCLUSIVE",
            summary=f"Only {len(all_claims)} claims in DB, need {n}",
        )
    # Prefer claims with specifics — they're what NEEDS attribution
    with_specifics = [c for c in all_claims if c.get("numeric") or any(ch.isdigit() for ch in c.get("text", ""))]
    sample = (with_specifics + all_claims)[:n]

    claim_block = "\n".join(
        f"[C{i}] {c['text']}{' [low-trust]' if c.get('low_trust') else ''}"
        for i, c in enumerate(sample, 1)
    )

    prompt = f"""[INST]Write a 400-500 word excerpt for a {topic} textbook chapter. Use ONLY the claims listed below as factual source.

ATTRIBUTION RULE (mandatory):
- Every sentence stating a specific value (number with unit, year, century, brand, standard ID, named technique) MUST be tagged with the source claim ID inline, e.g. `[C3]` at the end of the relevant phrase.
- Multiple claims in one sentence → multiple tags: `... process [C2] [C7] ...`
- If you cannot find a claim supporting a specific value, OMIT that value — do not invent.
- Non-specific connective prose does NOT need tags.

CLAIMS:
{claim_block}

Write the excerpt now. Markdown paragraphs, no headings.[/INST]"""

    started = time.time()
    output = call(RESEARCHER_MODEL, prompt, temperature=0.25, timeout=240,
                  num_ctx=8192, num_predict=1500)
    elapsed = time.time() - started

    if not output:
        return ProbeResult(
            name="claim-attribution",
            verdict="FAIL",
            summary="LLM returned empty output",
            metrics={"elapsed_sec": round(elapsed, 1)},
        )

    valid_ids = set(range(1, len(sample) + 1))
    sentences = [s for s in _SENT_SPLIT.split(output) if len(s.strip()) >= 20]
    specific = [s for s in sentences if _SPECIFIC_RE.search(s)]
    attributed = [s for s in specific if _CITE_RE.search(s)]
    all_cites = [int(m) for m in _CITE_RE.findall(output)]
    hallucinated = [m for m in all_cites if m not in valid_ids]

    rate = (len(attributed) / len(specific)) if specific else 0.0
    metrics = {
        "claims_provided": len(sample),
        "total_sentences": len(sentences),
        "specific_sentences": len(specific),
        "attributed_sentences": len(attributed),
        "attribution_rate": round(rate, 3),
        "total_cite_markers": len(all_cites),
        "hallucinated_ids": len(hallucinated),
        "elapsed_sec": round(elapsed, 1),
    }
    notes = [output[:1500] + ("…" if len(output) > 1500 else "")]

    if not specific:
        return ProbeResult(
            name="claim-attribution", verdict="INCONCLUSIVE",
            summary="No specifics in output", metrics=metrics, notes=notes,
        )
    if hallucinated:
        return ProbeResult(
            name="claim-attribution", verdict="FAIL",
            summary=f"{len(hallucinated)} hallucinated claim IDs (beyond C{len(sample)})",
            metrics=metrics, notes=notes,
        )
    if rate >= 0.70:
        v = "PASS"
    elif rate >= 0.40:
        v = "INCONCLUSIVE"
    else:
        v = "FAIL"
    return ProbeResult(
        name="claim-attribution", verdict=v,
        summary=f"{rate:.0%} of specific sentences attributed ({len(attributed)}/{len(specific)})",
        metrics=metrics, notes=notes,
    )


@probe(
    "ollama-latency",
    "Round-trip latency on a tiny prompt — detects loading / unstable models"
)
def ollama_latency(args) -> ProbeResult:
    """Three back-to-back small calls. PASS if all under 10s, FAIL if any timeout."""
    from llm.ollama_client import call
    from config import RESEARCHER_MODEL

    timings = []
    failures = 0
    for i in range(3):
        started = time.time()
        try:
            result = call(RESEARCHER_MODEL, "[INST]Reply with just the word OK.[/INST]",
                          temperature=0.0, timeout=30, num_ctx=512, num_predict=8)
            elapsed = time.time() - started
            timings.append(elapsed)
            if not result:
                failures += 1
        except Exception:
            timings.append(30.0)
            failures += 1

    avg = sum(timings) / 3
    metrics = {
        "calls": 3,
        "avg_sec": round(avg, 2),
        "max_sec": round(max(timings), 2),
        "min_sec": round(min(timings), 2),
        "failures": failures,
    }
    if failures > 0:
        return ProbeResult(
            name="ollama-latency", verdict="FAIL",
            summary=f"{failures}/3 calls failed; avg {avg:.1f}s",
            metrics=metrics,
        )
    if avg < 5:
        v = "PASS"
    elif avg < 15:
        v = "INCONCLUSIVE"
    else:
        v = "FAIL"
    return ProbeResult(
        name="ollama-latency", verdict=v,
        summary=f"avg {avg:.1f}s on 3 small calls (max {max(timings):.1f}s)",
        metrics=metrics,
    )


@probe(
    "claim-id-verification",
    "End-to-end: writer attributes, fact-checker verifies via lookup, post-pass strips."
)
def claim_id_verification(args) -> ProbeResult:
    """Generates a chapter excerpt with attribution, then runs:
       1. parse_claim_markers — extracts [CN] IDs
       2. FactChecker.check_by_markers — verifies via lookup
       3. strip_claim_markers — removes markers
    Validates the full claim-ID pipeline end-to-end without a real run."""
    from llm.ollama_client import call
    from llm.prompts import CLAIM_DRIVEN_CHAPTER_PROMPT
    from claims_db import _load as _load_claims, render_claims_for_prompt
    from curriculum.fact_checker import (
        FactChecker, parse_claim_markers, strip_claim_markers,
    )
    from config import RESEARCHER_MODEL

    topic = getattr(args, "topic", "leatherworking")
    n = getattr(args, "claims", 20)

    db = _load_claims(topic)
    all_claims = db.get("claims", [])
    if len(all_claims) < n:
        return ProbeResult(
            name="claim-id-verification", verdict="INCONCLUSIVE",
            summary=f"Need {n} claims, DB has {len(all_claims)}",
        )
    sample = all_claims[:n]

    # Use the REAL writer prompt — same one production uses
    prompt = CLAIM_DRIVEN_CHAPTER_PROMPT.format(
        topic=topic,
        chapter_title="Materials and Selection",
        claims_block=render_claims_for_prompt(sample)[:8000],
        expected_topics="leather types, thickness, grain, selection criteria",
    )

    started = time.time()
    output = call(RESEARCHER_MODEL, prompt, temperature=0.25, timeout=240,
                  num_ctx=8192, num_predict=1500)
    elapsed = time.time() - started

    if not output:
        return ProbeResult(
            name="claim-id-verification", verdict="FAIL",
            summary="LLM returned empty output",
            metrics={"elapsed_sec": round(elapsed, 1)},
        )

    # Step 1: parse markers
    marker_ids = parse_claim_markers(output)

    # Step 2: verify via direct lookup
    fc = FactChecker(topic)
    result = fc.check_by_markers(
        chapter_title="Materials and Selection",
        chapter_content=output,
        claims_passed=sample,
        source_files=["probe"],
    )
    if result is None:
        return ProbeResult(
            name="claim-id-verification", verdict="FAIL",
            summary="Writer produced 0 markers — attribution rule ignored",
            metrics={"elapsed_sec": round(elapsed, 1)},
            notes=[output[:1200]],
        )

    # Step 3: strip markers, verify clean prose
    stripped = strip_claim_markers(output)
    has_residual = "[C" in stripped and any(
        ch.isdigit() for ch in stripped.split("[C", 1)[1][:6]
    ) if "[C" in stripped else False

    metrics = {
        "claims_provided": n,
        "markers_emitted": len(marker_ids),
        "unique_markers": len(set(marker_ids)),
        "verified": result.verified_count,
        "tentative": result.tentative_count,
        "flagged": result.flagged_count,
        "confidence_score": round(result.confidence_score, 3),
        "stripped_residual": has_residual,
        "stripped_length": len(stripped),
        "original_length": len(output),
        "elapsed_sec": round(elapsed, 1),
    }
    notes = [
        "=== Writer output (first 800 chars) ===",
        output[:800],
        "",
        "=== After marker stripping (first 800 chars) ===",
        stripped[:800],
    ]

    # Verdict
    if has_residual:
        return ProbeResult(
            name="claim-id-verification", verdict="FAIL",
            summary="strip_claim_markers left residual [CN] in output",
            metrics=metrics, notes=notes,
        )
    hallucinated = sum(1 for c in result.flagged_claims if "hallucinated" in c.text.lower())
    if hallucinated > 0:
        return ProbeResult(
            name="claim-id-verification", verdict="FAIL",
            summary=f"{hallucinated} hallucinated marker IDs in writer output",
            metrics=metrics, notes=notes,
        )
    if len(marker_ids) < 3:
        return ProbeResult(
            name="claim-id-verification", verdict="INCONCLUSIVE",
            summary=f"Only {len(marker_ids)} markers — too few to judge",
            metrics=metrics, notes=notes,
        )
    if result.confidence_score >= 0.70:
        return ProbeResult(
            name="claim-id-verification", verdict="PASS",
            summary=f"score={result.confidence_score:.2f}, "
                    f"{len(marker_ids)} markers, no residual, no hallucinations",
            metrics=metrics, notes=notes,
        )
    return ProbeResult(
        name="claim-id-verification", verdict="INCONCLUSIVE",
        summary=f"score={result.confidence_score:.2f} (markers worked but verification weak)",
        metrics=metrics, notes=notes,
    )


@probe(
    "drift-gate-precision",
    "Does the drift gate correctly reject off-topic nodes? Sanity check."
)
def drift_gate_precision(args) -> ProbeResult:
    """Runs a fixed corpus of on-topic + off-topic nodes through the drift
    gate and reports precision (true rejects / total rejects)."""
    from drift_monitor import is_node_on_topic

    topic = getattr(args, "topic", "leatherworking")
    on_topic = [
        "leatherworking history", "edge bevelers and burnishers",
        "saddle stitching techniques", "vegetable-tanned leather",
        "awl sharpening methods",
    ]
    off_topic = [
        "Administrative districts of Belarus", "Gameplay mechanics for RPGs",
        "Election results 1872", "Mythology of ancient Greece",
        "Football tactics 4-3-3",
    ]
    lexicon = ["awl", "burnisher", "edge beveler", "stitch", "leather"]

    true_pos = 0   # off-topic correctly rejected
    false_neg = 0  # off-topic wrongly accepted
    true_neg = 0   # on-topic correctly accepted
    false_pos = 0  # on-topic wrongly rejected

    for n in on_topic:
        allowed, _ = is_node_on_topic(topic, n, lexicon)
        if allowed:
            true_neg += 1
        else:
            false_pos += 1
    for n in off_topic:
        allowed, _ = is_node_on_topic(topic, n, lexicon)
        if not allowed:
            true_pos += 1
        else:
            false_neg += 1

    metrics = {
        "on_topic_accepted": true_neg,
        "on_topic_rejected": false_pos,
        "off_topic_rejected": true_pos,
        "off_topic_accepted": false_neg,
    }
    if false_neg > 0 or false_pos > 1:
        v = "FAIL"
        summary = f"off-topic accepted: {false_neg}, on-topic rejected: {false_pos}"
    elif false_pos > 0:
        v = "INCONCLUSIVE"
        summary = f"{false_pos} on-topic node rejected"
    else:
        v = "PASS"
        summary = "all on-topic accepted, all off-topic rejected"
    return ProbeResult(
        name="drift-gate-precision", verdict=v, summary=summary, metrics=metrics,
    )
