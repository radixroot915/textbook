"""
TextbookCompiler — deep synthesis of all vault source files into a structured textbook.

Flow:
  1. Index ALL .txt files in the vault topic directory (text, headings, topic scores)
  2. Plan chapter structure from grit + lexicon via LLM (with structural fallback)
  3. For each chapter: score ALL source files for relevance, extract best passages
     from across the full corpus — not just the first 2000 bytes of one file
  4. Write each chapter via LLM with a large passage context budget
  5. Track coverage metrics (word count, source file count, topic hits) per chapter
  6. Write gap report: thin chapters, uncovered topics, unused files, search suggestions
"""

import os
import re
import json
import math
import logging
from dataclasses import dataclass, field
from curriculum.cleanup import vault_cleanup

log = logging.getLogger(__name__)

# Passage gathering limits
PASSAGE_BUDGET       = 20000  # chars of source text fed to LLM per chapter
PASSAGE_CHUNK_MIN    = 60     # skip chunks shorter than this
MAX_FILES_PER_CHAPTER = 15    # cap files to avoid prompt explosion
FC_REGEN_THRESHOLD   = 0.45   # chapters below this confidence score get reground

# LLM generation parameters
CHAPTER_CTX     = 8192
CHAPTER_PREDICT = 4096   # was 2048 — chapters were truncating mid-sentence
PLAN_CTX        = 4096
PLAN_PREDICT    = 1024

# Quality thresholds
MIN_CHAPTER_WORDS = 300       # chapters under this are flagged "thin"


# ---------------------------------------------------------------------------
# Data classes

@dataclass
class FileEntry:
    name: str
    text: str
    word_count: int = 0
    headings: list = field(default_factory=list)
    lexicon_hits: set = field(default_factory=set)


@dataclass
class ChapterSpec:
    title: str
    expected_topics: list = field(default_factory=list)
    grit_refs: list = field(default_factory=list)


@dataclass
class ChapterResult:
    spec: ChapterSpec
    content: str
    word_count: int
    source_files: list = field(default_factory=list)
    covered_topics: list = field(default_factory=list)
    missing_topics: list = field(default_factory=list)


# ---------------------------------------------------------------------------

class TextbookCompiler:
    def __init__(self, topic: str, lexicon: list, grit: list, vault_path: str, out_dir: str):
        self.topic = topic
        self.lexicon = [t.lower().strip() for t in lexicon if t.strip()]
        self.grit = grit
        self.vault_path = vault_path
        self.out_dir = out_dir
        # Claim list passed to the writer for each chapter, keyed by title.
        # The fact-checker uses this to verify [CN] markers via direct lookup
        # instead of fuzzy matching against the corpus.
        self._chapter_claims_used: dict = {}

    def compile(self) -> tuple:
        """Returns (textbook_path, gap_report_path, gap_nodes).

        gap_nodes is a list of searchable node strings derived from thin chapters
        and missing topics — ready to inject into the research frontier.
        """
        log.info(f"[COMPILER] Indexing vault: {self.vault_path}")
        file_index = self._index_vault_files()
        log.info(f"[COMPILER] Indexed {len(file_index)} source files "
                 f"({sum(e.word_count for e in file_index.values()):,} total words)")

        log.info(f"[COMPILER] Planning chapters...")
        chapters = self._plan_chapters(file_index)
        # Smoke-mode cap: HARVESTER_MAX_CHAPTERS env var truncates the
        # planner output to test pipeline behavior with proportional load.
        # Used by `workflow.py smoke` to keep iteration time under ~25 min.
        max_ch = os.environ.get("HARVESTER_MAX_CHAPTERS")
        if max_ch and max_ch.isdigit():
            cap = int(max_ch)
            if cap and len(chapters) > cap:
                log.info(f"[COMPILER] Capping chapters to {cap} (HARVESTER_MAX_CHAPTERS)")
                chapters = chapters[:cap]
        log.info(f"[COMPILER] {len(chapters)} chapters planned")

        # {filename: full_text} for fact-checker cross-referencing
        full_texts = {fname: entry.text for fname, entry in file_index.items()}

        results = []
        chapter_passages = {}   # chapter_title -> raw passages string
        for i, chapter in enumerate(chapters, 1):
            log.info(f"[COMPILER] [{i}/{len(chapters)}] {chapter.title}")
            passages, sources = self._gather_passages(chapter, file_index)
            chapter_passages[chapter.title] = passages
            content = self._write_chapter(chapter, passages)
            wc = len(content.split())
            covered = self._measure_coverage(content, chapter.expected_topics)
            missing = [t for t in chapter.expected_topics if t not in covered]
            results.append(ChapterResult(
                spec=chapter,
                content=content,
                word_count=wc,
                source_files=sources,
                covered_topics=covered,
                missing_topics=missing,
            ))
            log.info(f"[COMPILER]   {wc} words | {len(sources)} sources | "
                     f"{len(missing)} topics missing")

        # Fact-check first so the editor sees flagged claims
        log.info(f"[COMPILER] Running fact-check pass...")
        results, fc_results = self._fact_check_pass(results, chapter_passages, full_texts)

        # Reground chapters that failed the confidence threshold before editing
        low_confidence = [fc for fc in fc_results if fc.confidence_score < FC_REGEN_THRESHOLD]
        if low_confidence:
            log.info(
                f"[COMPILER] {len(low_confidence)} chapter(s) below "
                f"{FC_REGEN_THRESHOLD:.0%} confidence — regrounding from source passages"
            )
            results = self._reground_pass(results, fc_results, chapter_passages)

        log.info(f"[COMPILER] Running edit pass (with fact-check annotations)...")
        results = self._edit_pass(results, fc_results)

        # Final pass: re-fact-check, then HARD-DELETE any sentences still
        # flagged. After reground + edit, anything still unsupported is junk —
        # strip it instead of letting [⚠ UNVERIFIED] markers get silently
        # cleaned away leaving bad content in place.
        log.info(f"[COMPILER] Final fact-check + hard-delete pass...")
        results, fc_results = self._fact_check_pass(results, chapter_passages, full_texts)
        results = self._strip_flagged_sentences(results, fc_results)

        # Topic-coverage check — catches chapters where the LLM drifted into
        # a different craft (e.g. wrote about woodworking joinery in a
        # leatherworking textbook). Replaces drifted chapters with a brief
        # placeholder marked for human review.
        log.info(f"[COMPILER] Topic-coverage check...")
        results = self._topic_drift_filter(results)

        # Foreign-tool check — strip sentences referencing tools from other
        # crafts (e.g. "use a bench plane to thin the hide" in leatherworking
        # is dangerously wrong; remove those sentences entirely).
        log.info(f"[COMPILER] Foreign-tool check...")
        results = self._foreign_tool_check(results)

        # Redundancy merge — when two chapters cover overlapping sub-topics,
        # the second one's overlap is trimmed (the first keeps the full
        # treatment). Preserves unique additions from the second chapter.
        log.info(f"[COMPILER] Redundancy merge pass...")
        results = self._redundancy_merge(results)

        # Semantic paragraph dedup — catches paraphrased repeats within a
        # chapter that the heading-based merge can't see. Cheap Jaccard
        # pre-filter, LLM judges intent only on candidate pairs.
        log.info(f"[COMPILER] Semantic paragraph dedup pass...")
        results = self._semantic_paragraph_dedup(results)

        # Pedagogical enrichment — rationales, try-this, review questions.
        # No new facts; just scaffolding around existing content.
        log.info(f"[COMPILER] Pedagogical enrichment pass...")
        results = self._pedagogy_pass(results)

        # Hallucination filter — strip sentences whose specific values
        # (numbers, dates, standards) don't appear in the claim DB. This
        # is the railroad's enforcement layer for the anchor-and-railroad
        # architecture. Catches "plausible but invented" specifics.
        try:
            from curriculum.hallucination_filter import filter_chapter
            from claims_db import _load as _load_claims
            db = _load_claims(self.topic)
            claims = db.get("claims", [])
            if claims:
                total_stripped = 0
                total_unsupported = 0
                filtered_results = []
                for r in results:
                    new_content, stats = filter_chapter(r.content, claims)
                    if stats["sentences_stripped"] > 0:
                        log.info(
                            f"[HALLUCINATION] '{r.spec.title[:50]}': "
                            f"stripped {stats['sentences_stripped']} sentence(s) with "
                            f"{stats['specifics_unsupported']} unsupported specific(s)"
                        )
                    total_stripped += stats["sentences_stripped"]
                    total_unsupported += stats["specifics_unsupported"]
                    filtered_results.append(ChapterResult(
                        spec=r.spec,
                        content=new_content,
                        word_count=len(new_content.split()),
                        source_files=r.source_files,
                        covered_topics=r.covered_topics,
                        missing_topics=r.missing_topics,
                    ))
                results = filtered_results
                log.info(
                    f"[HALLUCINATION] total: {total_stripped} sentence(s) stripped, "
                    f"{total_unsupported} specifics found unsupported"
                )
                try:
                    from watchdog import wd
                    wd.emit("compile", "filter_activity",
                            name="hallucination",
                            stripped=total_stripped,
                            unsupported=total_unsupported)
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"[COMPILER] hallucination filter error: {e}")

        if os.environ.get("HARVESTER_SKIP_IMAGES"):
            log.info(f"[COMPILER] Skipping image fetch (HARVESTER_SKIP_IMAGES set)")
        else:
            log.info(f"[COMPILER] Fetching chapter images...")
            results = self._fetch_images(results)

        # Cross-chapter coherence — detect duplicate topics and contradictions
        try:
            self._write_coherence_report(results)
        except Exception as e:
            log.debug(f"[COMPILER] coherence report error: {e}")

        # Build glossary from bolded terms across chapters
        try:
            from curriculum.glossary import build_glossary
            build_glossary(self.topic, results, self.out_dir)
        except Exception as e:
            log.debug(f"[COMPILER] glossary error: {e}")

        textbook_path = self._write_textbook(results)

        # Reformat ugly markdown tables into clean prose-bullet style
        try:
            from curriculum.table_reformat import reformat_tables
            with open(textbook_path, encoding="utf-8") as f:
                md = f.read()
            reformatted, n_tables = reformat_tables(md)
            if n_tables:
                with open(textbook_path, "w", encoding="utf-8") as f:
                    f.write(reformatted)
                log.info(f"[TABLES] {n_tables} table(s) reformatted as bullets")
        except Exception as e:
            log.debug(f"[COMPILER] table reformat error: {e}")

        # Strip broken anchor links the LLM generated (slugs that don't
        # match any heading), then insert real cross-chapter links.
        try:
            from curriculum.chapter_xref import link_chapters, strip_broken_anchors
            with open(textbook_path, encoding="utf-8") as f:
                md = f.read()
            md, n_broken = strip_broken_anchors(md)
            if n_broken:
                log.info(f"[XREF-CHAP] {n_broken} broken anchor link(s) unlinked")
            linked, n = link_chapters(md)
            if n or n_broken:
                with open(textbook_path, "w", encoding="utf-8") as f:
                    f.write(linked)
                if n:
                    log.info(f"[XREF-CHAP] {n} cross-chapter links inserted")
        except Exception as e:
            log.debug(f"[COMPILER] chapter xref error: {e}")

        # Post-write enrichments: terminology canonicalization, acronym
        # expansion, glossary auto-link, confidence markers, era flags, index
        try:
            from curriculum.textbook_postpass import (
                expand_acronyms, link_glossary_terms, mark_confidence,
                flag_era, build_index,
            )
            from curriculum.terminology import canonicalize_textbook
            with open(textbook_path, encoding="utf-8") as f:
                md = f.read()

            # Canonicalize terminology BEFORE other passes so glossary
            # linking and confidence markers operate on consistent forms
            md, n_canon = canonicalize_textbook(md)
            if n_canon:
                log.info(f"[POSTPASS] canonicalized {n_canon} terminology variant(s)")
                try:
                    from watchdog import wd
                    wd.emit("compile", "filter_activity",
                            name="terminology", replaced=n_canon)
                except Exception:
                    pass

            md, n_acr = expand_acronyms(md, expand_uses=3)
            if n_acr:
                log.info(f"[POSTPASS] expanded/condensed {n_acr} acronym occurrence(s)")

            gloss_path = os.path.join(self.out_dir, f"{self.topic}_glossary.md")
            md, n_gloss = link_glossary_terms(md, gloss_path)
            if n_gloss:
                log.info(f"[POSTPASS] linked {n_gloss} glossary term(s)")

            fc_json = os.path.join(self.out_dir, f"{self.topic}_fact_check.json")
            md, n_conf = mark_confidence(md, fc_json)
            if n_conf:
                log.info(f"[POSTPASS] {n_conf} claim confidence marker(s) inserted")

            md, n_era = flag_era(md)
            if n_era:
                log.info(f"[POSTPASS] flagged {n_era} historical-practice paragraph(s)")

            # Append index appendix
            idx = build_index(md, self.topic)
            if idx:
                md = md.rstrip() + "\n\n---\n\n" + idx + "\n"
                log.info(f"[POSTPASS] index appendix appended")

            with open(textbook_path, "w", encoding="utf-8") as f:
                f.write(md)
        except Exception as e:
            log.debug(f"[COMPILER] postpass error: {e}")
        gap_path, gap_nodes = self._write_gap_report(results, file_index)

        # Write fact-check report
        try:
            from curriculum.fact_checker import write_fact_check_report
            write_fact_check_report(fc_results, self.out_dir, self.topic)
        except Exception as e:
            log.debug(f"[COMPILER] fact-check report error: {e}")

        # Attribute used/unused back to source agents for future runs
        all_used = set(s for r in results for s in r.source_files)
        try:
            from agent_stats import record_compile_result
            record_compile_result(self.topic, all_used, set(file_index.keys()))
        except Exception as e:
            log.debug(f"[COMPILER] stats record error: {e}")

        # Harvest outbound citations from the vault BEFORE cleanup destroys
        # the files. Cited URLs feed the next cycle's CitedURLAgent.
        try:
            from agents.cited_url_agent import harvest_citations_from_vault
            harvest_citations_from_vault(self.topic)
        except Exception as e:
            log.debug(f"[COMPILER] citation harvest error: {e}")

        # Prune vault — delete all source files, flag zero-hit unused as junk
        try:
            from config import MAP_PATH
            vault_cleanup(self.vault_path, self.topic, file_index, all_used, MAP_PATH)
        except Exception as e:
            log.warning(f"[COMPILER] vault cleanup error: {e}")

        log.info(f"[COMPILER] {len(gap_nodes)} gap nodes generated for re-harvest")
        return textbook_path, gap_path, gap_nodes

    # -----------------------------------------------------------------------
    # Phase 1: Index vault files

    def _index_vault_files(self) -> dict:
        index = {}
        if not os.path.exists(self.vault_path):
            return index
        for fname in sorted(os.listdir(self.vault_path)):
            if not fname.endswith(".txt"):
                continue
            fpath = os.path.join(self.vault_path, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except Exception as e:
                log.warning(f"[COMPILER] Could not read {fname}: {e}")
                continue
            text_lower = text.lower()
            index[fname] = FileEntry(
                name=fname,
                text=text,
                word_count=len(text.split()),
                headings=self._extract_headings(text),
                lexicon_hits={t for t in self.lexicon if t in text_lower},
            )
        return index

    def _extract_headings(self, text: str) -> list:
        headings = []
        for line in text.splitlines():
            stripped = line.strip()
            # Markdown headings
            if re.match(r'^#{1,4}\s+\S', stripped):
                headings.append(stripped.lstrip('#').strip())
            # Wiki-style == headings ==
            elif re.match(r'^={2,}\s*\S', stripped):
                headings.append(re.sub(r'=', '', stripped).strip())
        return headings[:25]

    # -----------------------------------------------------------------------
    # Phase 2: Chapter plan

    def _plan_chapters(self, file_index: dict) -> list:
        from llm.ollama_client import call_json
        from llm.prompts import CHAPTER_PLAN_PROMPT

        grit_tasks = [g.get("task", "") for g in self.grit if g.get("task")][:25]
        all_headings = []
        for entry in file_index.values():
            all_headings.extend(entry.headings[:4])
        unique_headings = list(dict.fromkeys(all_headings))[:50]

        prompt = CHAPTER_PLAN_PROMPT.format(
            topic=self.topic,
            lexicon=", ".join(self.lexicon[:35]),
            grit_tasks="\n".join(f"- {t}" for t in grit_tasks),
            source_headings="\n".join(f"- {h}" for h in unique_headings),
        )

        result = call_json(
            _model(), prompt, temperature=0.3, timeout=120,
            num_ctx=PLAN_CTX, num_predict=PLAN_PREDICT,
        )

        if isinstance(result, list) and result:
            chapters = []
            for item in result:
                if not isinstance(item, dict) or "title" not in item:
                    continue
                title = item["title"]
                topics = item.get("topics", [])
                grit_refs = [
                    g for g in self.grit
                    if any(kw.lower() in g.get("task", "").lower()
                           for kw in (topics + [title])[:5])
                ]
                chapters.append(ChapterSpec(title=title, expected_topics=topics,
                                            grit_refs=grit_refs))
            if chapters:
                return chapters

        log.warning(f"[COMPILER] LLM chapter plan failed (got {type(result).__name__}: {str(result)[:120]}) — using generic fallback")
        return self._fallback_chapters()

    def _fallback_chapters(self) -> list:
        """Fallback derived from grit and lexicon — generic skeleton enriched
        with topic-specific terms mined from the actual corpus, so chapters
        aren't just empty boilerplate when the LLM plan call fails."""
        # Mine grit tasks to find natural groupings
        all_tasks = [g.get("task", "") for g in self.grit if g.get("task")]
        all_tools = set()
        for g in self.grit:
            all_tools.update(t.lower() for t in g.get("tools", []))

        topic_label = self.topic.replace("_", " ").title()
        lex = [t for t in self.lexicon if t]

        # Bucket lexicon terms by semantic keyword association so chapter
        # expected_topics include domain-specific vocabulary, not just generic words
        def lex_for(*kws: str) -> list:
            kws_lower = [k.lower() for k in kws]
            return [t for t in lex if any(k in t.lower() for k in kws_lower)][:6]

        # Generic skeleton with topic-mined enrichment per chapter
        structure = [
            (f"Introduction to {topic_label}",
             ["history", "overview", "fundamentals", "scope"] + lex_for("history", "origin", "fundamental")),
            ("Safety and Workspace Setup",
             ["safety", "ppe", "workspace", "hazards"] + lex_for("safe", "hazard", "ppe", "protection", "ventilation")),
            ("Tools and Equipment",
             list(list(all_tools)[:8]) + lex_for("tool", "equipment", "machine", "instrument")),
            ("Materials and Selection",
             ["materials", "properties", "grades"] + lex_for("material", "grade", "alloy", "wood", "metal", "fiber")),
            ("Foundational Techniques",
             ["technique", "fundamentals"] + lex_for("technique", "method", "basic", "fundamental")),
            ("Joinery and Connections",
             ["joint", "fastening", "assembly"] + lex_for("joint", "weld", "fasten", "bond", "connect")),
            ("Intermediate Methods",
             ["accuracy", "precision"] + lex_for("intermediate", "precision", "control")),
            ("Surface Treatment and Finishing",
             ["finish", "surface"] + lex_for("finish", "coat", "polish", "sand", "treat")),
            ("Project Planning and Layout",
             ["planning", "layout", "measurement"] + lex_for("plan", "layout", "measure", "mark", "design")),
            ("Quality and Inspection",
             ["inspection", "tolerances", "defects"] + lex_for("inspect", "defect", "tolerance", "quality", "test")),
            ("Advanced Techniques",
             ["advanced", "specialized"] + lex_for("advanced", "specialist", "complex", "expert")),
        ]

        # Score each structure entry against actual grit tasks so grit_refs are useful
        chapters = []
        for title, topics in structure:
            grit_refs = [
                g for g in self.grit
                if any(kw in g.get("task", "").lower() for kw in topics[:5])
            ]
            # Also pull any lexicon terms relevant to this chapter's topics
            lex_topics = [t for t in self.lexicon
                          if any(kw in t for kw in topics[:4])]
            chapters.append(ChapterSpec(
                title=title,
                expected_topics=topics + lex_topics[:4],
                grit_refs=grit_refs,
            ))
        return chapters

    # -----------------------------------------------------------------------
    # Phase 3: Passage gathering across ALL source files

    def _gather_passages(self, chapter: ChapterSpec, file_index: dict) -> tuple:
        """Score every source file for relevance to this chapter, extract best passages."""
        # Build the chapter's search term set
        chapter_terms = set()
        for t in chapter.expected_topics:
            chapter_terms.update(t.lower().split())
        chapter_terms.update(w for w in chapter.title.lower().split() if len(w) > 3)
        chapter_terms.update(
            t for t in self.lexicon
            if any(cw in t for cw in chapter_terms)
        )

        # Classification-aware bonus: files pre-tagged as belonging to this
        # chapter (by the classifier) get a head start. We still score
        # every file by lexical relevance — the tag is a bonus, not a gate.
        try:
            from classifier import get_all
            classifications = get_all()
        except Exception:
            classifications = {}

        # Build a chapter-keyword fingerprint matched against compound
        # slot names like "material-selection" or "procedure-troubleshooting"
        title_words = {
            w.lower() for w in chapter.title.replace(":", " ").split()
            if len(w) > 3
        }
        topic_words = {t.lower().split()[0] for t in chapter.expected_topics if t}
        title_words |= topic_words

        def _slot_matches(slot: str) -> bool:
            # Slot is "material-selection" → ["material", "selection"]
            slot_parts = slot.replace("-", " ").split()
            return any(
                tw in slot_parts or slot_parts[0] in tw or tw in slot
                for tw in title_words
            )

        # Score every file — normalise by sqrt(word_count) so large broad
        # articles (Wikipedia) don't crowd out focused specialist documents
        scored = []
        for fname, entry in file_index.items():
            lexicon_overlap = len(entry.lexicon_hits & chapter_terms)
            heading_hits = sum(
                1 for h in entry.headings
                if any(t in h.lower() for t in chapter_terms)
            )
            text_hits = sum(
                1 for t in chapter_terms
                if t in entry.text.lower()
            )
            raw = lexicon_overlap * 3 + heading_hits * 2 + min(text_hits, 10)

            # Classification bonus: +50% if any of the file's chapter_relevance
            # slots aligns with this chapter's title/topic keywords
            cls = classifications.get(fname, {})
            tags = cls.get("chapter_relevance", [])
            if any(_slot_matches(t) for t in tags):
                raw = int(raw * 1.5) + 2

            if raw > 0:
                score = raw / math.sqrt(max(entry.word_count, 1))
                scored.append((score, fname, entry))

        scored.sort(key=lambda x: -x[0])

        # Apply a relative relevance gate — drop the tail end of the ranked list
        # when scores collapse to noise. Threshold = 25% of the top score.
        if scored:
            top_score = scored[0][0]
            cutoff = top_score * 0.25
            scored = [s for s in scored if s[0] >= cutoff]

        parts = []
        sources_used = []
        budget = PASSAGE_BUDGET

        for _, fname, entry in scored[:MAX_FILES_PER_CHAPTER]:
            if budget <= 0:
                break
            chunk = self._extract_relevant_chunks(entry.text, chapter_terms, budget)
            if chunk:
                parts.append(f"[{fname}]\n{chunk}")
                sources_used.append(fname)
                budget -= len(chunk)

        # If nothing scored, pull from highest word-count files as fallback
        if not parts and file_index:
            fallback = sorted(file_index.values(), key=lambda e: -e.word_count)[:3]
            for entry in fallback:
                parts.append(f"[{entry.name}]\n{entry.text[:1800]}")
                sources_used.append(entry.name)

        return "\n\n".join(parts), sources_used

    def _extract_relevant_chunks(self, text: str, terms: set, budget: int) -> str:
        """Split text into paragraph-level chunks, score each, return top chunks up to budget."""
        # Split at blank lines or heading markers
        raw_chunks = re.split(r'\n{2,}|(?=\n#{1,4}\s)', text)

        scored = []
        for chunk in raw_chunks:
            chunk = chunk.strip()
            if len(chunk) < PASSAGE_CHUNK_MIN:
                continue
            chunk_lower = chunk.lower()
            score = sum(1 for t in terms if t in chunk_lower)
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda x: -x[0])

        result_parts = []
        used = 0
        for _, chunk in scored:
            if used >= budget:
                break
            take = min(len(chunk), budget - used)
            result_parts.append(chunk[:take])
            used += take

        return "\n\n".join(result_parts)

    # -----------------------------------------------------------------------
    # Phase 4: LLM chapter generation

    def _write_chapter_claim_driven(self, chapter: ChapterSpec,
                                    claims: list) -> str:
        """Generate a chapter using ONLY pre-extracted claims as factual base.
        Hallucination becomes structurally impossible — the LLM can only
        arrange and connect facts it's been given.
        """
        from llm.ollama_client import call
        from llm.prompts import CLAIM_DRIVEN_CHAPTER_PROMPT
        from claims_db import render_claims_for_prompt

        claims_block = render_claims_for_prompt(claims)
        prompt = CLAIM_DRIVEN_CHAPTER_PROMPT.format(
            topic=self.topic,
            chapter_title=chapter.title,
            claims_block=claims_block[:14000],
            expected_topics=", ".join(chapter.expected_topics[:12]),
        )

        result = call(
            _model(), prompt,
            temperature=0.25, timeout=300,
            num_ctx=CHAPTER_CTX, num_predict=CHAPTER_PREDICT,
        )

        if not result or len(result.strip()) < 80:
            log.warning(f"[COMPILER] claim-driven returned thin content for: {chapter.title}")
            return (f"*Content for '{chapter.title}' needs bolstering — "
                    f"insufficient claims in database.*\n\n"
                    f"Expected topics: {', '.join(chapter.expected_topics)}")

        cleaned = _clean_chapter_output(result)
        cleaned = _strip_duplicate_title(cleaned, chapter.title)
        log.info(
            f"[CLAIM-DRIVEN] '{chapter.title}': "
            f"{len(claims)} claims → {len(cleaned.split())} words"
        )
        return cleaned

    def _write_chapter(self, chapter: ChapterSpec, passages: str) -> str:
        from llm.ollama_client import call

        # Claim-driven mode: if a claim DB exists for this topic, use it as
        # the authoritative fact source. The LLM writes prose connecting
        # the pre-verified claims; it cannot invent specifics.
        try:
            from claims_db import (
                get_claims_for_chapter, render_claims_for_prompt, _load,
            )
            db = _load(self.topic)
            if db.get("claims"):
                claims = get_claims_for_chapter(
                    self.topic, chapter.title,
                    chapter.expected_topics, self.lexicon,
                )
                if claims:
                    # Stash the exact claim list given to the writer; the
                    # fact-check pass will use it to resolve [CN] markers.
                    self._chapter_claims_used[chapter.title] = claims
                    return self._write_chapter_claim_driven(chapter, claims)
        except Exception as e:
            log.debug(f"[COMPILER] claim-driven path error: {e}")

        # Fallback to passages-based writing when no claims are available
        from llm.prompts import DEEP_CHAPTER_PROMPT

        grit_items = [
            {"task": g.get("task", ""),
             "tools": g.get("tools", []),
             "steps": g.get("steps", [])}
            for g in chapter.grit_refs[:8]
        ]
        grit_text = json.dumps(grit_items, indent=2)

        # If this chapter is about tools, materials, or first projects,
        # the LLM tends to dump exhaustive lists. Inject a beginner-pathing
        # instruction so the chapter actually guides a newcomer.
        beginner_hint = ""
        ct = chapter.title.lower()
        if any(k in ct for k in ("tool", "equipment", "material", "introduction",
                                  "getting started", "first project", "starter")):
            beginner_hint = (
                "\n\nBEGINNER GUIDANCE — open this chapter with a clearly-marked "
                "'Starter Set' section that names 3-6 minimum tools/materials a "
                "complete beginner needs for their first project, with a one-line "
                "justification for each. Advanced/specialist equipment (lasers, "
                "industrial machines, premium materials) goes in a separate "
                "'Advanced/Specialist' section near the end of the chapter — "
                "not interleaved with beginner essentials.\n"
            )

        prompt = DEEP_CHAPTER_PROMPT.format(
            topic=self.topic,
            chapter_title=chapter.title,
            expected_topics=", ".join(chapter.expected_topics[:12]) + beginner_hint,
            source_passages=passages[:18000],
            grit_items=grit_text[:1500],
        )

        result = call(
            _model(), prompt,
            temperature=0.35, timeout=300,
            num_ctx=CHAPTER_CTX, num_predict=CHAPTER_PREDICT,
        )

        if not result or len(result.strip()) < 80:
            log.warning(f"[COMPILER] LLM returned thin/empty content for: {chapter.title}")
            return (f"*Content for '{chapter.title}' needs bolstering — "
                    f"insufficient source material or LLM timeout.*\n\n"
                    f"Expected topics: {', '.join(chapter.expected_topics)}")

        return _strip_duplicate_title(_clean_chapter_output(result), chapter.title)

    # -----------------------------------------------------------------------
    # Phase 4b: Edit pass

    def _edit_pass(self, results: list, fc_results: list = None) -> list:
        """Run one LLM editing pass over each chapter to improve quality.

        fc_results, if provided, are FactCheckResult objects aligned with
        results by index. Flagged claims are passed to the editor so it can
        soften or remove unsupported statements.
        """
        from llm.ollama_client import call
        from llm.prompts import EDIT_PASS_PROMPT

        fc_map = {}
        if fc_results:
            fc_map = {fc.chapter_title: fc for fc in fc_results}

        edited = []
        for r in results:
            issues = []

            # Always enforce these — not conditional on chapter quality
            issues.append(
                "Remove ALL brand names and specific model numbers — "
                "replace with generic tool or equipment names (e.g. 'bench plane', not 'Stanley No. 4')"
            )
            issues.append(
                "Remove any code block fences (``` or ```markdown) — "
                "output must be plain markdown prose, never wrapped in code blocks"
            )
            issues.append(
                "Remove any historical padding or tangential stories not directly relevant "
                "to practical technique. Cut anything a practitioner would skip."
            )

            if r.word_count < MIN_CHAPTER_WORDS:
                issues.append("Chapter is very thin — expand with more detail")
            if r.missing_topics:
                issues.append(f"These topics were expected but may be thin: {', '.join(r.missing_topics[:5])}")

            # Inject flagged claims — require removal or softening (not just annotation)
            fc = fc_map.get(r.spec.title)
            if fc and fc.flagged_claims:
                flagged_texts = "; ".join(c.text[:80] for c in fc.flagged_claims[:5])
                issues.append(
                    f"The following claims were NOT found in source material — "
                    f"REMOVE or soften to 'typically' or 'generally' (do not just annotate): "
                    f"{flagged_texts}"
                )

            # Detect truncation: chapter ending mid-sentence
            stripped_content = r.content.rstrip()
            if stripped_content and stripped_content[-1] not in ".!?":
                issues.append(
                    "The chapter appears to end mid-sentence — complete it properly "
                    "with a full conclusion or summary paragraph"
                )

            prompt = EDIT_PASS_PROMPT.format(
                topic=self.topic,
                chapter_title=r.spec.title,
                chapter_draft=r.content[:14000],
                issues="\n".join(f"- {i}" for i in issues),
            )

            improved = _resilient_call(
                _model(), prompt,
                temperature=0.25, timeout=240,
                num_ctx=CHAPTER_CTX, num_predict=CHAPTER_PREDICT,
            )
            if improved and len(improved.strip()) > 100:
                improved = _clean_chapter_output(improved)
                improved = _strip_duplicate_title(improved, r.spec.title)
                new_wc = len(improved.split())
                log.info(f"[EDITOR]   {r.spec.title}: {r.word_count} → {new_wc} words")
                covered = self._measure_coverage(improved, r.spec.expected_topics)
                edited.append(ChapterResult(
                    spec=r.spec,
                    content=improved,
                    word_count=new_wc,
                    source_files=r.source_files,
                    covered_topics=covered,
                    missing_topics=[t for t in r.spec.expected_topics if t not in covered],
                ))
                continue
            else:
                log.warning(f"[EDITOR] edit pass failed for '{r.spec.title}' after retries — keeping original")

            edited.append(r)

        return edited

    # -----------------------------------------------------------------------
    # Phase 4c: Fact-check pass

    def _fact_check_pass(self, results: list, chapter_passages: dict,
                         full_texts: dict) -> tuple:
        """Cross-reference every chapter against source material.

        Returns (updated_results, fc_results) where updated_results have
        annotated content and fc_results are FactCheckResult objects.
        """
        from curriculum.fact_checker import FactChecker

        checker = FactChecker(self.topic)
        fc_results = []
        updated = []

        for r in results:
            passages = chapter_passages.get(r.spec.title, "")
            claims_passed = self._chapter_claims_used.get(r.spec.title) or []
            try:
                fc = None
                # Preferred path: if the writer was given a claim list AND the
                # chapter contains [CN] markers, verify by direct lookup. No
                # LLM call, no fuzzy match — deterministic, near-instant.
                if claims_passed:
                    fc = checker.check_by_markers(
                        chapter_title=r.spec.title,
                        chapter_content=r.content,
                        claims_passed=claims_passed,
                        source_files=r.source_files,
                    )
                if fc is None:
                    # Fallback to legacy LLM-extract + corpus search
                    fc = checker.check_chapter(
                        chapter_title=r.spec.title,
                        chapter_content=r.content,
                        source_passages=passages,
                        source_files=r.source_files,
                        full_source_texts=full_texts,
                    )
                fc_results.append(fc)
                updated.append(ChapterResult(
                    spec=r.spec,
                    content=fc.annotated_content,
                    word_count=len(fc.annotated_content.split()),
                    source_files=r.source_files,
                    covered_topics=r.covered_topics,
                    missing_topics=r.missing_topics,
                ))
            except Exception as e:
                log.warning(f"[CHECKER] Failed for '{r.spec.title}': {e}")
                updated.append(r)

        return updated, fc_results

    # -----------------------------------------------------------------------
    # Phase 4c2: Reground pass for low-confidence chapters

    def _reground_pass(self, results: list, fc_results: list,
                       chapter_passages: dict) -> list:
        """Regenerate chapters whose fact-check confidence is below threshold.

        Uses a stricter prompt that explicitly forbids adding facts not present
        in source passages. Only fires for chapters that failed the FC gate.
        """
        from llm.ollama_client import call
        from llm.prompts import REGROUND_CHAPTER_PROMPT

        fc_map = {fc.chapter_title: fc for fc in fc_results}
        regrounded = []

        for r in results:
            fc = fc_map.get(r.spec.title)
            if fc and fc.confidence_score < FC_REGEN_THRESHOLD:
                log.info(
                    f"[REGROUND] '{r.spec.title}' score={fc.confidence_score:.0%} "
                    f"— regenerating from source passages only "
                    f"({len(fc.flagged_claims)} flagged claims)"
                )
                passages = chapter_passages.get(r.spec.title, "")
                flagged_text = "\n".join(
                    f"- {c.text[:200]}" for c in fc.flagged_claims[:15]
                ) if fc.flagged_claims else "(none specifically extracted)"
                prompt = REGROUND_CHAPTER_PROMPT.format(
                    topic=self.topic,
                    chapter_title=r.spec.title,
                    source_passages=passages[:16000],
                    flagged_claims=flagged_text,
                    chapter_draft=r.content[:4000],
                )
                try:
                    result = call(
                        _model(), prompt,
                        temperature=0.2, timeout=300,
                        num_ctx=CHAPTER_CTX, num_predict=CHAPTER_PREDICT,
                    )
                    if result and len(result.strip()) > 100:
                        result = _clean_chapter_output(result)
                        result = _strip_duplicate_title(result, r.spec.title)
                        new_wc = len(result.split())
                        log.info(f"[REGROUND] '{r.spec.title}': {r.word_count} → {new_wc} words")
                        regrounded.append(ChapterResult(
                            spec=r.spec,
                            content=result,
                            word_count=new_wc,
                            source_files=r.source_files,
                            covered_topics=r.covered_topics,
                            missing_topics=r.missing_topics,
                        ))
                        continue
                except Exception as e:
                    log.warning(f"[REGROUND] Failed for '{r.spec.title}': {e}")

            regrounded.append(r)

        return regrounded

    # -----------------------------------------------------------------------
    # Phase 4c4b: Foreign-tool check — flag chapters that reference tools
    # from a different craft (a bench plane in a leatherworking textbook is
    # wrong advice that could damage a hide).

    _FOREIGN_TOOLS = {
        "leatherworking": [
            r'\bbench plane\b', r'\bjack plane\b', r'\bhand plane\b',
            r'\blathe\b', r'\brouter table\b', r'\bplaner\b',
            r'\banvil\b', r'\bforge\b', r'\bcrucible\b',
            r'\bdrill press\b', r'\bbench grinder\b', r'\bdie grinder\b',
            r'\bwelder\b', r'\btorch\b', r'\bsoldering iron\b',
            r'\bchainsaw\b', r'\bmiter saw\b', r'\btable saw\b',
        ],
        "blacksmithing": [
            r'\bleather punch\b', r'\bskiving knife\b',
            r'\bbench plane\b', r'\brouter table\b',
            r'\bsewing machine\b', r'\bawl(?!\s*shape)\b',
        ],
        "carpentry": [
            r'\banvil\b', r'\bforge\b', r'\btanning vat\b',
            r'\bskiving knife\b', r'\bswivel knife\b',
        ],
        "woodworking": [
            r'\banvil\b', r'\bforge\b', r'\bskiving knife\b',
        ],
        "welding": [
            r'\bbench plane\b', r'\bskiving knife\b', r'\btanning vat\b',
        ],
        "blacksmith": [
            r'\bleather punch\b', r'\bbench plane\b', r'\bsewing machine\b',
        ],
    }

    def _foreign_tool_check(self, results: list) -> list:
        topic_key = self.topic.replace('_', ' ').lower().strip()
        patterns = []
        for key, pats in self._FOREIGN_TOOLS.items():
            if key in topic_key or topic_key in key:
                patterns.extend(pats)
        if not patterns:
            return results

        combined = re.compile('|'.join(patterns), re.IGNORECASE)
        flagged = []
        for r in results:
            hits = combined.findall(r.content)
            if hits:
                # Strip sentences containing each foreign-tool mention
                content = r.content
                removed = 0
                for hit_pattern in patterns:
                    sent_re = re.compile(
                        r'(?:[^.!?\n]*?\b' + hit_pattern.strip('\\b') +
                        r'\b[^.!?\n]*[.!?])',
                        re.IGNORECASE,
                    )
                    new, n = sent_re.subn(' ', content)
                    if n:
                        content = new
                        removed += n
                if removed:
                    content = re.sub(r' {2,}', ' ', content)
                    content = re.sub(r'\n{3,}', '\n\n', content).strip()
                    log.info(
                        f"[FOREIGN-TOOL] '{r.spec.title}': stripped {removed} "
                        f"sentence(s) referencing foreign tools "
                        f"({', '.join(sorted(set(hits))[:5])})"
                    )
                    flagged.append(ChapterResult(
                        spec=r.spec,
                        content=content,
                        word_count=len(content.split()),
                        source_files=r.source_files,
                        covered_topics=r.covered_topics,
                        missing_topics=r.missing_topics,
                    ))
                    continue
            flagged.append(r)
        return flagged

    # -----------------------------------------------------------------------
    # Phase 4c4: Topic-drift filter — catch off-topic LLM drift

    def _topic_drift_filter(self, results: list) -> list:
        """Reject chapters where the topic word/root appears so rarely that
        the LLM has clearly drifted (e.g. wrote a woodworking chapter inside
        a leatherworking textbook). Replace with a placeholder stub so the
        next compile cycle can re-attempt with better grounding.
        """
        topic_root = self.topic.replace('_', ' ').lower()
        # Derive root forms so "leatherworking" matches "leather"
        roots = {topic_root}
        for stem in topic_root.split():
            if len(stem) > 5:
                roots.add(stem)
                # strip common suffixes
                for suf in ("working", "smithing", "making", "ing"):
                    if stem.endswith(suf) and len(stem) > len(suf) + 3:
                        roots.add(stem[:-len(suf)])

        filtered = []
        for r in results:
            body_lower = r.content.lower()
            # Count occurrences of any root variant
            hits = sum(body_lower.count(root) for root in roots)
            words = max(r.word_count, 1)
            density = hits / (words / 100)  # hits per 100 words
            # A leatherworking chapter should have leather/leatherwork
            # mentioned at least once per ~80 words.  Below ~0.5/100 words
            # the chapter has drifted to a different domain.
            if density < 0.5:
                log.warning(
                    f"[DRIFT] '{r.spec.title}': {hits} topic hits in "
                    f"{words} words (density={density:.2f}/100) — replacing "
                    f"with placeholder"
                )
                stub = (
                    f"## Summary\n\n*This chapter was flagged for topic drift "
                    f"and removed.* The original draft used content from a "
                    f"different craft instead of {topic_root}. Re-harvesting "
                    f"on the next cycle should improve coverage.\n\n"
                    f"**Expected topics:** {', '.join(r.spec.expected_topics[:8])}"
                )
                filtered.append(ChapterResult(
                    spec=r.spec,
                    content=stub,
                    word_count=len(stub.split()),
                    source_files=r.source_files,
                    covered_topics=[],
                    missing_topics=r.spec.expected_topics,
                ))
            else:
                filtered.append(r)
        return filtered

    # -----------------------------------------------------------------------
    # Phase 4c5b: Semantic paragraph dedup — catches paraphrased repeats

    def _semantic_paragraph_dedup(self, results: list) -> list:
        """Within each chapter, find paragraph pairs that say substantially
        the same thing (even if reworded) and drop the second occurrence.

        Lexical pre-filter (Jaccard on significant tokens) keeps it cheap —
        LLM only judges candidate pairs above the lexical threshold.
        """
        from llm.ollama_client import call

        _STOP = {
            "the","a","an","and","or","but","in","on","at","to","for","of",
            "with","by","from","this","that","these","those","is","are","was",
            "were","be","been","being","have","has","had","do","does","did",
            "you","your","it","its","not","can","may","will","would","should",
            "use","using","used","make","made","ensure","ensures","apply",
            "applies","prevent","prevents","also","then","when","while",
        }

        def _signature(text: str) -> set:
            return {
                w for w in re.findall(r'\b[a-z]{4,}\b', text.lower())
                if w not in _STOP
            }

        def _para_split(content: str) -> list[tuple[int, int, str]]:
            """Return (start_line, end_line, text) for each paragraph block,
            skipping headings, code, blockquotes, and very short bits.
            """
            paras = []
            lines = content.split('\n')
            buf: list[str] = []
            start = 0
            for i, line in enumerate(lines):
                # Boundary: blank line or new heading
                if line.strip() == '' or re.match(r'^#{1,6}\s', line):
                    if buf:
                        text = '\n'.join(buf).strip()
                        if len(text) >= 80 and not text.startswith(('>', '```')):
                            paras.append((start, i, text))
                        buf = []
                    start = i + 1
                    continue
                # Skip list items — they're naturally short, dedup poorly
                if line.lstrip().startswith(('-', '*', '1.', '2.', '3.')):
                    if buf:
                        text = '\n'.join(buf).strip()
                        if len(text) >= 80 and not text.startswith(('>', '```')):
                            paras.append((start, i, text))
                        buf = []
                    start = i + 1
                    continue
                buf.append(line)
            if buf:
                text = '\n'.join(buf).strip()
                if len(text) >= 80:
                    paras.append((start, len(lines), text))
            return paras

        deduped = []
        for r in results:
            paras = _para_split(r.content)
            if len(paras) < 2:
                deduped.append(r)
                continue

            # Compute signatures, find candidate pairs by Jaccard ≥ 0.5
            sigs = [(i, s, e, t, _signature(t)) for i, (s, e, t) in enumerate(paras)]
            drop_indices: set = set()
            for a in range(len(sigs)):
                if a in drop_indices:
                    continue
                ia, sa, ea, ta, sga = sigs[a]
                if len(sga) < 5:
                    continue
                for b in range(a + 1, len(sigs)):
                    if b in drop_indices:
                        continue
                    ib, sb, eb, tb, sgb = sigs[b]
                    if len(sgb) < 5:
                        continue
                    inter = sga & sgb
                    union = sga | sgb
                    jaccard = len(inter) / len(union) if union else 0
                    if jaccard < 0.5:
                        continue

                    # LLM judges intent — only on candidates that pass Jaccard
                    prompt = (
                        f"[INST]Do these two paragraphs say substantially the "
                        f"same thing — same procedure, same facts, same advice — "
                        f"just in different words? Answer with exactly one word: "
                        f"yes or no.\n\n"
                        f"PARAGRAPH A:\n{ta[:600]}\n\n"
                        f"PARAGRAPH B:\n{tb[:600]}\n\n"
                        f"Answer:[/INST]"
                    )
                    try:
                        reply = call(_model(), prompt, temperature=0.0,
                                     timeout=30, num_ctx=2048, num_predict=8)
                    except Exception:
                        continue
                    if reply and reply.strip().lower().startswith("y"):
                        drop_indices.add(b)

            if not drop_indices:
                deduped.append(r)
                continue

            # Remove from bottom up so indices stay valid
            lines = r.content.split('\n')
            drop_ranges = sorted(
                [(sigs[i][1], sigs[i][2]) for i in drop_indices],
                key=lambda x: -x[0],
            )
            for s, e in drop_ranges:
                del lines[s:e]
            new_content = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
            new_wc = len(new_content.split())
            log.info(
                f"[SEMDEDUP] '{r.spec.title}': removed {len(drop_indices)} "
                f"paraphrased paragraph(s), {r.word_count} → {new_wc} words"
            )
            deduped.append(ChapterResult(
                spec=r.spec,
                content=new_content,
                word_count=new_wc,
                source_files=r.source_files,
                covered_topics=r.covered_topics,
                missing_topics=r.missing_topics,
            ))
        return deduped

    # -----------------------------------------------------------------------
    # Phase 4c5: Redundancy merge

    def _redundancy_merge(self, results: list) -> list:
        """For each pair of chapters with overlapping section headings, trim
        the duplicate from the LATER chapter (keeps the first treatment).
        Reduces the "same content repeated multiple times" pattern.
        """
        # Collect (chapter_index, heading_text, start_offset, end_offset) tuples
        all_sections: list = []
        for i, r in enumerate(results):
            lines = r.content.split('\n')
            section_starts = []
            for ln, line in enumerate(lines):
                m = re.match(r'^(#{2,4})\s+(.+?)\s*$', line)
                if m:
                    section_starts.append((ln, m.group(1), m.group(2)))
            section_starts.append((len(lines), '', ''))
            for j in range(len(section_starts) - 1):
                ln, lvl, hdr = section_starts[j]
                end_ln = section_starts[j + 1][0]
                norm = re.sub(r'^\d+[\.\)]\s*', '', hdr.lower()).strip()
                # Skip pedagogical wrappers — those SHOULD repeat
                if norm in {"summary", "key takeaways", "review questions",
                            "answer key", "learning outcomes", "try this"}:
                    continue
                all_sections.append((i, ln, end_ln, norm))

        # Group by normalised heading
        by_heading: dict = {}
        for i, start, end, norm in all_sections:
            by_heading.setdefault(norm, []).append((i, start, end))

        # For headings appearing in 2+ chapters, trim later occurrences
        trims: dict = {}  # {chapter_idx: [(start_ln, end_ln), ...]}
        for norm, occurrences in by_heading.items():
            if len(occurrences) < 2:
                continue
            # Keep the first, trim the rest
            for i, start, end in occurrences[1:]:
                trims.setdefault(i, []).append((start, end))

        if not trims:
            return results

        merged = []
        total_trimmed = 0
        for i, r in enumerate(results):
            if i not in trims:
                merged.append(r)
                continue
            lines = r.content.split('\n')
            # Trim from bottom up so indices stay valid
            drop_ranges = sorted(trims[i], key=lambda x: -x[0])
            for start, end in drop_ranges:
                del lines[start:end]
                total_trimmed += 1
            new_content = '\n'.join(lines)
            new_content = re.sub(r'\n{3,}', '\n\n', new_content).strip()
            new_wc = len(new_content.split())
            log.info(
                f"[MERGE] '{r.spec.title}': trimmed {len(drop_ranges)} "
                f"duplicate section(s), {r.word_count} → {new_wc} words"
            )
            merged.append(ChapterResult(
                spec=r.spec,
                content=new_content,
                word_count=new_wc,
                source_files=r.source_files,
                covered_topics=r.covered_topics,
                missing_topics=r.missing_topics,
            ))
        log.info(f"[MERGE] {total_trimmed} redundant sections removed across {len(trims)} chapters")
        try:
            from watchdog import wd
            wd.emit("compile", "filter_activity",
                    name="redundancy_merge", trimmed=total_trimmed)
        except Exception:
            pass
        return merged

    # -----------------------------------------------------------------------
    # Phase 4d-ped: Pedagogical enrichment

    def _pedagogy_pass(self, results: list) -> list:
        """Add learning-aid scaffolding to each chapter without introducing
        new facts: rationales on procedure steps, a 'Try This' practice
        prompt, and 3 review questions. Pure structural augmentation.
        """
        from llm.prompts import PEDAGOGY_ENRICH_PROMPT

        enriched = []
        for r in results:
            prompt = PEDAGOGY_ENRICH_PROMPT.format(
                topic=self.topic,
                chapter_title=r.spec.title,
                chapter_content=r.content[:14000],
            )
            result = _resilient_call(
                _model(), prompt,
                temperature=0.25, timeout=300,
                num_ctx=CHAPTER_CTX, num_predict=CHAPTER_PREDICT,
            )
            if result and len(result.strip()) > len(r.content) * 0.7:
                cleaned = _clean_chapter_output(result)
                cleaned = _strip_duplicate_title(cleaned, r.spec.title)
                new_wc = len(cleaned.split())
                log.info(f"[PEDAGOGY] '{r.spec.title}': {r.word_count} → {new_wc} words")
                enriched.append(ChapterResult(
                    spec=r.spec,
                    content=cleaned,
                    word_count=new_wc,
                    source_files=r.source_files,
                    covered_topics=r.covered_topics,
                    missing_topics=r.missing_topics,
                ))
                continue
            else:
                log.warning(f"[PEDAGOGY] '{r.spec.title}': retries exhausted, keeping original")
            enriched.append(r)
        return enriched

    # -----------------------------------------------------------------------
    # Phase 5b: Cross-chapter coherence report

    def _write_coherence_report(self, results: list):
        """Scan chapters for overlapping topic coverage and contradictory
        numeric specs about the same subject. Writes <topic>_coherence.md.
        Does not auto-edit — surfaces issues for human review.
        """
        # Build {section_heading: [chapter_titles]} to find duplicates
        heading_to_chapters: dict = {}
        for r in results:
            for line in r.content.split('\n'):
                m = re.match(r'^#{2,3}\s+(.+)$', line.strip())
                if m:
                    h = m.group(1).lower().strip()
                    # Normalise: drop numbering, articles
                    h = re.sub(r'^\d+[\.\)]\s*', '', h)
                    h = re.sub(r'^(the|a|an)\s+', '', h)
                    heading_to_chapters.setdefault(h, []).append(r.spec.title)

        duplicates = {h: chs for h, chs in heading_to_chapters.items()
                      if len(set(chs)) > 1}

        # Detect contradictory specs (same subject, different numeric values)
        # Match phrases like "<X> minutes", "<X>°C", "<X> psi" near a noun
        spec_pat = re.compile(
            r'(\b\w{4,}(?:\s+\w+){0,2})\s+(?:for|to|at|of|is|are)?\s*'
            r'(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*'
            r'(°[CF]|minutes?|hours?|psi|mm|inch(?:es)?|°|%)\b',
            re.IGNORECASE,
        )
        spec_map: dict = {}
        for r in results:
            for m in spec_pat.finditer(r.content):
                subject = m.group(1).lower().strip()
                value = m.group(2)
                unit = m.group(3).lower()
                key = (subject, unit)
                spec_map.setdefault(key, []).append((value, r.spec.title))

        contradictions = []
        for (subj, unit), vals in spec_map.items():
            uniq_values = set(v for v, _ in vals)
            uniq_chapters = set(c for _, c in vals)
            if len(uniq_values) > 1 and len(uniq_chapters) > 1:
                contradictions.append((subj, unit, vals))

        if not duplicates and not contradictions:
            return

        lines = [f"# {self.topic.replace('_',' ').title()} — Coherence Report\n"]
        if duplicates:
            lines += ["## Duplicate Section Headings Across Chapters", ""]
            for h, chs in sorted(duplicates.items()):
                lines.append(f"- **{h}** appears in: {', '.join(sorted(set(chs)))}")
            lines.append("")
        if contradictions:
            lines += ["## Possible Numeric Contradictions", ""]
            for subj, unit, vals in contradictions[:30]:
                vs = ", ".join(f"`{v}{unit}` ({c})" for v, c in vals)
                lines.append(f"- **{subj}** ({unit}): {vs}")

        out_path = os.path.join(self.out_dir, f"{self.topic}_coherence.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log.info(
            f"[COMPILER] Coherence report: {out_path} "
            f"({len(duplicates)} duplicates, {len(contradictions)} contradictions)"
        )

    # -----------------------------------------------------------------------
    # Phase 4c3: Strip still-flagged sentences

    def _strip_flagged_sentences(self, results: list, fc_results: list) -> list:
        """For any claim still flagged after reground + edit, remove the
        sentence from the chapter. Better to ship a shorter accurate chapter
        than one studded with hallucinated specifics behind silent markers.
        """
        fc_map = {fc.chapter_title: fc for fc in fc_results}
        stripped = []
        total_removed = 0
        for r in results:
            fc = fc_map.get(r.spec.title)
            if not fc or not fc.flagged_claims:
                stripped.append(r)
                continue

            content = r.content
            removed = 0
            for c in fc.flagged_claims:
                claim_text = c.text.strip()
                if len(claim_text) < 15:
                    continue
                # Match the claim sentence flexibly — allow leading whitespace
                # and trailing punctuation drift from edit pass
                key = re.escape(claim_text[:120])
                pattern = re.compile(
                    r'\s*' + key + r'[^\n.!?]*[.!?]?',
                    re.IGNORECASE
                )
                new_content, n = pattern.subn(" ", content)
                if n:
                    content = new_content
                    removed += n

            if removed:
                # Collapse runs of whitespace left by removals
                content = re.sub(r' {2,}', ' ', content).strip()
                content = re.sub(r'\n{3,}', '\n\n', content)
                new_wc = len(content.split())
                log.info(
                    f"[STRIP] '{r.spec.title}': removed {removed} flagged "
                    f"sentence(s), {r.word_count} → {new_wc} words"
                )
                total_removed += removed
                stripped.append(ChapterResult(
                    spec=r.spec,
                    content=content,
                    word_count=new_wc,
                    source_files=r.source_files,
                    covered_topics=r.covered_topics,
                    missing_topics=r.missing_topics,
                ))
            else:
                stripped.append(r)
        if total_removed:
            try:
                from watchdog import wd
                wd.emit("compile", "filter_activity",
                        name="strip_flagged", stripped=total_removed)
            except Exception:
                pass
        return stripped

    # -----------------------------------------------------------------------
    # Phase 4d: Image fetching

    def _fetch_images(self, results: list) -> list:
        """
        Fetch reference images per chapter section.
        - One image after the chapter intro (chapter title search)
        - One image per ### subsection that describes a technique or project
        - Cap: MAX_IMAGES_PER_CHAPTER total per chapter
        """
        try:
            from curriculum.image_fetcher import fetch_tool_image
        except Exception:
            return results

        images_dir = os.path.join(self.out_dir, "images")
        os.makedirs(images_dir, exist_ok=True)

        updated = []
        for r in results:
            content, img_count = _inject_section_images(
                content=r.content,
                chapter_title=r.spec.title,
                topic=self.topic,
                images_dir=images_dir,
                out_dir=self.out_dir,
                fetch_fn=fetch_tool_image,
            )
            if img_count:
                log.info(f"[IMG] {r.spec.title}: {img_count} image(s) inserted")
            updated.append(ChapterResult(
                spec=r.spec,
                content=content,
                word_count=r.word_count,
                source_files=r.source_files,
                covered_topics=r.covered_topics,
                missing_topics=r.missing_topics,
            ))

        return updated

    # -----------------------------------------------------------------------
    # Phase 5: Coverage measurement

    def _measure_coverage(self, content: str, expected_topics: list) -> list:
        content_lower = content.lower()
        covered = []
        for t in expected_topics:
            tl = t.lower().strip()
            if not tl:
                continue
            if re.search(r'\b' + re.escape(tl) + r'\b', content_lower):
                covered.append(t)
        return covered

    # -----------------------------------------------------------------------
    # Phase 6: Write outputs

    _LOW_TRUST_SOURCES = frozenset({"reddit", "duckduckgo", "hub", "cited"})

    def _reliability_lookup(self) -> tuple[dict, dict]:
        """Pre-load file_origins + claims indexed by source_file once per textbook write.
        Returns (origins_map, claims_by_file). Both are best-effort — empty if missing."""
        try:
            from agent_stats import _load as _load_stats, _origins_path
            origins = _load_stats(_origins_path()).get(self.topic, {})
        except Exception:
            origins = {}
        try:
            from claims_db import _load as _load_claims
            db = _load_claims(self.topic)
            by_file: dict = {}
            for c in db.get("claims", []):
                by_file.setdefault(c.get("source_file", "?"), []).append(c)
        except Exception:
            by_file = {}
        return origins, by_file

    def _chapter_reliability_block(self, r: ChapterResult,
                                   origins: dict, claims_by_file: dict) -> str:
        """One-line reliability blockquote prepended to each chapter."""
        if not r.source_files:
            return ""
        srcs = list(dict.fromkeys(r.source_files))  # preserve order, dedup
        n_files = len(srcs)
        n_low_files = sum(1 for f in srcs if origins.get(f, "?") in self._LOW_TRUST_SOURCES)
        n_high_files = n_files - n_low_files

        claims_high = 0
        claims_low = 0
        for f in srcs:
            for c in claims_by_file.get(f, []):
                if c.get("low_trust"):
                    claims_low += 1
                else:
                    claims_high += 1

        parts = [f"{n_files} source files"]
        if n_low_files:
            parts.append(f"{n_high_files} reference, {n_low_files} community")
        total_claims = claims_high + claims_low
        if total_claims:
            parts.append(f"{total_claims} claims")
            if claims_low:
                parts.append(f"{claims_low} low-trust ({claims_low * 100 // total_claims}%)")
        return f"> **Reliability:** {' | '.join(parts)}.\n\n"

    def _experimental_appendix(self, used_sources: set,
                                origins: dict, claims_by_file: dict) -> str:
        """Render an appendix of low-trust claims from sources used in the textbook.
        Surfaces practitioner/forum content as anecdotal rather than letting it
        blend into reference-grade prose."""
        # Collect low-trust claims from sources actually used in the textbook
        low_trust_by_source: dict = {}
        for fname in used_sources:
            src = origins.get(fname, "?")
            if src not in self._LOW_TRUST_SOURCES:
                continue
            for c in claims_by_file.get(fname, []):
                if not c.get("low_trust"):
                    continue
                text = c.get("text", "").strip()
                if len(text) < 80:  # skip thin claims
                    continue
                low_trust_by_source.setdefault(src, []).append(text)

        if not low_trust_by_source:
            return ""

        # Cap per source so the appendix doesn't explode
        PER_SOURCE_CAP = 15
        lines = [
            "## Appendix: Practitioner Tips & Anecdotal Notes",
            "",
            "> These items are compiled from community sources (forums, web search results).",
            "> They have not been cross-checked against reference texts — treat as practitioner",
            "> experience rather than authoritative procedure.",
            "",
        ]
        for src in sorted(low_trust_by_source.keys()):
            items = low_trust_by_source[src][:PER_SOURCE_CAP]
            label = {"reddit": "Reddit (community discussion)",
                     "duckduckgo": "Web search results",
                     "hub": "Hub-shared content",
                     "cited": "Cited references"}.get(src, src.title())
            lines.append(f"### From {label}")
            lines.append("")
            for item in items:
                # Dedup whitespace + drop trailing markers
                clean = re.sub(r'\s+', ' ', item).strip().rstrip('.')
                lines.append(f"- {clean}.")
            lines.append("")
        return "\n".join(lines)

    def _write_textbook(self, results: list) -> str:
        # Strip [CN] attribution markers — they were the verification artifact,
        # not reader-facing. Done HERE (just before assembly) so every prior
        # pass had access to the markers for fact-check / reground / etc.
        from curriculum.fact_checker import strip_claim_markers
        cleaned_results = []
        markers_stripped = 0
        for r in results:
            clean = strip_claim_markers(r.content)
            if clean != r.content:
                markers_stripped += r.content.count("[C")  # approximate
            cleaned_results.append(ChapterResult(
                spec=r.spec,
                content=clean,
                word_count=len(clean.split()),
                source_files=r.source_files,
                covered_topics=r.covered_topics,
                missing_topics=r.missing_topics,
            ))
        if markers_stripped:
            log.info(f"[COMPILER] Stripped ~{markers_stripped} claim markers before assembly")
        results = cleaned_results

        toc_lines = []
        sections = []

        # Per-topic reference data for reliability headers and experimental appendix
        rel_origins, rel_claims_by_file = self._reliability_lookup()

        for i, r in enumerate(results, 1):
            anchor = re.sub(r'[^\w\s-]', '', r.spec.title.lower()).strip().replace(' ', '-')
            toc_lines.append(f"{i}. [{r.spec.title}](#{anchor})")
            header_block = self._chapter_reliability_block(
                r, rel_origins, rel_claims_by_file
            )
            sections.append(f"## {i}. {r.spec.title}\n\n{header_block}{r.content}")

        total_words = sum(r.word_count for r in results)
        all_sources = set(s for r in results for s in r.source_files)

        appendix = self._experimental_appendix(all_sources, rel_origins, rel_claims_by_file)

        body_blocks = ["\n\n---\n\n".join(sections)]
        if appendix:
            body_blocks.append("---")
            body_blocks.append(appendix)

        text = "\n".join([
            f"# {self.topic.replace('_', ' ').title()} — Complete Technical Guide",
            "",
            "## Table of Contents",
            "",
            "\n".join(toc_lines),
            "",
            "---",
            "",
            "\n\n".join(body_blocks),
            "",
            "---",
            "",
            f"*Compiled from {len(all_sources)} source files | "
            f"~{total_words:,} words | {len(results)} chapters*",
        ])

        text = _normalize_headings(text)

        out_path = os.path.join(self.out_dir, f"{self.topic}_textbook.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        log.info(f"[COMPILER] Textbook: {out_path} ({total_words:,} words, "
                 f"{len(all_sources)} sources)")

        # Export PDF
        try:
            from curriculum.export_pdf import export_pdf
            images_dir = os.path.join(self.out_dir, "images")
            pdf_path = export_pdf(out_path, images_dir=images_dir if os.path.isdir(images_dir) else None)
            if pdf_path:
                log.info(f"[COMPILER] PDF: {pdf_path}")
        except Exception as e:
            log.debug(f"[COMPILER] PDF export skipped: {e}")

        return out_path

    def _write_gap_report(self, results: list, file_index: dict) -> str:
        total_words = sum(r.word_count for r in results)
        all_used_sources = set(s for r in results for s in r.source_files)
        unused_files = [f for f in file_index if f not in all_used_sources]

        thin = [r for r in results if r.word_count < MIN_CHAPTER_WORDS]
        missing_topics = [(r.spec.title, r.missing_topics)
                          for r in results if r.missing_topics]

        lines = [
            f"# {self.topic.replace('_', ' ').title()} — Textbook Gap Report",
            "",
            f"**{len(file_index)} source files** | "
            f"**{total_words:,} total words** | "
            f"**{len(results)} chapters** | "
            f"**{len(all_used_sources)} sources used** | "
            f"**{len(unused_files)} sources unused**",
            "",
            "## Chapter Coverage",
            "",
        ]

        for r in results:
            flag = " ⚠ THIN" if r.word_count < MIN_CHAPTER_WORDS else ""
            missing_str = (f" | missing: *{', '.join(r.missing_topics[:5])}*"
                           if r.missing_topics else "")
            lines.append(
                f"| {r.spec.title} | {r.word_count} words | "
                f"{len(r.source_files)} sources{flag}{missing_str} |"
            )

        if thin:
            lines += [
                "",
                "## Thin Chapters (need more source material)",
                "",
            ]
            for r in thin:
                lines.append(f"- **{r.spec.title}** ({r.word_count} words) — "
                              f"search for: `{self.topic} {r.spec.title.lower()} tutorial`")

        if missing_topics:
            lines += [
                "",
                "## Topics Expected But Poorly Covered",
                "",
            ]
            for title, topics in missing_topics:
                lines.append(f"- **{title}**: {', '.join(topics)}")

        if unused_files:
            lines += [
                "",
                f"## Unused Source Files ({len(unused_files)} files not drawn from)",
                "",
                "*These files were collected but no chapter pulled content from them. "
                "They may cover topics not yet represented in the chapter plan.*",
                "",
            ]
            for fname in sorted(unused_files):
                entry = file_index[fname]
                top_hits = sorted(entry.lexicon_hits)[:5]
                lines.append(f"- `{fname}` ({entry.word_count} words) "
                              f"— lexicon: {', '.join(top_hits) if top_hits else 'none'}")

        lines += [
            "",
            "## Recommended Search Queries to Bolster Weak Areas",
            "",
        ]
        seen_queries = set()
        for r in thin[:6]:
            q = f"{self.topic} {r.spec.title.lower()} complete guide"
            if q not in seen_queries:
                lines.append(f"- `{q}`")
                seen_queries.add(q)
        for title, topics in missing_topics[:6]:
            for t in topics[:3]:
                q = f"{self.topic} {t} techniques step by step"
                if q not in seen_queries:
                    lines.append(f"- `{q}`")
                    seen_queries.add(q)

        # Build gap nodes — searchable strings for the next harvest cycle
        gap_nodes = []
        seen = set()

        def _add(node: str):
            n = node.strip()
            if n and n not in seen:
                seen.add(n)
                gap_nodes.append(n)

        for r in thin:
            _add(f"{self.topic} {r.spec.title.lower()} complete guide")
            _add(f"{self.topic} {r.spec.title.lower()} techniques procedures")
        for title, topics in missing_topics:
            for t in topics[:4]:
                _add(f"{self.topic} {t}")
                _add(f"{self.topic} {t} step by step")
        # Unused files: their top lexicon hits become nodes too
        for fname in unused_files[:10]:
            entry = file_index[fname]
            for hit in sorted(entry.lexicon_hits)[:3]:
                _add(f"{self.topic} {hit} practical guide")

        lines += [
            "",
            f"## Gap Nodes for Next Harvest Cycle ({len(gap_nodes)} nodes)",
            "",
            "*These will be injected into the research frontier automatically.*",
            "",
        ]
        for n in gap_nodes:
            lines.append(f"- `{n}`")

        gap_text = "\n".join(lines)
        out_path = os.path.join(self.out_dir, f"{self.topic}_gaps.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(gap_text)
        log.info(f"[COMPILER] Gap report: {out_path}")
        return out_path, gap_nodes


# ---------------------------------------------------------------------------

_LLM_PREAMBLE = re.compile(
    r'^(?:here(?:\'s|\s+is)\s+(?:(?:the|your|my|a|an)\s+)?(?:improved|revised|edited|rewritten|updated|corrected|enriched|rewrought|enhanced|expanded|reformatted|finalized|polished|refined|cleaned[\s-]?up)?\s*'
    r'(?:chapter|content|text|version|draft|rewrite|markdown|output|result|response)[^:\n]*:?\s*\n+|'
    r'(?:i\'ve|i\s+have|i\'ve|i\s+will|i\s+am|let\s+me)\s+(?:edited|improved|revised|updated|corrected|rewritten|enriched|enhanced|added|expanded|reformatted)[^\n]*\n+|'
    r'(?:below\s+is|the\s+following|here\s+is\s+the|here\'s\s+the|please\s+find)[^\n]*\n+|'
    r'^\s*(?:---+\s*\n+)+)',
    re.IGNORECASE | re.MULTILINE,
)

_CODE_FENCE = re.compile(r'^\s*```(?:markdown)?\s*\n?', re.MULTILINE)
_CODE_FENCE_CLOSE = re.compile(r'\n?\s*```\s*$', re.MULTILINE)

# Strip [⚠ UNVERIFIED] annotations — editor should have removed them; clean any that remain
_UNVERIFIED_ANNOTATION = re.compile(r'\s*\[⚠\s*UNVERIFIED[^\]]*\]', re.IGNORECASE)

# Strip claim-attribution debug markers like *(VOL_leatherworking_...)* that
# the LLM sometimes leaks into prose when given attributed claims. Catches
# both wrapped and unwrapped variants of the source-filename pattern.
_CITATION_MARKER = re.compile(
    r'\s*[\*\(\[]+\s*VOL_[A-Za-z0-9_]+[^*\)\]]*[\*\)\]]+',
    re.IGNORECASE,
)
_BARE_VOL_REF = re.compile(r'\s*VOL_[A-Za-z0-9_]+\b', re.IGNORECASE)


MAX_IMAGES_PER_CHAPTER = 5

# Section headings that suggest visual content worth illustrating
_TECHNIQUE_KEYWORDS = {
    "technique", "method", "procedure", "how to", "step", "process",
    "using", "applying", "cutting", "joining", "finishing", "sharpening",
    "fitting", "marking", "laying out", "assembling", "fastening",
    "project", "exercise", "practice", "build", "make", "construct",
    "weld", "sanding", "planing", "chiseling", "sawing", "drilling",
}


def _inject_section_images(
    content: str,
    chapter_title: str,
    topic: str,
    images_dir: str,
    out_dir: str,
    fetch_fn,
) -> tuple[str, int]:
    """
    Walk chapter lines. Insert images:
      1. After intro block (before first ## or ### heading) — chapter-level image
      2. After each ### heading whose text matches technique/project keywords

    Returns (modified_content, images_inserted_count).
    """
    lines = content.split("\n")
    out_lines = []
    img_count = 0
    intro_done = False
    seen_terms: set[str] = set()

    def _try_fetch(search_terms: list[str]) -> str | None:
        """Try each search term until we get an image, skip already-used terms."""
        for term in search_terms:
            t = term.lower().strip()
            if t in seen_terms:
                continue
            seen_terms.add(t)
            path = fetch_fn(term, images_dir)
            if path:
                return path
        return None

    def _image_md(path: str, alt: str) -> str:
        rel = os.path.relpath(path, out_dir)
        return f"\n![{alt}]({rel})\n"

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # --- Intro image: insert before first ## or ### heading ---
        if not intro_done and stripped.startswith("##"):
            if img_count < MAX_IMAGES_PER_CHAPTER:
                terms = _intro_image_terms(chapter_title, topic)
                path = _try_fetch(terms)
                if path:
                    out_lines.append(_image_md(path, chapter_title))
                    img_count += 1
            intro_done = True

        # --- Section image: after a ### heading that sounds visual ---
        if stripped.startswith("### "):
            heading_text = _strip_md_inline(stripped[4:].strip())
            out_lines.append(line)
            i += 1
            if img_count < MAX_IMAGES_PER_CHAPTER and _is_visual_section(heading_text):
                terms = [
                    f"{heading_text} {topic}",
                    heading_text,
                    f"{topic} {chapter_title} {heading_text}",
                ]
                path = _try_fetch(terms)
                if path:
                    out_lines.append(_image_md(path, heading_text))
                    img_count += 1
            continue

        out_lines.append(line)
        i += 1

    # Fallback: no ## headings found — insert after first paragraph
    if not intro_done and img_count == 0:
        terms = [chapter_title, f"{topic} {chapter_title}", topic]
        path = _try_fetch(terms)
        if path:
            insert_at = _find_post_intro(out_lines)
            out_lines.insert(insert_at, _image_md(path, chapter_title))
            img_count += 1

    return "\n".join(out_lines), img_count


_TITLE_NOISE = {
    "introduction", "overview", "fundamentals", "basics", "principles",
    "applications", "guide", "handbook", "manual", "to", "the", "a",
    "an", "for", "and", "of", "in", "with", "using", "part", "section",
    "basic", "advanced", "practical", "general", "essential", "complete",
}


def _intro_image_terms(chapter_title: str, topic: str) -> list[str]:
    """
    Build 3 progressively simpler search terms for the chapter intro image.
    Strips structural/noise words from the title to get shorter, more matchable phrases.
    """
    words = [w for w in chapter_title.split() if w.lower() not in _TITLE_NOISE]
    core = " ".join(words).strip()
    topic_clean = topic.replace("_", " ")
    if core and core.lower() != topic_clean.lower():
        return [f"{core} {topic_clean}", core, topic_clean]
    return [topic_clean, f"{topic_clean} workshop", f"{topic_clean} tools"]


_MD_INLINE = re.compile(r'\*{1,3}|_{1,3}|`|~~')
_NUMBERED_PREFIX = re.compile(r'^[\d]+(?:\.\d+)*[\.:]\s*|^[A-Z]\.\s+')


def _strip_md_inline(text: str) -> str:
    """Remove markdown bold/italic/code markers and numbered prefixes."""
    text = _MD_INLINE.sub('', text)
    text = _NUMBERED_PREFIX.sub('', text)
    return text.strip()


def _is_visual_section(heading: str) -> bool:
    """Return True if this section heading suggests a technique or project."""
    h = heading.lower()
    # Skip generic structural headings with no searchable content
    if len(h.split()) < 2:
        return False
    return any(kw in h for kw in _TECHNIQUE_KEYWORDS)


def _find_post_intro(lines: list[str]) -> int:
    """Find insertion index after the first paragraph block."""
    in_para = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith("#"):
            in_para = True
        elif in_para and not s:
            return i + 1
    return min(6, len(lines))


_LEADING_H1 = re.compile(r'^#\s+\**[^\n#]+\**\s*\n+', re.MULTILINE)
_HEADING_BOLD = re.compile(r'^(#{1,6})\s+\**(.+?)\**\s*$', re.MULTILINE)
_HEADING_NUM_PREFIX = re.compile(r'^\d+\.\s+')


def _normalize_headings(text: str) -> str:
    """Strip bold/italic markers and leading numbers from H2+ heading lines."""
    def clean(m):
        hashes = m.group(1)
        body = re.sub(r'\*{1,3}|_{1,3}', '', m.group(2)).strip()
        # Strip sub-section numbering (1. Nail Hammers → Nail Hammers) from H2/H3+
        if len(hashes) > 1:
            body = _HEADING_NUM_PREFIX.sub('', body)
        return f"{hashes} {body}"
    return _HEADING_BOLD.sub(clean, text)


def _strip_duplicate_title(text: str, title: str) -> str:
    """If the chapter body begins with a heading that matches (or closely
    matches) the chapter title, drop that heading. The compiler adds the
    `## N. Title` anchor itself — a duplicate `### Title` from the LLM is
    pure noise.
    """
    title_norm = re.sub(r'\W+', ' ', title).strip().lower()
    lines = text.split('\n')
    # Look in the first 5 non-empty lines for a near-duplicate heading
    looked = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        looked += 1
        if looked > 5:
            break
        m = re.match(r'^#{2,6}\s+(.+?)\s*$', line)
        if not m:
            continue
        line_title = re.sub(r'\W+', ' ', m.group(1)).strip().lower()
        if line_title == title_norm:
            del lines[i]
            # Also drop a trailing blank line that's now stranded
            if i < len(lines) and not lines[i].strip():
                del lines[i]
            break
    return '\n'.join(lines)


def _clean_chapter_output(text: str) -> str:
    """Strip LLM artifacts: code fences, preamble commentary, leftover annotations."""
    text = text.strip()
    # Strip opening code fence
    text = _CODE_FENCE.sub("", text)
    # Strip closing code fence
    text = _CODE_FENCE_CLOSE.sub("", text)
    text = text.strip()
    # Strip LLM preamble commentary
    text = _LLM_PREAMBLE.sub("", text)
    # Remove leftover [⚠ UNVERIFIED] markers — editor should fix these; we remove the marker
    text = _UNVERIFIED_ANNOTATION.sub("", text)
    # Strip claim-attribution debug markers — the LLM sometimes leaks
    # source-filename references into prose when given attributed claims
    text = _CITATION_MARKER.sub("", text)
    text = _BARE_VOL_REF.sub("", text)
    # Strip leading H1 title — the compiler adds ## N. Title as the chapter anchor
    text = _LEADING_H1.sub("", text, count=1)
    return text.strip()


def _model() -> str:
    from config import RESEARCHER_MODEL
    return RESEARCHER_MODEL


def _resilient_call(model: str, prompt: str, max_attempts: int = 3,
                    backoff_seconds: tuple = (15, 45), **kwargs) -> str | None:
    """Call the LLM with retry-on-failure. Survives transient Ollama outages
    (the kind that truncated cycle 1's edit pass during reboot). Retries on
    exception OR empty result. Returns None when all attempts fail.

    backoff_seconds[i] is the wait BEFORE attempt i+1. Default 15s, 45s = up
    to 60s of total wait, enough for an Ollama reboot.
    """
    import time
    from llm.ollama_client import call

    last_error = None
    for attempt in range(max_attempts):
        if attempt > 0:
            wait = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
            log.info(f"[LLM] retry {attempt + 1}/{max_attempts} after {wait}s wait "
                     f"(last error: {last_error})")
            time.sleep(wait)
        try:
            result = call(model, prompt, **kwargs)
            if result and len(result.strip()) > 20:
                return result
            last_error = "empty result"
        except Exception as e:
            last_error = e
    log.warning(f"[LLM] resilient_call failed after {max_attempts} attempts: {last_error}")
    return None


def compile_textbook(topic: str, lexicon: list, grit: list) -> tuple:
    """Entry point called from builder.py."""
    from config import VAULT_ROOT
    vault_path = os.path.join(VAULT_ROOT, topic)
    out_dir = os.path.join(vault_path, "curriculum")
    os.makedirs(out_dir, exist_ok=True)
    compiler = TextbookCompiler(topic, lexicon, grit, vault_path, out_dir)
    return compiler.compile()
