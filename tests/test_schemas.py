"""core.schemas — type contracts + path resolution + load/save round-trip."""

import json
import os

from core.schemas import (
    ClaimDict,
    ClassificationDict,
    DriftLogEntry,
    claims_db_path,
    classifications_path,
    drift_log_path,
    load_claims_db,
    save_claims_db,
    load_classifications,
    save_classifications,
)


def test_claims_db_round_trip(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))

    db = {"claims": [
        {
            "text": "Vegetable-tanned leather develops a natural patina over time.",
            "source_file": "VOL_test_001.txt",
            "source_name": "wikipedia",
            "type": "material",
            "numeric": [],
            "keywords": ["vegetable-tanned", "patina"],
            "low_trust": False,
        }
    ]}
    save_claims_db("toy", db)
    loaded = load_claims_db("toy")
    assert loaded["claims"][0]["text"].startswith("Vegetable")
    assert loaded["claims"][0]["low_trust"] is False


def test_paths_use_base_dir(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    assert claims_db_path("leatherworking").endswith("claims_leatherworking.json")
    assert classifications_path().endswith("file_classifications.json")
    assert drift_log_path("toy").endswith("drift_log_toy.json")


def test_classifications_empty_default(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    # Reading a non-existent file returns the typed default
    assert load_classifications() == {}
