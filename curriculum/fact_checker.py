"""
Fact-checker for compiled textbook chapters.

Flow:
  1. LLM extracts specific factual claims from the chapter draft
     (measurements, temperatures, specs, procedure steps with values)
  2. Each claim is cross-referenced against the raw source passages that
     were actually used to write the chapter — deterministic string search,
     no LLM involved in verification
  3. Claims are scored: verified (2+ sources), tentative (1 source), or
     flagged (0 sources — likely hallucinated or unsupported)
  4. The chapter text is annotated with [⚠ UNVERIFIED] markers on
     zero-support claims; a Sources section is appended
  5. A per-chapter FactCheckResult is returned for reporting

Design principle: LLM is only used to extract claims, never to verify them.
Verification is deterministic text matching — it cannot hallucinate.
"""

import re
import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# LLM params for claim extraction (small task, small context needed)
EXTRACT_CTX     = 4096
EXTRACT_PREDICT = 1024

# Cross-reference thresholds
VERIFIED_THRESHOLD  = 2   # sources needed for "verified"
TENTATIVE_THRESHOLD = 1   # sources needed for "tentative"


def _is_low_trust(filename: str) -> bool:
    """Files whose origin is user-generated content (Reddit, forums, blogs)
    can corroborate but should not single-handedly verify a specific claim.
    Detection is heuristic on the source slug in the filename — the file
    origins map records which agent saved each file.
    """
    fn = filename.lower()
    if "eddit" in fn or "ddit" in fn:            # reddit_<id>
        return True
    # Look up the file origin map for ground truth
    try:
        from agent_stats import _load, _origins_path
        origins = _load(_origins_path())
        for topic_origins in origins.values():
            src = topic_origins.get(filename)
            if src and src in ("reddit", "duckduckgo", "hub", "cited"):
                return True
    except Exception:
        pass
    return False

# Proximity window: how many chars around a keyword hit to call it "supporting"
PROXIMITY_WINDOW = 400
# Sentence splitter shared by both verification paths
_SENT_SPLIT_PROD = re.compile(r'(?<=[.!?])\s+(?=[A-Z\d])')


@dataclass
class Claim:
    text: str                           # the claim as a sentence
    claim_type: str                     # "specification", "procedure", "safety", "general"
    keywords: list = field(default_factory=list)  # searchable terms from the claim
    supporting_sources: list = field(default_factory=list)  # filenames that support it
    supporting_quotes: list = field(default_factory=list)   # excerpt snippets
    confidence: str = "unverified"      # "verified", "tentative", "flagged"


@dataclass
class FactCheckResult:
    chapter_title: str
    annotated_content: str
    claims: list                        # all Claim objects
    flagged_claims: list                # Claim objects with confidence=="flagged"
    verified_count: int = 0
    tentative_count: int = 0
    flagged_count: int = 0
    confidence_score: float = 0.0      # fraction of claims that are verified or tentative
    source_citations: list = field(default_factory=list)


class FactChecker:
    def __init__(self, topic: str):
        self.topic = topic

    # -----------------------------------------------------------------------
    # Claim-ID attribution path — preferred when the chapter contains
    # [CN] markers emitted by the writer. Direct lookup, no LLM, no fuzz.

    def check_by_markers(
        self,
        chapter_title: str,
        chapter_content: str,
        claims_passed: list,        # exact list the writer was given (1-indexed via [CN])
        source_files: list,
    ) -> FactCheckResult | None:
        """Verify a chapter by parsing [CN] markers and looking up claims
        in the list the writer was given. Returns None if the chapter has
        no markers (caller should fall back to the legacy check)."""
        ids = parse_claim_markers(chapter_content)
        if not ids:
            return None

        n_claims = len(claims_passed)
        valid_ids = set(range(1, n_claims + 1))

        # Build "claims" — one per UNIQUE attribution marker the writer used.
        # Each gets `confidence` per the rules:
        #   - valid ID + high-trust source → verified
        #   - valid ID + only low-trust source → tentative
        #   - invalid ID (hallucinated marker) → flagged
        claims_out: list = []
        for cid in dict.fromkeys(ids):                # de-dup while preserving order
            if cid in valid_ids:
                source = claims_passed[cid - 1]
                is_low_trust = bool(source.get("low_trust"))
                conf = "tentative" if is_low_trust else "verified"
                claims_out.append(Claim(
                    text=source.get("text", "")[:200],
                    claim_type=source.get("type", "general"),
                    keywords=source.get("keywords", []) or [],
                    supporting_sources=[source.get("source_file", "?")],
                    supporting_quotes=[f"[claim-ID lookup: C{cid}]"],
                    confidence=conf,
                ))
            else:
                claims_out.append(Claim(
                    text=f"[hallucinated marker C{cid} — not in claim list of {n_claims}]",
                    claim_type="general",
                    keywords=[],
                    supporting_sources=[],
                    supporting_quotes=[],
                    confidence="flagged",
                ))

        # Flagged also includes specific-bearing sentences with NO attribution.
        # The hallucination_filter regex already encodes our "specific value"
        # definition; reuse it for consistency.
        try:
            from curriculum.hallucination_filter import _NUM_UNIT_PAT, _YEAR_PAT, _STANDARD_PAT
            specific_patterns = [_NUM_UNIT_PAT, _YEAR_PAT, _STANDARD_PAT]
        except Exception:
            specific_patterns = []

        unattributed_specifics = []
        sentences = _SENT_SPLIT_PROD.split(chapter_content)
        for sent in sentences:
            s = sent.strip()
            if len(s) < 25:
                continue
            if _CLAIM_MARKER_RE.search(s):
                continue
            for pat in specific_patterns:
                if pat.search(s):
                    unattributed_specifics.append(s[:140])
                    claims_out.append(Claim(
                        text=s[:200],
                        claim_type="unattributed",
                        keywords=[],
                        supporting_sources=[],
                        supporting_quotes=[],
                        confidence="flagged",
                    ))
                    break

        verified  = [c for c in claims_out if c.confidence == "verified"]
        tentative = [c for c in claims_out if c.confidence == "tentative"]
        flagged   = [c for c in claims_out if c.confidence == "flagged"]
        total = len(claims_out)
        score = (len(verified) + len(tentative) * 0.5) / total if total else 1.0

        log.info(
            f"[CHECKER:ID] {chapter_title}: "
            f"{len(verified)} verified / {len(tentative)} tentative / "
            f"{len(flagged)} flagged | score={score:.2f} "
            f"({len(ids)} markers, {len(unattributed_specifics)} unattributed specifics)"
        )

        return FactCheckResult(
            chapter_title=chapter_title,
            annotated_content=chapter_content,    # no annotation in marker mode
            claims=claims_out,
            flagged_claims=flagged,
            verified_count=len(verified),
            tentative_count=len(tentative),
            flagged_count=len(flagged),
            confidence_score=score,
            source_citations=source_files,
        )

    def check_chapter(
        self,
        chapter_title: str,
        chapter_content: str,
        source_passages: str,       # raw passages fed to the LLM when writing
        source_files: list,         # filenames used as sources
        full_source_texts: dict,    # {filename: full_text} for deep search
    ) -> FactCheckResult:
        """Check one chapter. Returns annotated content + claim report."""

        claims = self._extract_claims(chapter_title, chapter_content)
        if not claims:
            log.debug(f"[CHECKER] No claims extracted for: {chapter_title}")
            return FactCheckResult(
                chapter_title=chapter_title,
                annotated_content=chapter_content,
                claims=[],
                flagged_claims=[],
                confidence_score=1.0,
                source_citations=source_files,
            )

        log.info(f"[CHECKER] {chapter_title}: {len(claims)} claims to verify")

        # Build search corpus from source passages + full texts of used files
        corpus = _build_corpus(source_passages, full_source_texts, source_files)

        for claim in claims:
            self._cross_reference(claim, corpus)

        # Plausibility check — downgrade implausible claims to flagged
        # regardless of whether they were verified in the corpus
        try:
            from curriculum.plausibility import check_text
            warnings = check_text(chapter_content)
            if warnings:
                warning_keys = {w.sentence[:80].lower() for w in warnings}
                for claim in claims:
                    if claim.text[:80].lower() in warning_keys or any(
                        w.sentence[:60].lower() in claim.text.lower() for w in warnings
                    ):
                        claim.confidence = "flagged"
                log.info(f"[CHECKER] {chapter_title}: {len(warnings)} plausibility warnings")
        except Exception as e:
            log.debug(f"[CHECKER] plausibility skipped: {e}")

        verified   = [c for c in claims if c.confidence == "verified"]
        tentative  = [c for c in claims if c.confidence == "tentative"]
        flagged    = [c for c in claims if c.confidence == "flagged"]

        total = len(claims)
        score = (len(verified) + len(tentative) * 0.5) / total if total else 1.0

        log.info(
            f"[CHECKER] {chapter_title}: "
            f"{len(verified)} verified / {len(tentative)} tentative / "
            f"{len(flagged)} flagged | score={score:.2f}"
        )

        annotated = self._annotate(chapter_content, flagged)
        annotated = self._append_sources(annotated, chapter_title, claims, source_files)

        return FactCheckResult(
            chapter_title=chapter_title,
            annotated_content=annotated,
            claims=claims,
            flagged_claims=flagged,
            verified_count=len(verified),
            tentative_count=len(tentative),
            flagged_count=len(flagged),
            confidence_score=score,
            source_citations=source_files,
        )

    # -----------------------------------------------------------------------
    # Step 1: Claim extraction (LLM)

    def _extract_claims(self, chapter_title: str, content: str) -> list:
        from llm.ollama_client import call_json
        from llm.prompts import CLAIM_EXTRACT_PROMPT

        prompt = CLAIM_EXTRACT_PROMPT.format(
            topic=self.topic,
            chapter_title=chapter_title,
            chapter_text=content[:12000],
        )

        try:
            result = call_json(
                _model(), prompt, temperature=0.1, timeout=120,
                num_ctx=EXTRACT_CTX, num_predict=EXTRACT_PREDICT,
            )
        except Exception as e:
            log.debug(f"[CHECKER] claim extraction LLM error: {e}")
            return []

        if not isinstance(result, list):
            return []

        claims = []
        for item in result:
            if not isinstance(item, dict):
                continue
            text = item.get("claim", "").strip()
            if not text or len(text) < 10:
                continue
            keywords = item.get("keywords", [])
            if not keywords:
                # Prefer specifics: numbers+units, capitalized identifiers (ASTM, AISI),
                # then technical-looking words. Generic words last and only if nothing else.
                specifics = re.findall(
                    r'\b\d+(?:[\.,]\d+)?\s*(?:°[CF]|°|%|mm|cm|m|km|in|inch|ft|kg|g|lb|oz|psi|bar|amp|amps|A|V|hz|hp|rpm|sec|min|hr|hour)\b'
                    r'|\b[A-Z]{2,}[\s-]?\d+[A-Z0-9]*\b'
                    r'|\b\d+(?:[\.,]\d+)?\b',
                    text,
                )
                caps = re.findall(r'\b[A-Z][a-z]{3,}(?:[\s-][A-Z][a-z]+)*\b', text)
                generic = [w for w in re.findall(r'\b\w{5,}\b', text.lower())
                           if w not in _STOPWORDS]
                merged = list(dict.fromkeys(specifics + caps + generic))
                keywords = merged[:6]
            claims.append(Claim(
                text=text,
                claim_type=item.get("type", "general"),
                keywords=[k.lower().strip() for k in keywords if k.strip()],
            ))

        return claims

    # -----------------------------------------------------------------------
    # Step 2: Cross-reference (deterministic)

    def _check_claim_db(self, claim: Claim) -> list[str]:
        """Look up the claim against the topic's claim DB. Returns a list
        of source_file names from the DB that back this claim.

        Matching: keyword overlap >= half of the claim's keywords AND, if
        the claim contains specific numerics, those numerics must also
        appear in the DB claim (or within ±15% tolerance).
        """
        try:
            from claims_db import _load as _load_claims
            db = _load_claims(self.topic)
        except Exception:
            return []

        claims_list = db.get("claims", [])
        if not claims_list:
            return []

        # Numerics in the new claim
        claim_text_lower = claim.text.lower()
        claim_nums = [
            float(m.group(1))
            for m in re.finditer(r'\b(\d+(?:\.\d+)?)\b', claim.text)
        ]

        matches: list[str] = []
        kws_lower = [k.lower() for k in claim.keywords]
        required_kw = max(1, len(kws_lower) // 2)

        for db_claim in claims_list:
            db_text_lower = db_claim.get("text", "").lower()
            kw_hits = sum(1 for k in kws_lower if k in db_text_lower)
            if kw_hits < required_kw:
                continue

            # If the chapter claim has numerics, require them to overlap
            # with the DB claim's numerics (or appear in its text)
            if claim_nums:
                db_nums = [
                    float(m.group(1))
                    for m in re.finditer(r'\b(\d+(?:\.\d+)?)\b',
                                          db_claim.get("text", ""))
                ]
                db_nums.extend([
                    float(n.split()[0])
                    for n in db_claim.get("numeric", []) or []
                    if n and n.split()[0].replace('.','').isdigit()
                ])
                if not db_nums:
                    continue
                # ±15% tolerance match
                match_found = False
                for cn in claim_nums:
                    for dn in db_nums:
                        if abs(cn - dn) / max(abs(dn), 1) <= 0.15:
                            match_found = True
                            break
                    if match_found:
                        break
                if not match_found:
                    continue

            src = db_claim.get("source_file")
            if src and src not in matches:
                matches.append(src)

        return matches

    def _cross_reference(self, claim: Claim, corpus: dict):
        """Search corpus files for support. Modifies claim in-place.

        New flow: check the claim DB FIRST (cheap, exact source of truth).
        Only fall back to corpus keyword search if DB doesn't match.
        This makes verification claim-aware — paraphrased claims that
        originated in a DB entry get correctly verified, instead of being
        flagged because the LLM reworded them.
        """
        if not claim.keywords:
            claim.confidence = "tentative"
            return

        # Step 1: Claim DB check (primary)
        db_match = self._check_claim_db(claim)
        if db_match:
            claim.supporting_sources.extend(db_match)
            for src in db_match:
                claim.supporting_quotes.append(f"[claim-DB match in {src[:30]}]")

        for fname, text in corpus.items():
            text_lower = text.lower()

            # Check how many keywords appear in this source
            hits = [kw for kw in claim.keywords if kw in text_lower]
            if len(hits) < max(1, len(claim.keywords) // 2):
                continue

            # Find supporting quote: region around first keyword hit
            for kw in hits:
                idx = text_lower.find(kw)
                if idx == -1:
                    continue
                start = max(0, idx - PROXIMITY_WINDOW // 2)
                end   = min(len(text), idx + PROXIMITY_WINDOW // 2)
                snippet = text[start:end].strip()

                # Check that the specific values in the claim also appear nearby
                claim_numbers = re.findall(r'\b\d+(?:\.\d+)?(?:\s*(?:°F|°C|psi|amp|A|V|mm|in|ft|lb|kg|rpm|%))?\b', claim.text)
                value_support = not claim_numbers or any(
                    num.strip() in snippet for num in claim_numbers
                )

                if value_support:
                    claim.supporting_sources.append(fname)
                    claim.supporting_quotes.append(snippet[:200])
                    break

        # Low-trust sources (Reddit threads, forum posts) can corroborate
        # but cannot single-handedly *verify* a claim. A claim supported only
        # by Reddit sources gets demoted from verified → tentative; if only
        # one Reddit source, it stays tentative; nothing changes if any
        # high-trust source also supports the claim.
        sources = claim.supporting_sources
        n = len(sources)
        low_trust_count = sum(1 for s in sources if _is_low_trust(s))
        high_trust_count = n - low_trust_count

        if n >= VERIFIED_THRESHOLD and high_trust_count >= 1:
            # At least one trusted source AND total >= 2
            claim.confidence = "verified"
        elif n >= VERIFIED_THRESHOLD:
            # Multiple Reddit-only matches don't escape tentative
            claim.confidence = "tentative"
        elif n >= TENTATIVE_THRESHOLD:
            claim.confidence = "tentative"
        else:
            claim.confidence = "flagged"

    # -----------------------------------------------------------------------
    # Step 3: Annotate flagged claims

    def _annotate(self, content: str, flagged: list) -> str:
        """Insert [⚠ UNVERIFIED] marker after flagged claim sentences in the text."""
        if not flagged:
            return content

        annotated = content
        for claim in flagged:
            # Try to find the claim sentence and mark it
            # Use keywords to locate the sentence in the text
            best_kw = sorted(claim.keywords, key=len, reverse=True)[:2]
            for kw in best_kw:
                pattern = re.compile(
                    r'([^.!?\n]*' + re.escape(kw) + r'[^.!?\n]*[.!?])',
                    re.IGNORECASE
                )
                match = pattern.search(annotated)
                if match:
                    original = match.group(0)
                    if "[⚠" not in original:
                        annotated = annotated.replace(
                            original,
                            original + " [⚠ UNVERIFIED — not found in source material]",
                            1
                        )
                    break

        return annotated

    # -----------------------------------------------------------------------
    # Step 4: Append sources section

    def _append_sources(self, content: str, chapter_title: str,
                        claims: list, source_files: list) -> str:
        flagged  = [c for c in claims if c.confidence == "flagged"]
        verified = [c for c in claims if c.confidence == "verified"]

        lines = [
            "\n\n---\n",
            "### Chapter Sources & Verification\n",
        ]

        if source_files:
            lines.append("**Source files used:**\n")
            for f in source_files:
                lines.append(f"- `{f}`")
            lines.append("")

        if flagged:
            lines.append(
                f"\n**⚠ {len(flagged)} claim(s) could not be verified against source material:**\n"
            )
            for c in flagged:
                lines.append(f"- *{c.text}*")
            lines.append(
                "\n*These claims may be accurate but were not found in the collected "
                "source files. Verify independently before relying on them.*"
            )

        if verified:
            lines.append(
                f"\n*{len(verified)}/{len(claims)} claims cross-referenced to source material.*"
            )

        return content + "\n".join(lines)


# ---------------------------------------------------------------------------
# Report writer

def write_fact_check_report(results: list, out_dir: str, topic: str) -> str:
    """Write a combined fact-check report for all chapters."""
    import os

    total_claims   = sum(r.verified_count + r.tentative_count + r.flagged_count for r in results)
    total_verified = sum(r.verified_count for r in results)
    total_tentative = sum(r.tentative_count for r in results)
    total_flagged  = sum(r.flagged_count for r in results)
    avg_score      = sum(r.confidence_score for r in results) / len(results) if results else 0

    lines = [
        f"# {topic.replace('_', ' ').title()} — Textbook Fact-Check Report",
        "",
        f"**{total_claims} claims checked** | "
        f"**{total_verified} verified** | "
        f"**{total_tentative} tentative** | "
        f"**{total_flagged} flagged** | "
        f"**Accuracy score: {avg_score:.0%}**",
        "",
        "> Verified = found in 2+ source files. Tentative = found in 1 source. "
        "Flagged = not found in any source — possible hallucination.",
        "",
        "## Per-Chapter Summary",
        "",
        "| Chapter | Claims | Verified | Tentative | Flagged | Score |",
        "|---------|--------|----------|-----------|---------|-------|",
    ]

    for r in results:
        total = r.verified_count + r.tentative_count + r.flagged_count
        lines.append(
            f"| {r.chapter_title} | {total} | {r.verified_count} | "
            f"{r.tentative_count} | {r.flagged_count} | {r.confidence_score:.0%} |"
        )

    # Detail for flagged claims
    all_flagged = [(r.chapter_title, c) for r in results for c in r.flagged_claims]
    if all_flagged:
        lines += [
            "",
            f"## All Flagged Claims ({len(all_flagged)} total)",
            "",
            "*These specific claims were not found in the collected source material "
            "and should be independently verified before use.*",
            "",
        ]
        for ch_title, claim in all_flagged:
            lines.append(f"**{ch_title}** — {claim.text}")
            if claim.keywords:
                lines.append(f"  - Keywords searched: {', '.join(claim.keywords)}")
            lines.append("")

    report_md = "\n".join(lines)
    report_path = os.path.join(out_dir, f"{topic}_fact_check.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    # Also write machine-readable JSON
    json_path = os.path.join(out_dir, f"{topic}_fact_check.json")
    json_data = {
        "topic": topic,
        "summary": {
            "total_claims": total_claims,
            "verified": total_verified,
            "tentative": total_tentative,
            "flagged": total_flagged,
            "accuracy_score": round(avg_score, 3),
        },
        "chapters": [
            {
                "title": r.chapter_title,
                "verified": r.verified_count,
                "tentative": r.tentative_count,
                "flagged": r.flagged_count,
                "score": round(r.confidence_score, 3),
                "flagged_claims": [c.text for c in r.flagged_claims],
                "sources": r.source_citations,
            }
            for r in results
        ],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2)

    log.info(f"[CHECKER] Report: {report_path} | {total_flagged} flagged claims")
    return report_path


# ---------------------------------------------------------------------------
# Helpers

def _build_corpus(source_passages: str, full_texts: dict, used_files: list) -> dict:
    """Build a {filename: text} corpus for cross-referencing."""
    corpus = {}
    # The passages block already contains the most relevant excerpts
    if source_passages:
        corpus["__passages__"] = source_passages

    # Add full texts for files actually used
    for fname in used_files:
        if fname in full_texts:
            corpus[fname] = full_texts[fname]

    return corpus


def _model() -> str:
    from config import RESEARCHER_MODEL
    return RESEARCHER_MODEL


_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "will",
    "when", "used", "using", "also", "than", "more", "some", "been", "into",
    "during", "after", "before", "should", "which", "their", "there",
}


# ---------------------------------------------------------------------------
# Claim-ID attribution — writer-emitted [CN] markers as verification primary
#
# Architecture: the writer LLM is instructed to tag every specific-bearing
# sentence with [CN] markers referencing the source claim ID. The fact-checker
# then verifies by DIRECT LOOKUP against the claim list passed to the writer.
# No fuzzy matching, no LLM re-extraction — verification is deterministic.
#
# Markers are stripped from the final textbook by a post-pass so the reader
# sees clean prose, not citation noise.

# Matches either `[C7]`, `[C1, C2]`, `[C1 C2]`, or `[C1, C2 C3]`.
_CLAIM_MARKER_RE = re.compile(
    r'\[\s*C\s*\d+(?:\s*[,\s]\s*C?\s*\d+)*\s*\]',
    re.IGNORECASE,
)
_CLAIM_ID_RE = re.compile(r'\d+')


def parse_claim_markers(text: str) -> list[int]:
    """Return ALL claim IDs referenced in `text` (with duplicates), in order
    of appearance. `[C7]` → [7]. `[C1, C2] [C3]` → [1, 2, 3]."""
    ids: list[int] = []
    for m in _CLAIM_MARKER_RE.finditer(text):
        ids.extend(int(n) for n in _CLAIM_ID_RE.findall(m.group(0)))
    return ids


def strip_claim_markers(text: str) -> str:
    """Remove all [CN] / [C1, C2] / [C1 C2] markers from `text` and tidy
    the resulting whitespace + punctuation gaps."""
    # Drop the marker and any space immediately preceding it
    out = re.sub(r'\s*' + _CLAIM_MARKER_RE.pattern, '', text, flags=re.IGNORECASE)
    # Collapse double-spaces that the removal may have left behind
    out = re.sub(r' {2,}', ' ', out)
    # Tighten orphaned " ." / " ," / " ;" left by stripped markers
    out = re.sub(r'\s+([.,;:!?])', r'\1', out)
    return out
