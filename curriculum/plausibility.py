"""plausibility — magnitude-sanity check for claims with units.

Catches catastrophic unit errors that lexical fact-check can't see:
  - "Set the laser to 50–100 mW" when real lasers are 50–100 W (1000× off)
  - "Heat the die to 150–180°C (300–350°F)" — internal C↔F mismatch
  - "Soak for 500 hours" — too long
  - "Apply 0.001 mm of finish" — too thin

Returns a list of plausibility warnings (sentence + reason). The
fact-checker downgrades claims with plausibility warnings from verified
to flagged so the reground pass picks them up.
"""

import re
from dataclasses import dataclass


@dataclass
class PlausibilityWarning:
    sentence: str
    reason: str


# Typical-range table: (unit_regex, min, max, label)
# Anything outside these ranges is suspicious for the unit family.
_RANGES = [
    # Power: legitimate writing in mW stays sub-watt; values >500 mW
    # almost always indicate a unit error (should be W).
    (r'\bmW\b',   0.1,  500,     "mW"),
    (r'\bwatts?\b|\bW\b',   0.001, 50000,   "W"),
    # Voltage
    (r'\bvolts?\b|\bV\b',   0.5,  100000,  "V"),
    # Current
    (r'\bamps?\b|\bA\b',    0.001, 5000,   "A"),
    # Temperature
    (r'°C\b',    -273, 5000,   "°C"),
    (r'°F\b',    -459, 9000,   "°F"),
    # Length
    (r'\bmm\b',   0.001, 100000, "mm"),
    (r'\binch(?:es)?\b|\bin\b', 0.001, 10000, "in"),
    # Pressure
    (r'\bpsi\b',  0.01, 100000, "psi"),
    # Time
    (r'\bseconds?\b|\bsec\b',   0.001, 86400,  "sec"),
    (r'\bminutes?\b|\bmin\b',   0.001, 1440,   "min"),
    (r'\bhours?\b|\bhr\b',      0.001, 720,    "hr"),
]


_NUM_UNIT = re.compile(
    r'(\d+(?:[\.,]\d+)?)\s*([a-zA-Z°µ]+)\b'
)
_TEMP_PAIR = re.compile(
    r'(\d+(?:[\.,]\d+)?)\s*[-–]\s*(\d+(?:[\.,]\d+)?)\s*°C\b\s*\(\s*(\d+(?:[\.,]\d+)?)\s*[-–]\s*(\d+(?:[\.,]\d+)?)\s*°F\b'
)
_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')


# Unit-pair conversion checks — when two units of the same dimension are
# stated together (e.g. "30°C / 86°F" or "2 oz (0.8 mm)"), verify the
# conversion is correct within ±15% tolerance. Catches the "2-3 oz / 0.8-1.0 mm"
# error the auditor caught (3 oz is closer to 1.2mm, not 1.0mm).
#
# Each tuple: (regex, dim_a_to_b function, tolerance_pct, label)
def _c_to_f(c): return c * 9 / 5 + 32
def _f_to_c(f): return (f - 32) * 5 / 9
def _mm_to_in(mm): return mm / 25.4
def _in_to_mm(i): return i * 25.4
def _kg_to_lb(kg): return kg * 2.20462
def _lb_to_kg(lb): return lb / 2.20462
def _g_to_oz(g): return g / 28.35
def _oz_to_g(oz): return oz * 28.35
def _leather_oz_to_mm(oz): return oz * 0.4   # 1 oz ≈ 0.4mm leather weight
def _leather_mm_to_oz(mm): return mm / 0.4


def _avg(low: float, high: float | None) -> float:
    return (low + high) / 2 if high is not None else low


def _parse_range(s: str) -> tuple[float, float | None]:
    s = s.strip()
    m = re.match(r'(\d+(?:[\.,]\d+)?)\s*[-–]\s*(\d+(?:[\.,]\d+)?)', s)
    if m:
        return float(m.group(1).replace(',', '.')), float(m.group(2).replace(',', '.'))
    m = re.match(r'(\d+(?:[\.,]\d+)?)', s)
    if m:
        return float(m.group(1).replace(',', '.')), None
    return 0.0, None


_UNIT_PAIRS = [
    # "X°C (Y°F)" or "X°C / Y°F" or "X°C, Y°F"
    (
        re.compile(r'(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*°C\s*[/(,]\s*(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*°F'),
        _c_to_f, "°C↔°F",
    ),
    (
        re.compile(r'(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*°F\s*[/(,]\s*(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*°C'),
        _f_to_c, "°F↔°C",
    ),
    # "X mm (Y in)" or "X in (Y mm)"
    (
        re.compile(r'(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*mm\s*[/(,]\s*(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*(?:inch(?:es)?|in)\b'),
        _mm_to_in, "mm↔in",
    ),
    (
        re.compile(r'(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*(?:inch(?:es)?|in)\s*[/(,]\s*(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*mm\b'),
        _in_to_mm, "in↔mm",
    ),
    # Weight: kg ↔ lb
    (
        re.compile(r'(\d+(?:[\.,]\d+)?)\s*kg\s*[/(,]\s*(\d+(?:[\.,]\d+)?)\s*lb'),
        _kg_to_lb, "kg↔lb",
    ),
    # Leather weight: oz ↔ mm (1 oz ≈ 0.4mm)
    (
        re.compile(r'(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*oz\b\s*[/(,]\s*(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*mm'),
        _leather_oz_to_mm, "oz↔mm (leather)",
    ),
    (
        re.compile(r'(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*mm\s*[/(,]\s*(\d+(?:[\.,]\d+)?(?:\s*[-–]\s*\d+(?:[\.,]\d+)?)?)\s*oz\b'),
        _leather_mm_to_oz, "mm↔oz (leather)",
    ),
]


def check_unit_conversions(text: str) -> list[PlausibilityWarning]:
    """Find side-by-side unit pairs and verify the conversion."""
    warnings: list[PlausibilityWarning] = []
    for pat, convert_fn, label in _UNIT_PAIRS:
        for m in pat.finditer(text):
            a_str, b_str = m.group(1), m.group(2)
            a_lo, a_hi = _parse_range(a_str)
            b_lo, b_hi = _parse_range(b_str)
            if a_lo == 0:
                continue
            # Compare averages — covers single values and ranges
            expected = convert_fn(_avg(a_lo, a_hi))
            actual = _avg(b_lo, b_hi)
            if expected == 0:
                continue
            err_pct = abs(expected - actual) / abs(expected)
            if err_pct > 0.15:  # ±15% tolerance
                # Extract sentence containing the match
                sent_start = max(0, text.rfind('.', 0, m.start()) + 1)
                sent_end = text.find('.', m.end())
                if sent_end == -1:
                    sent_end = min(len(text), m.end() + 100)
                sent = text[sent_start:sent_end].strip()
                warnings.append(PlausibilityWarning(
                    sentence=sent[:200],
                    reason=f"{label}: {a_str} should convert to ~{expected:.1f}, "
                           f"but stated as {b_str} ({err_pct*100:.0f}% off)",
                ))
    return warnings


_LASER_CTX = re.compile(r'\b(laser|cutter|engraver|welder|cnc)\b', re.IGNORECASE)


def check_text(text: str) -> list[PlausibilityWarning]:
    """Return a list of plausibility warnings. Empty list = looks fine."""
    warnings: list[PlausibilityWarning] = []
    sentences = _SENT_SPLIT.split(text)

    for sent in sentences:
        # Context-aware: laser/cutter/welder power given in mW is almost
        # always a unit error (real industrial tools are in watts)
        if _LASER_CTX.search(sent):
            for m in re.finditer(r'(\d+(?:[\.,]\d+)?)\s*mW\b', sent):
                try:
                    val = float(m.group(1).replace(",", "."))
                except ValueError:
                    continue
                warnings.append(PlausibilityWarning(
                    sentence=sent.strip()[:200],
                    reason=f"{val}mW for laser/cutter — likely unit error (should be W)",
                ))

        # Magnitude check on each (number, unit) pair
        for m in _NUM_UNIT.finditer(sent):
            try:
                val = float(m.group(1).replace(",", "."))
            except ValueError:
                continue
            unit_token = m.group(0)
            for pat, lo, hi, label in _RANGES:
                if not re.search(pat, unit_token, re.IGNORECASE):
                    continue
                if val < lo or val > hi:
                    warnings.append(PlausibilityWarning(
                        sentence=sent.strip()[:200],
                        reason=f"{val}{label} outside typical range [{lo}, {hi}]",
                    ))
                    break  # one warning per number

        # Internal C↔F consistency check
        for m in _TEMP_PAIR.finditer(sent):
            c_lo, c_hi, f_lo, f_hi = (float(g.replace(",", ".")) for g in m.groups())
            expected_f_lo = c_lo * 9 / 5 + 32
            expected_f_hi = c_hi * 9 / 5 + 32
            # Allow 15°F tolerance on rounded numbers
            if abs(f_lo - expected_f_lo) > 15 or abs(f_hi - expected_f_hi) > 15:
                warnings.append(PlausibilityWarning(
                    sentence=sent.strip()[:200],
                    reason=f"C↔F mismatch: {c_lo}-{c_hi}°C should be ~{expected_f_lo:.0f}-{expected_f_hi:.0f}°F, got {f_lo}-{f_hi}°F",
                ))

    return warnings
