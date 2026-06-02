"""quality_gate — read compile artifacts and decide whether to keep looping.

After each compile cycle, this module:
  - Reads fact_check.json, coherence.md, the textbook itself
  - Computes a quality score across multiple dimensions
  - Decides whether the textbook is "usable" (stop) or needs another cycle
  - Suggests what kind of gap nodes to prioritize next cycle
"""

import os
import re
import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class QualityReport:
    cycle: int = 0
    fact_confidence: float = 0.0        # 0.0–1.0, fraction of claims verified
    flagged_claims: int = 0
    duplicate_headings: int = 0
    contradictions: int = 0
    thin_chapters: int = 0               # word count < MIN
    chapters_total: int = 0
    has_learning_outcomes: int = 0       # number of chapters with "Learning Outcomes"
    has_try_this: int = 0
    has_review_questions: int = 0
    pedagogy_coverage: float = 0.0       # 0.0–1.0
    overall_score: float = 0.0           # 0.0–1.0 composite
    is_usable: bool = False
    reasons: list = field(default_factory=list)


MIN_CHAPTER_WORDS = 500


def evaluate(topic: str, out_dir: str, cycle: int = 0) -> QualityReport:
    """Run all quality checks and return a report. Out_dir is the curriculum
    folder (vault/<topic>/curriculum/).
    """
    r = QualityReport(cycle=cycle)

    textbook = os.path.join(out_dir, f"{topic}_textbook.md")
    fc_json  = os.path.join(out_dir, f"{topic}_fact_check.json")
    coherence = os.path.join(out_dir, f"{topic}_coherence.md")

    if not os.path.exists(textbook):
        r.reasons.append("textbook file missing")
        return r

    with open(textbook, encoding="utf-8") as f:
        md = f.read()

    # --- Fact-check confidence ---
    if os.path.exists(fc_json):
        try:
            data = json.load(open(fc_json, encoding="utf-8"))
            summary = data.get("summary", {}) or {}
            r.fact_confidence = float(
                summary.get("accuracy_score") or
                data.get("overall_score") or
                data.get("confidence_score") or 0.0
            )
            r.flagged_claims = int(
                summary.get("flagged") or
                data.get("total_flagged") or 0
            )
        except Exception as e:
            log.debug(f"[gate] fact_check parse error: {e}")

    # --- Coherence: duplicate headings + contradictions ---
    if os.path.exists(coherence):
        try:
            ctext = open(coherence, encoding="utf-8").read()
            r.duplicate_headings = len(re.findall(
                r'^- \*\*[^*]+\*\* appears in:', ctext, re.MULTILINE))
            r.contradictions = len(re.findall(
                r'^- \*\*[^*]+\*\* \([^)]+\):', ctext, re.MULTILINE))
        except Exception as e:
            log.debug(f"[gate] coherence parse error: {e}")

    # --- Chapter-level pedagogy + thinness ---
    # Use the fact-check JSON's chapter list as ground truth — markdown has
    # sub-sections rendered as H2 that bloat any regex-based count
    chapter_titles = []
    if os.path.exists(fc_json):
        try:
            d = json.load(open(fc_json, encoding="utf-8"))
            chapter_titles = [c.get("title", "") for c in d.get("chapters", [])
                              if c.get("title")]
        except Exception:
            pass

    # For each chapter title, slice the markdown between its heading and the
    # next chapter heading (or end of doc)
    chapters_md = []
    if chapter_titles:
        positions = []
        for title in chapter_titles:
            # Find `## [N.] Title` heading for this chapter
            pat = re.compile(
                r'^##\s+(?:\d+[\.\)]\s+)?' + re.escape(title) + r'\s*$',
                re.MULTILINE | re.IGNORECASE,
            )
            m = pat.search(md)
            if m:
                positions.append((title, m.start()))
        positions.sort(key=lambda x: x[1])
        for i, (title, start) in enumerate(positions):
            end = positions[i + 1][1] if i + 1 < len(positions) else len(md)
            chapters_md.append(md[start:end])
    r.chapters_total = len(chapters_md)

    for ch in chapters_md:
        if len(ch.split()) < MIN_CHAPTER_WORDS:
            r.thin_chapters += 1
        if re.search(r'##\s+learning outcomes', ch, re.IGNORECASE):
            r.has_learning_outcomes += 1
        if re.search(r'\btry this\b', ch, re.IGNORECASE):
            r.has_try_this += 1
        if re.search(r'##\s+review questions', ch, re.IGNORECASE):
            r.has_review_questions += 1

    if r.chapters_total:
        ped_present = (r.has_learning_outcomes + r.has_try_this + r.has_review_questions)
        r.pedagogy_coverage = ped_present / (r.chapters_total * 3)

    # --- Composite score (0..1) ---
    # Weighted: facts 35%, pedagogy 30%, coherence 20%, thinness 15%
    fact_term = r.fact_confidence
    ped_term  = r.pedagogy_coverage
    coh_term  = 1.0 if (r.duplicate_headings + r.contradictions == 0) else max(
        0.0, 1.0 - 0.1 * (r.duplicate_headings + r.contradictions))
    thin_term = 1.0 - (r.thin_chapters / max(r.chapters_total, 1))
    r.overall_score = (
        fact_term * 0.35 + ped_term * 0.30 + coh_term * 0.20 + thin_term * 0.15
    )

    # --- Usability decision ---
    reasons = []
    if r.fact_confidence < 0.70:
        reasons.append(f"fact confidence {r.fact_confidence:.0%} < 70%")
    if r.pedagogy_coverage < 0.80:
        reasons.append(f"pedagogy coverage {r.pedagogy_coverage:.0%} < 80%")
    if r.duplicate_headings > 2:
        reasons.append(f"{r.duplicate_headings} duplicate headings (>2)")
    if r.contradictions > 0:
        reasons.append(f"{r.contradictions} numeric contradictions")
    if r.thin_chapters > max(1, r.chapters_total // 4):
        reasons.append(f"{r.thin_chapters}/{r.chapters_total} chapters thin")
    if r.chapters_total < 5:
        reasons.append(f"only {r.chapters_total} chapters — corpus too thin")

    r.reasons = reasons
    r.is_usable = len(reasons) == 0
    return r


def log_report(r: QualityReport, log_obj):
    log_obj.info(
        f"[QUALITY] cycle={r.cycle} score={r.overall_score:.2f} | "
        f"facts={r.fact_confidence:.0%} | pedagogy={r.pedagogy_coverage:.0%} | "
        f"dup={r.duplicate_headings} | contra={r.contradictions} | "
        f"thin={r.thin_chapters}/{r.chapters_total} | "
        f"usable={'YES' if r.is_usable else 'NO'}"
    )
    if r.reasons:
        for reason in r.reasons:
            log_obj.info(f"[QUALITY]   - {reason}")


def has_improved(prev: QualityReport | None, curr: QualityReport,
                 min_delta: float = 0.05) -> bool:
    """Return True if the current cycle is meaningfully better than the
    previous one. Plateau detection — used to decide whether to keep looping.
    """
    if prev is None:
        return True
    return (curr.overall_score - prev.overall_score) >= min_delta
