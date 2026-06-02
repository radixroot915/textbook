"""Claim-ID attribution — marker parsing, stripping, and the new
`check_by_markers` verification path that replaces fuzzy matching with
direct lookup against the claim list passed to the writer."""

import pytest
from curriculum.fact_checker import (
    parse_claim_markers,
    strip_claim_markers,
    FactChecker,
)


# ---------------------------------------------------------------------------
# parse_claim_markers

def test_parse_single_marker():
    assert parse_claim_markers("Saddle stitch uses two needles [C7].") == [7]


def test_parse_multi_marker_comma():
    """Two-claim attribution `[C1, C2]` should produce both IDs."""
    assert parse_claim_markers("Cure leather [C1, C2] before stitching.") == [1, 2]


def test_parse_multi_marker_space():
    """`[C1 C2]` (space-separated) is also valid attribution syntax."""
    assert parse_claim_markers("Cure leather [C1 C2] before stitching.") == [1, 2]


def test_parse_multiple_markers():
    """Two separate markers in the same sentence: `[C7] ... [C11]`."""
    text = "Edge bevelers come in sizes [C1] [C11] [C40] to handle various thicknesses."
    assert parse_claim_markers(text) == [1, 11, 40]


def test_parse_no_markers():
    assert parse_claim_markers("No specifics here, just prose.") == []


def test_parse_ignores_non_claim_brackets():
    """`[Note]` or `[1]` (no C prefix) should not match."""
    text = "See [Note] or [1] but [C5] does match."
    assert parse_claim_markers(text) == [5]


# ---------------------------------------------------------------------------
# strip_claim_markers

def test_strip_single_marker():
    src = "Saddle stitch uses two needles [C7]."
    assert strip_claim_markers(src) == "Saddle stitch uses two needles."


def test_strip_multi_form():
    src = "Cure leather [C1, C2] then condition [C3] before use."
    assert strip_claim_markers(src) == "Cure leather then condition before use."


def test_strip_handles_punctuation_gaps():
    """A marker right before a comma shouldn't leave a stray space."""
    src = "First [C1], then second [C2]."
    out = strip_claim_markers(src)
    assert "  " not in out
    assert " ," not in out
    assert out == "First, then second."


def test_strip_idempotent():
    """Stripping already-clean text doesn't change anything."""
    src = "Clean prose without markers."
    assert strip_claim_markers(src) == src


# ---------------------------------------------------------------------------
# check_by_markers — the new verification path

@pytest.fixture
def fc():
    return FactChecker("toy_topic")


def _claim(text, low_trust=False, source_file="VOL_test.txt"):
    return {
        "text": text,
        "low_trust": low_trust,
        "source_file": source_file,
        "type": "general",
        "keywords": [],
        "numeric": [],
    }


def test_marker_verification_all_valid(fc):
    """Chapter cites only valid claim IDs from a high-trust source list."""
    claims = [
        _claim("Vegetable-tanned leather develops patina."),
        _claim("Stropping leather should be 8 oz minimum."),
    ]
    chapter = (
        "Vegetable-tanned leather develops a rich patina over time [C1]. "
        "For stropping, leather should be at least 8 oz [C2]."
    )
    result = fc.check_by_markers("Materials", chapter, claims, source_files=["x"])
    assert result is not None
    assert result.verified_count == 2
    assert result.flagged_count == 0
    assert result.confidence_score == 1.0


def test_marker_verification_hallucinated_id(fc):
    """`[C99]` when only 2 claims exist → flagged."""
    claims = [_claim("Claim one"), _claim("Claim two")]
    chapter = "Some real attribution [C1] and also some made-up one [C99]."
    result = fc.check_by_markers("Materials", chapter, claims, source_files=["x"])
    assert result is not None
    assert result.verified_count == 1
    assert result.flagged_count >= 1
    assert any("hallucinated" in c.text.lower() for c in result.flagged_claims)


def test_marker_verification_low_trust_demotes_to_tentative(fc):
    """A marker pointing at a low-trust claim → tentative, not verified."""
    claims = [_claim("Reddit advice on stitching", low_trust=True)]
    chapter = "Practitioners hand-stitch by feel [C1]."
    result = fc.check_by_markers("Stitching", chapter, claims, source_files=["x"])
    assert result is not None
    assert result.tentative_count == 1
    assert result.verified_count == 0


def test_no_markers_returns_none(fc):
    """If the chapter has no [CN] markers, caller should fall back to
    the legacy path. check_by_markers signals this with None."""
    claims = [_claim("Some claim")]
    chapter = "Prose with no markers anywhere."
    assert fc.check_by_markers("X", chapter, claims, source_files=["x"]) is None


def test_unattributed_specific_flagged(fc):
    """Specific value (8 oz) with no [CN] tag → flagged as unattributed."""
    claims = [_claim("Some other claim")]
    chapter = (
        "Some other claim is true [C1]. "
        "However, leather should be 8 oz minimum thickness for everything."
    )
    result = fc.check_by_markers("X", chapter, claims, source_files=["x"])
    assert result is not None
    assert any(c.claim_type == "unattributed" for c in result.flagged_claims)
