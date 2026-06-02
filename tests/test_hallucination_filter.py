"""Hallucination filter — known-bad specifics get stripped; known-good
sentences (matching the claim DB) are preserved. Pedagogy sections are
exempted entirely (the v10 regression repair)."""

from curriculum.hallucination_filter import filter_chapter


CLAIMS = [
    {
        "text": "Vegetable-tanned leather should be at least 8 oz thickness for stability.",
        "numeric": ["8 oz"],
        "keywords": ["vegetable-tanned", "8 oz"],
    },
    {
        "text": "Saddle stitches use two needles passing through the same hole from opposite sides.",
        "numeric": [],
        "keywords": ["saddle stitch", "two needles"],
    },
]


def test_strips_unsupported_temperature():
    """Specific temperature value not in claims → containing sentence stripped."""
    content = (
        "## Materials\n\n"
        "Cure leather at 105°F for stability across the workshop. "
        "Saddle stitches use two needles passing through the same hole from opposite sides.\n"
    )
    filtered, stats = filter_chapter(content, CLAIMS)
    assert "105°F" not in filtered
    assert "Saddle stitches" in filtered
    assert stats["sentences_stripped"] >= 1


def test_keeps_supported_specific():
    """8 oz appears in claims → sentence with 8 oz survives."""
    content = (
        "## Materials\n\n"
        "Stropping leather should be at least 8 oz thickness for stability while honing.\n"
    )
    filtered, stats = filter_chapter(content, CLAIMS)
    assert "8 oz" in filtered
    assert stats["sentences_stripped"] == 0


def test_pedagogy_block_exempted():
    """Sentences inside Try This / Review Questions blocks are NOT filtered,
    even when they contain unsupported specifics. This is the v10 fix."""
    content = (
        "## Try This\n\n"
        "Take a 6 oz piece of veg-tan and cut a 2-inch strip at 45° to the grain. "
        "Check that it bends evenly without cracking.\n\n"
        "## Review Questions\n\n"
        "1. What temperature should you avoid for storage?\n"
    )
    filtered, _ = filter_chapter(content, CLAIMS)
    # Specific values (6 oz, 2-inch, 45°) are NOT in claims but live in
    # pedagogy blocks, so they must survive.
    assert "6 oz" in filtered
    assert "2-inch" in filtered
    assert "Take a 6 oz piece" in filtered


def test_proper_nouns_warned_not_stripped():
    """Brand-style proper nouns used to be stripped in v10. Track A reverted
    that to warn-only — they should now survive. (Detection skips the
    sentence-start word to avoid false positives, so brands mid-sentence
    are what the filter actually checks.)"""
    content = (
        "## Tools\n\n"
        "Many practitioners reach for Amy Roke clamps when saddle stitching "
        "because they hold leather steadily under bench tension.\n"
    )
    filtered, stats = filter_chapter(content, CLAIMS)
    assert "Amy Roke" in filtered
    # Warning was recorded but sentence kept
    assert stats.get("proper_noun_warnings", 0) >= 1
    assert stats["sentences_stripped"] == 0
