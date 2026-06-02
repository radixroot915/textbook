"""Drift gate — known off-topic nodes for a craft topic are rejected
before they enter the frontier. This is the gate that catches gap-analysis
pollution like 'Administrative districts' or 'Gameplay'."""

from drift_monitor import is_node_on_topic


def test_rejects_administrative_districts():
    allowed, reason = is_node_on_topic("leatherworking", "Administrative districts", [])
    assert not allowed
    assert "off-topic" in reason


def test_rejects_gameplay():
    allowed, reason = is_node_on_topic("leatherworking", "Gameplay mechanics", [])
    assert not allowed


def test_accepts_topic_root_match():
    """Anything containing the topic root passes immediately."""
    allowed, reason = is_node_on_topic(
        "leatherworking", "Leatherworking tools for beginners", []
    )
    assert allowed
    assert reason == "topic-root-match"


def test_accepts_lexicon_match():
    """A lexicon term present in the node passes."""
    lexicon = ["awl", "burnisher", "edge beveler"]
    allowed, reason = is_node_on_topic("leatherworking", "Sharpening awl tips", lexicon)
    assert allowed
    assert reason == "lexicon-match"


def test_empty_input_rejected():
    allowed, reason = is_node_on_topic("leatherworking", "", [])
    assert not allowed
