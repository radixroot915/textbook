import re
from config import MIN_LETTER_RATIO as _MIN_LETTER_RATIO, HTML_CHECK_WINDOW as _CHECK_WINDOW

_HTML_TAGS = frozenset({"<!doctype", "<html", "<head", "<body", "<script", "<style", "<meta", "<div", "<span"})


def select_instructional_content(raw_text: str) -> str:
    if not raw_text:
        return ""

    # Reject HTML/redirect pages by scanning for common tags
    sample = raw_text[:_CHECK_WINDOW].lower()
    if any(tag in sample for tag in _HTML_TAGS):
        return ""

    # Reject low-quality OCR or binary garbage by letter ratio
    alpha = sum(1 for c in raw_text if c.isalpha())
    if len(raw_text) > 0 and alpha / len(raw_text) < _MIN_LETTER_RATIO:
        return ""

    content = re.sub(r"Digitized by .*?\n", "", raw_text)
    return content.strip()
