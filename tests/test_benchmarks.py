"""core.benchmarks — three dashboard metrics surface healthy/unhealthy
runs without grepping log files."""

from core.benchmarks import (
    reference_source_share,
    score_delta_cycle_over_cycle,
    filter_ratio_health,
    run_health,
)


def test_reference_source_share_all_reference():
    db = {"claims": [
        {"source_name": "wikipedia"},
        {"source_name": "archive_org"},
        {"source_name": "openlibrary"},
    ]}
    assert reference_source_share(db) == 1.0


def test_reference_source_share_all_forum():
    db = {"claims": [
        {"source_name": "reddit"},
        {"source_name": "duckduckgo"},
    ]}
    assert reference_source_share(db) == 0.0


def test_reference_source_share_empty_db():
    assert reference_source_share({}) == 0.0
    assert reference_source_share({"claims": []}) == 0.0


def test_reference_source_share_current_run():
    """The actual leatherworking DB shape — 86% low-trust as observed.
    Reference share should land near 14% (97 wikipedia + 8 stackexchange + 26 cited
    out of 803 — but 'cited' is not in REFERENCE_SOURCES so ~13%)."""
    db = {"claims": (
        [{"source_name": "reddit"}] * 404 +
        [{"source_name": "duckduckgo"}] * 268 +
        [{"source_name": "wikipedia"}] * 97 +
        [{"source_name": "cited"}] * 26 +
        [{"source_name": "stackexchange"}] * 8
    )}
    share = reference_source_share(db)
    # wikipedia + stackexchange = 105 / 803 ≈ 0.131
    assert 0.12 < share < 0.16, f"got {share}"


def test_score_delta_no_regression():
    import pytest
    cq = [
        {"cycle": 1, "score": 0.71},
        {"cycle": 2, "score": 0.76},
    ]
    assert score_delta_cycle_over_cycle(cq) == pytest.approx(0.05)


def test_score_delta_regression():
    cq = [
        {"cycle": 1, "score": 0.73},
        {"cycle": 2, "score": 0.62},
    ]
    delta = score_delta_cycle_over_cycle(cq)
    assert delta is not None and delta < -0.10


def test_score_delta_insufficient_cycles():
    assert score_delta_cycle_over_cycle([]) is None
    assert score_delta_cycle_over_cycle([{"cycle": 1, "score": 0.7}]) is None


def test_filter_ratio_v10_scenario():
    """The exact v10-style regression: hallucination strips jumped + quality drops.
    Should surface in the ratio table."""
    filter_effects = {
        "1": {"hallucination": {"stripped": 5, "unsupported": 12}},
        "2": {"hallucination": {"stripped": 45, "unsupported": 80}},
    }
    cycle_quality = [
        {"cycle": 1, "score": 0.75},
        {"cycle": 2, "score": 0.62},
    ]
    results = filter_ratio_health(filter_effects, cycle_quality)
    assert len(results) == 2  # stripped + unsupported
    # Worst ratio first
    assert results[0]["ratio"] >= 2.0
    assert results[0]["quality_dropped"] is True


def test_filter_ratio_no_change():
    filter_effects = {
        "1": {"hallucination": {"stripped": 10}},
        "2": {"hallucination": {"stripped": 12}},
    }
    cycle_quality = [
        {"cycle": 1, "score": 0.75},
        {"cycle": 2, "score": 0.76},
    ]
    results = filter_ratio_health(filter_effects, cycle_quality)
    assert results[0]["ratio"] < 2.0
    assert results[0]["quality_dropped"] is False


def test_run_health_v10_red_flags_all():
    """A pathological scorecard hitting all three issues."""
    claims_db = {"claims": [{"source_name": "reddit"}] * 100}  # 0% reference
    scorecard = {
        "cycle_quality": [
            {"cycle": 1, "score": 0.75},
            {"cycle": 2, "score": 0.62},
        ],
        "filter_effects": {
            "1": {"hallucination": {"stripped": 5}},
            "2": {"hallucination": {"stripped": 50}},
        },
    }
    h = run_health(claims_db, scorecard)
    assert h["verdict"] == "investigate"
    assert len(h["issues"]) >= 3
    assert h["reference_source_share"] == 0.0
    assert h["worst_cycle_delta"] is not None and h["worst_cycle_delta"] < -0.05


def test_run_health_below_score_floor():
    """Single-cycle run with score below 0.74 should fail health verdict
    (the smoke-test edge case that motivated the floor check)."""
    claims_db = {"claims": [{"source_name": "wikipedia"}] * 80 +
                            [{"source_name": "reddit"}] * 20}  # 80% ref
    scorecard = {
        "cycle_quality": [
            {"cycle": 1, "score": 0.67, "facts": 0.51, "pedagogy": 0.47},
        ],
    }
    h = run_health(claims_db, scorecard)
    assert h["verdict"] == "investigate"
    assert any("0.67" in i and "floor" in i for i in h["issues"])


def test_run_health_healthy():
    claims_db = {"claims": [{"source_name": "wikipedia"}] * 60 +
                            [{"source_name": "reddit"}] * 40}  # 60% reference
    scorecard = {
        "cycle_quality": [
            {"cycle": 1, "score": 0.70},
            {"cycle": 2, "score": 0.76},
        ],
        "filter_effects": {
            "1": {"hallucination": {"stripped": 10}},
            "2": {"hallucination": {"stripped": 12}},
        },
    }
    h = run_health(claims_db, scorecard)
    assert h["verdict"] == "healthy"
    assert h["issues"] == []
