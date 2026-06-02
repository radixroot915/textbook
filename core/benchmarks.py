"""core.benchmarks — the three dashboard metrics surfaced by the run-compare
diagnostic. Each function is pure (takes data, returns a number/dict) so it
can be called from CLI, tests, or future dashboards without side effects.

The benchmarks:

1. `reference_source_share(claims_db)` — fraction of claims from
   high-trust reference sources. Healthy ≥ 0.40, investigate < 0.20.

2. `score_delta_cycle_over_cycle(cycle_quality)` — worst score drop
   between consecutive cycles. Healthy ≥ 0, investigate < -0.05.

3. `filter_ratio_health(filter_effects, cycle_quality)` — per-filter
   strip ratio cycle-over-cycle, paired with score drop. Returns a list of
   (filter_name, stat_key, ratio, quality_dropped) tuples. Anything with
   ratio ≥ 2.0 AND quality_dropped=True is the v10 regression signal.
"""

REFERENCE_SOURCES = frozenset({
    "wikipedia", "archive_org", "openlibrary", "wikibooks",
    "hathitrust", "wikisource", "gutenberg", "libretexts",
    "skillscommons", "core", "dtic", "everyspec", "osha",
    "stackexchange",
})


def reference_source_share(claims_db: dict) -> float:
    """Fraction of claims sourced from reference (non-forum) agents."""
    claims = claims_db.get("claims", [])
    if not claims:
        return 0.0
    ref = sum(1 for c in claims if c.get("source_name") in REFERENCE_SOURCES)
    return ref / len(claims)


def score_delta_cycle_over_cycle(cycle_quality: list) -> float | None:
    """Worst score change between consecutive cycles.
    None if fewer than 2 cycles."""
    if not cycle_quality or len(cycle_quality) < 2:
        return None
    deltas = [
        cycle_quality[i + 1].get("score", 0) - cycle_quality[i].get("score", 0)
        for i in range(len(cycle_quality) - 1)
    ]
    return min(deltas)


def filter_ratio_health(filter_effects: dict, cycle_quality: list) -> list:
    """Per-filter, per-stat cycle-over-cycle ratio. Returns a list of:
       {filter, stat, prev, curr, ratio, quality_dropped}
    Sorted by ratio descending so the worst offenders surface first.
    """
    cycles = sorted((int(k) for k in filter_effects.keys()))
    if len(cycles) < 2:
        return []

    # Build quality lookup by cycle for the drop check
    q_by_cycle = {q.get("cycle"): q.get("score", 0) for q in (cycle_quality or [])}

    results = []
    for i in range(1, len(cycles)):
        prev_c, curr_c = cycles[i - 1], cycles[i]
        prev_data = filter_effects.get(str(prev_c), {})
        curr_data = filter_effects.get(str(curr_c), {})
        # All filter+stat pairs seen in either cycle
        seen: set = set()
        for fe in (prev_data, curr_data):
            for fname, stats in fe.items():
                for stat in stats.keys():
                    seen.add((fname, stat))

        prev_score = q_by_cycle.get(prev_c, 0)
        curr_score = q_by_cycle.get(curr_c, 0)
        quality_dropped = curr_score < prev_score - 0.03

        for fname, stat in seen:
            prev_v = prev_data.get(fname, {}).get(stat, 0)
            curr_v = curr_data.get(fname, {}).get(stat, 0)
            if prev_v == 0 and curr_v == 0:
                continue
            ratio = (curr_v / prev_v) if prev_v > 0 else float("inf")
            results.append({
                "filter": fname,
                "stat": stat,
                "prev": prev_v,
                "curr": curr_v,
                "ratio": ratio,
                "quality_dropped": quality_dropped,
            })

    results.sort(key=lambda r: (-(r["ratio"] if r["ratio"] != float("inf") else 1e9)))
    return results


# ---------------------------------------------------------------------------
# Composite health check — one call returns the full picture for a run

USABLE_SCORE_FLOOR = 0.74
FACTS_FLOOR = 0.55
PEDAGOGY_FLOOR = 0.45


def run_health(claims_db: dict, scorecard: dict) -> dict:
    """Apply all benchmarks to a finished run.
    Returns a dict with each metric + a 'verdict' string."""
    ref_share = reference_source_share(claims_db)
    delta = score_delta_cycle_over_cycle(scorecard.get("cycle_quality", []))
    ratio_alerts = [
        r for r in filter_ratio_health(
            scorecard.get("filter_effects", {}),
            scorecard.get("cycle_quality", []),
        )
        if r["ratio"] >= 2.0 and r["quality_dropped"]
    ]

    # Latest cycle's score / facts / pedagogy — single-cycle runs need
    # an absolute health check, not just cross-cycle deltas.
    cq = scorecard.get("cycle_quality", []) or []
    # Use None when a field is missing so the floor checks below can skip
    # cleanly (avoids spurious "0% < floor" issues on partial scorecards).
    latest_score = cq[-1].get("score") if cq else None
    latest_facts = cq[-1].get("facts") if cq else None
    latest_pedagogy = cq[-1].get("pedagogy") if cq else None

    issues = []
    if ref_share < 0.20:
        issues.append(f"reference_source_share {ref_share:.0%} < 20% (sources too forum-heavy)")
    if delta is not None and delta < -0.05:
        issues.append(f"score dropped {delta:+.2f} between cycles")
    if ratio_alerts:
        for r in ratio_alerts[:3]:
            issues.append(
                f"{r['filter']}.{r['stat']} jumped {r['prev']} → {r['curr']} "
                f"({r['ratio']:.1f}×) with quality drop"
            )
    if latest_score is not None and latest_score < USABLE_SCORE_FLOOR:
        issues.append(f"latest score {latest_score:.2f} < {USABLE_SCORE_FLOOR} floor")
    if latest_facts is not None and latest_facts < FACTS_FLOOR:
        issues.append(f"facts {latest_facts:.0%} < {FACTS_FLOOR:.0%} floor")
    if latest_pedagogy is not None and latest_pedagogy < PEDAGOGY_FLOOR:
        issues.append(f"pedagogy {latest_pedagogy:.0%} < {PEDAGOGY_FLOOR:.0%} floor")

    return {
        "reference_source_share": ref_share,
        "worst_cycle_delta": delta,
        "filter_ratio_alerts": ratio_alerts,
        "latest_score": latest_score,
        "latest_facts": latest_facts,
        "latest_pedagogy": latest_pedagogy,
        "issues": issues,
        "verdict": "healthy" if not issues else "investigate",
    }
