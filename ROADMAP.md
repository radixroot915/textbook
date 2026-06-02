# Harvester Roadmap

Living doc — next-steps queue, ordered by priority. Cross items off as they ship.

## Track A — Quality regression triage (in progress)

The v10 cycle-2 regression (0.64 vs v9's 0.75) traced to four post-write filters added during the v9→v10 audit cycle. Audit-of-audit confirmed: each filter was correct in isolation but compounded to shred prose coherence and pedagogy content.

- [x] **Disable named-entity validator's strip behavior** — changed to warn-only. Detection still records as `proper_noun_warnings` in stats but does not strip. Amy Roke / Hermès / brand-name references now survive.
- [x] **Exempt pedagogy blocks from post-write filters** — section-context tracking added. `## Try This`, `## Review Questions`, `## Answer Key`, `## Learning Outcomes`, `## Summary`, `## Key Takeaways` immune to hallucination_filter while in section scope.
- [x] **Reduce terminology canonicalization aggressiveness** — threshold raised from ≥3 occurrences to ≥6, AND now requires top variant ≥ 2× runner-up. Previously 161 replacements per cycle; expected ~10-30.
- [ ] **Re-run leatherworking** to confirm v9-equivalent quality returns. Target: score ≥ 0.74, facts ≥ 70%, pedagogy ≥ 60%.

## Session arc (continuing next session)

Shipped this session:

- [x] Track C Step 7 — `workflow.py quality TOPIC` (composes scorecards + claims DB + drift summary)
- [x] Track C Step 10 — `workflow.py verify TOPIC --sample N` (random-sample claims to markdown for manual review)
- [x] Track C Step 11 — Low-trust prompt hedging in `CLAIM_DRIVEN_CHAPTER_PROMPT` (must-not-be-sole-basis rule)
- [x] Track C Step 12 — `USAGE.md` + Open:/Suggested: lines on every workflow.py command exit
- [x] Track C Step 8 — Experimental appendix in compiled textbook (low-trust claims grouped by source, labeled anecdotal)
- [x] Track C Step 9 — Chapter reliability blockquote header (source files used, high vs low-trust claim counts)
- [x] Track B Step 4 — `workflow.py repair TOPIC` (fills missing classifications/claims, idempotent)
- [x] Track B Step 2 — Unified harvest loop. Extracted `main.run_harvest()`; both `main.main()` and `workflow.cmd_harvest()` call it. workflow.py now has quality gate + watchdog + fingerprint reset + deep-dive expansion + plateau detection it was missing.
- [x] Re-run leatherworking validation — completed. Cycle 1 = 0.73 (facts 70%, pedagogy 58%) vs pre-revert 0.70 (facts 72%, pedagogy 39%). Pedagogy exemption confirmed working (+19 points). Score 0.01 under usable floor. Cycle 2 regressed to 0.62 — separate issue, source exhaustion + 86% low-trust claim DB.
- [x] Track B Step 1 — `core/schemas.py` with TypedDict contracts for every JSON file (Claim, Classification, DriftLogEntry, KnowledgeMap, AgentStats) plus path helpers and load/save round-trips. QualityReport re-exported from canonical home. Existing call sites untouched; migration is incremental.
- [x] Track B Step 5 — Filter-effect tracker in watchdog. `filter_effects[cycle][filter_name][stat]` populated by `filter_activity` events. Wired emit calls in textbook_compiler for hallucination, terminology, redundancy_merge, strip_flagged. New alert rule `filter_over_stripping` fires when a filter's strip count jumps ≥ 2× cycle-over-cycle AND quality drops — the exact diagnostic that would have caught v10. Scorecard surfaces per-filter trend table.
- [x] Track B Step 6 — `tests/` directory with pytest fixtures: hallucination filter (4 tests covering strip/keep/pedagogy-exempt/proper-noun-warn), terminology (3 tests covering dominant/below-threshold/no-clear-winner), drift gate (5 tests covering rejects + accepts), watchdog scorecard (2 tests covering well-formed + filter-over-stripping alert), schemas (3 tests covering round-trip + paths). **17 tests pass** in 0.17s. Run via `python -m pytest tests/`.

All planned tracks done this session.

## Track D — Claim-ID attribution architecture (this session)

The verification pipeline pivoted from fuzzy match → direct lookup. Writer LLM is now instructed to tag every specific-bearing sentence with `[CN]` markers referencing the source claim ID. Fact-checker parses markers and looks up claim N directly in the list passed to the writer. No LLM call, no Jaccard, no keyword search — deterministic.

### What shipped

- [x] `[CN]`-prefixed claim rendering — `claims_db.py:render_claims_for_prompt`
- [x] Attribution rule added to `CLAIM_DRIVEN_CHAPTER_PROMPT` — explicit instruction with multi-claim syntax `[C1, C2]`
- [x] `parse_claim_markers` + `strip_claim_markers` helpers — `curriculum/fact_checker.py`
- [x] `FactChecker.check_by_markers` — verification via direct lookup; returns None when chapter has no markers (caller falls back to legacy LLM-extract path)
- [x] `_fact_check_pass` in textbook_compiler tries marker path first, legacy as fallback
- [x] Marker strip post-pass in `_write_textbook` — removes `[CN]` artifacts before reader render
- [x] `get_claims_for_chapter` fallback — returns top-N most-recent when keyword match is empty (closes the "fallback writer skips attribution" gap)
- [x] 15 unit tests in `test_claim_markers.py` covering parse / strip / verify-via-lookup
- [x] `claim-id-verification` probe — end-to-end probe of the new architecture
- [x] `claim-attribution` probe — measures attribution rate at production scale

### Probe + smoke validation

- `claim-attribution` probe at 40 claims: **100% attribution rate, 0 hallucinated IDs** (probe PASS)
- Smoke run with marker path active: marker-path chapters had **0 flagged claims each** (vs 4-10 in legacy chapters). Aggregate score muted by `only 4 chapters — corpus too thin` quality-gate alert from the smoke chapter cap.

### Probe framework also shipped

`workflow.py probe <name>` runs small isolated verification questions in ~30s-2min. Use BEFORE expensive changes:

- `claim-attribution` — does writer follow attribution rules?
- `claim-id-verification` — end-to-end marker → lookup → strip
- `ollama-latency` — round-trip on small prompts
- `drift-gate-precision` — does drift gate reject off-topic nodes? (Currently FAILS — gate is too permissive on borderline LLM-fallback cases)

### Reverts also done this session

- Source rebalancing (wikipedia/wikibooks min_text_length lowered, node sanitization) — reverted
- DB-aware fact-checker rewrite (Jaccard + plural stem + abs overlap) — reverted

Both rolled back per the "broader-and-cleaner not confirmed at full scale" decision tree. Replaced by the claim-ID architecture above.

## Next session ready-to-run

1. **Full run on current code** (`python main.py leatherworking 10 1 2`) — apples-to-apples vs best run. With fallback-attribution wired, all chapters should hit marker path. Looking for flagged-claim count near zero across all chapters.
2. **If full run hits ≥ 0.74 → M2 attempt #1** (reproducibility). Run a second full at identical code.
3. **If full run < 0.74** → check whether the issue is (a) marker path not firing (look for `CHECKER:ID` log lines per chapter), (b) writer ignoring attribution rule, (c) something else.
4. **drift-gate-precision probe currently FAILS** — gate accepts "Mythology" / "Football tactics" as leatherworking-adjacent. Tighten LLM gate prompt to be less permissive.

## Bugs caught in flight (note for triage)

- **claims_db race condition** — concurrent `extract_and_store_claims` calls do read-then-write outside the save lock. Observed during re-run: cycle counter went `total: 619` then `total: 615`, dropping ~4 claims from the wiki write because the reddit write loaded stale state before saving. Fix: move `db = _load(topic)` inside the `with _lock:` block.
- **`core` agent stale kwarg** — log shows repeated `[!] core error: BaseSourceAgent.validate_and_save() got an unexpected keyword argument 'min_hits'`. Core agent is calling the base method with a kwarg the base method dropped. Quiet bug — drops candidates without crash. Fix: remove the `min_hits=` kwarg at the core call site, or accept+ignore in the base.

## Track B — Architectural debt

Identified in code review. Order changed from original proposal: recovery pass first (immediate pain), schemas second (foundation), loop unification third (bug factory).

### Step 4: Recovery pass (highest practical value, ~2-3 hours)

Build `python workflow.py repair TOPIC` that:
- Scans `vault/TOPIC/*.txt` 
- For any file missing a classification entry → run `classifier.classify_file`
- For any file missing claims → run `claims_db.extract_and_store_claims`
- Optionally recompute topic-density metrics and update drift_log

Solves: Ollama outages currently leave silent partial state. Some files classified, others not. Some claims extracted, others not. No way to know which is which without manual inspection. Repair command makes outages survivable.

### Step 1: Centralize shared schemas (~half day)

Create `core/schemas.py` with Pydantic models or TypedDicts for:
- `KnowledgeMap` (nodes, frontier_scores, lexicon, junk_sources, high_value_sources)
- `Claim` (text, source_file, source_name, type, numeric, keywords, low_trust)
- `Classification` (chapter_relevance, skill_tier, content_type)
- `DriftLogEntry` (ts, file, classification, topic_density, claim_count)
- `QualityReport` (current ad-hoc dataclass formalized)
- `FileOriginsEntry` (filename → source_name)

Provide load/save helpers. Migrate all JSON I/O to use these. Refactoring becomes type-safe.

### Step 2: Unify the harvest loop (~1-2 hours)

`main.main()` and `workflow.cmd_harvest()` have drifted apart. Both orchestrate `Coordinator.run()` cycles but differ in:
- Quality gate integration (only in main)
- Watchdog usage (only in main)  
- Fingerprint reset (only in main)
- PDF export (only in workflow)

Extract one `run_harvest(topic, ...)` function that does everything. Both entry points call it.

### Step 5: Filter-effect tracker in watchdog (~1 hour, after watchdog full wire-in)

Track per-filter strip/replace counts per cycle. Surface in scorecard:

| Filter | C1 strips | C2 strips | Trend | Effect on facts score |

Catches "filter X is stripping more this cycle, and score is dropping" — exactly the diagnostic that would have caught v10's regression earlier.

### Step 6: Minimal regression test harness (~half day)

Create `tests/` directory with a `toy_topic` fixture:

- 5 curated vault `.txt` files (small, well-formed, on-topic)
- Frozen `claims_toy_topic.json` (~40 claims spread across slots)
- Frozen `file_classifications.json` (matching the 5 vault files)

Tests assert:
- `builder.build_curriculum()` returns a textbook path; gap_nodes count ≤ threshold
- `quality_gate.evaluate()` returns `is_usable == True` (or score ≥ floor we set, e.g. 0.65)
- Watchdog `scorecard.json` is well-formed; contains expected top-level keys (`run_id`, `cycle_quality`, `alerts`, `metrics`)
- Hallucination filter on a known-bad sentence strips it; on a known-good sentence keeps it
- Terminology canonicalization with synthetic 3+1+1 variants picks the dominant
- Drift gate rejects "Administrative districts" and "Gameplay" for a craft topic

Run via `pytest tests/`. Once stable, this catches regressions like v10's automatically before we ship.

## Track C — Reliability & UX

External roadmap pass. Tightens visibility and narrative quality; low risk to text. Each step composes existing data — no new pipeline machinery, just better surfacing. Slot in after Track A re-run validates v9-equivalent quality. Step 7 first (cheap visibility), then the rest interleaved with Track B as appetite allows.

### Step 7: `workflow.py quality TOPIC` (~1 hour)

Compose existing data into one human-readable snapshot:

- Quality gate scores per cycle (from `runs/<topic>/*/scorecard.json`)
- Claims DB stats — total, by source, low-trust ratio (from `claims_db.db_stats()`)
- Drift summary (from `drift_monitor.drift_summary()`)
- Source breakdown (claim `source_name` counts)

Example output:

```
=== Quality overview: leatherworking ===

Cycles:
  1: score=0.71 facts=82% pedagogy=76%
  2: score=0.84 facts=91% pedagogy=83%  (USABLE)

Claims:
  total: 274
  sources: books=142, standards=26, forums=58, web=48
  low-trust: 66 (24%)

Drift (last 20 saves):
  reference_ratio: 0.10
  narrative_ratio: 0.05
  avg_topic_density: 0.81
```

Pure visibility, zero risk to text quality. Highest ROI of this track — wire it in before the re-run so we can inspect that result with it.

### Step 8: Experimental appendix in compiled textbook (~2 hours)

During compile, collect sentences/sections that:

- Are sourced entirely from `low_trust=True` claims, OR
- Don't map to any claim but survived other filters (i.e. weren't strong enough to strip but weren't grounded either)

Move them into a clearly-labeled appendix at the end of the textbook:

```
## Appendix: Experimental Techniques and Practitioner Tips

> These notes are compiled from forums and anecdotal sources.
> They have not been cross-checked against reference texts.

- ...
```

Main narrative stays "reference-grade"; borderline content has a home that doesn't pollute it.

### Step 9: Chapter reliability header block (~1 hour)

Generate a meta-block at the top of each chapter from data already collected during compile:

```
> Reliability summary
> - Source files used: 27 (books: 15, standards: 4, forums: 8)
> - High-trust claims: 83; low-trust: 9 (clearly labeled when used)
> - Filter activity: 0 contradictions, 2 speculative sentences removed
```

Sources: `file_classifications.json` + `source_name`, `claims_db.get_claims_for_chapter()` results, `hallucination_filter` stats. Just needs surfacing.

### Step 10: `workflow.py verify TOPIC --sample N` (~2 hours)

Random-sample N claims from `claims_<topic>.json`, write a manual review markdown:

- `claim_text`, `source_file`, numeric values
- Checkboxes: `[ ] Correct  [ ] Incorrect  [ ] Unclear`, notes field

Output: `verify_<topic>.md`. Later: parse the annotated file and flag incorrect claims so the compiler skips them.

Gives ground-truth data to calibrate the LLM extractors and to sanity-check quality scores.

### Step 11: Low-trust prompt hedging (~30 min)

Add to `CLAIM_DRIVEN_CHAPTER_PROMPT`:

> Low-trust claims (forum, web, hub) must not be the sole basis for a strong factual statement. If a low-trust claim is the only support, hedge ("Some practitioners report…", "Anecdotally…") and keep it clearly provisional.

Composes with the existing `low_trust` marker. Tiny prompt change, no code shift, prevents reddit/duckduckgo color from dominating hard facts.

### Step 12: USAGE.md + friendly `workflow.py` output (~1 hour)

Document the canonical lifecycle so non-developers can run the pipeline:

```
python workflow.py status
python workflow.py harvest TOPIC --min-files 120 --iterations 6 --cycles 2
python workflow.py status            # check for 'complete' / 'needs PDF export'
# open vault/TOPIC/curriculum/TOPIC_textbook.pdf
python workflow.py verify TOPIC --sample 40   # optional spot-check
python workflow.py quality TOPIC              # quality overview
```

And make every `workflow.py` command end with clear output lines:

- `Open: vault\leatherworking\curriculum\leatherworking_textbook.pdf`
- `Suggested: python workflow.py verify leatherworking --sample 40`

Pushes users toward `workflow.py` as the front door; `main.py` becomes an implementation detail.

---

**Dropped from external proposal:** inline claim markers (`[C12]` tags in prose, mapped post-hoc to a per-chapter bibliography). Conflicts with the anchor-for-prose decision we just landed — readers should see textbook prose, not a wiki citation dump. The verification benefit is served by Step 10 without polluting reader-facing text.

## Deferred (low ROI right now)

### Step 3: Per-topic namespacing of global stores

`file_classifications.json`, `cited_urls.json`, `agent_stats.json` are all global but should be per-topic. Only matters if running multiple topics concurrently, which we aren't. Defer until we do.

### Drift gating precision improvements

v10's problem wasn't drift, it was over-correction. Drift gating works well where it is.

### Automated regression tests

`pytest` suite that runs a small prepared vault through builder+compiler+quality_gate and asserts score thresholds. Worth doing once architecture is stable enough that tests have predictable inputs.

### Theory↔practice claim dimension

User insight from prior session: claims should carry orthogonal `theoretical_support` and `practical_support` scores, not a single `low_trust` flag. Conflicts between theory and practice should be flagged as moderating-variable opportunities (the disagreement is the teaching content). Architecturally large; defer until current quality issues are stable.

### Conflict-resolution / conditional-claim layer

User insight: when claims about the same subject disagree on numerics, run an LLM pass to diagnose the moderating variable. Tier becomes "how many conditions modify this claim". Composes with theory↔practice axis. Defer for the same reason.

### Image pipeline

Wikimedia Commons image fetcher exists but produces 0.3% hit rate. Need either:
- Wikipedia article-embedded images (different from Commons keyword search)
- Pixabay/Unsplash for hero photography
- Smithsonian/Met for historical craft objects

Deferred until text pipeline is solid.

### Forum agents beyond Reddit

Reddit agent is in. Could add Discourse forums, phpBB, niche craft communities. Marginal value until current corpus issues are sorted.

## Background context

- v9 cycle 2 is the current best output: 0.75 score, 74% facts, 61% pedagogy
- Pipeline is at v10 (regressed) — Track A reverts to v9-equivalent before adding more
- Watchdog is partially wired (main.py, base_source_agent.py); other phases not yet emitting
- Claims DB persists across runs and compounds (~500 claims for leatherworking at v10 end)
