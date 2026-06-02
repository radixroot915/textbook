"""Watchdog scorecard — well-formed JSON with expected top-level keys,
filter_effects tracked per cycle, and the regression-detector rules fire
on a v10-like scenario (filter strip count jumps + quality drops)."""

import json
import os
import tempfile

from watchdog import Watchdog


def test_scorecard_well_formed(tmp_path, monkeypatch):
    # Redirect runs/ to a temp dir
    import config
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))

    wd = Watchdog()
    wd.start_run("toy_topic")
    wd.set_cycle(1)
    wd.emit("harvest", "save", source="wikipedia", size=12000)
    wd.emit("harvest", "gate_reject", gate="drift", source="reddit")
    wd.emit("quality", "gate",
            score=0.75, facts=0.74, pedagogy=0.61,
            duplicates=0, contradictions=0, usable=True)
    wd.end_run()

    sc_path = tmp_path / "runs" / wd.run_id / "scorecard.json"
    assert sc_path.exists()
    sc = json.loads(sc_path.read_text(encoding="utf-8"))

    # Required top-level keys
    for key in ("run_id", "topic", "duration_sec", "alerts", "cycle_quality",
                "metrics", "agent_productivity", "filter_effects", "event_count"):
        assert key in sc, f"missing key: {key}"

    assert sc["topic"] == "toy_topic"
    assert sc["cycle_quality"][0]["score"] == 0.75


def test_filter_over_stripping_alert(tmp_path, monkeypatch):
    """v10-like scenario: hallucination strip count jumps 5→45 and quality
    drops 0.75→0.62. Both quality_regression and filter_over_stripping
    alerts should fire."""
    import config
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))

    wd = Watchdog()
    wd.start_run("toy_topic")

    wd.set_cycle(1)
    wd.emit("compile", "filter_activity",
            name="hallucination", stripped=5, unsupported=12)
    wd.emit("quality", "gate",
            score=0.75, facts=0.74, pedagogy=0.61,
            duplicates=0, contradictions=0, usable=True)

    wd.set_cycle(2)
    wd.emit("compile", "filter_activity",
            name="hallucination", stripped=45, unsupported=80)
    wd.emit("quality", "gate",
            score=0.62, facts=0.59, pedagogy=0.42,
            duplicates=0, contradictions=0, usable=False)
    wd.end_run()

    alert_kinds = [a["kind"] for a in wd.alerts]
    assert "quality_regression" in alert_kinds
    assert "filter_over_stripping" in alert_kinds
