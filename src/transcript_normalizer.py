"""
Transcript normalization for Lakewood, Ohio dispatch audio.

Runs on raw STT output before any extraction or classification.
Fixes systematic transcription errors without touching content the
model got right.

Rules are ordered from most to least specific. Each rule is a
(pattern, replacement, description) tuple for auditability.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Address number reassembly
#
# Dispatchers say Lakewood addresses as two spoken chunks:
#   "fourteen three-hundred" → STT: "1 4300" or "1-4300"
#   "eighteen nine-hundred"  → STT: "1-8900" or "1 8900"
#   "fourteen six-fifty"     → STT: "1-4650" (usually fine) or "1 4650"
#
# The pattern is always: a lone "1" (with optional hyphen/space) followed
# immediately by a 4-digit number. Rejoin them into a single 5-digit token.
# ---------------------------------------------------------------------------

_SPLIT_ADDRESS_RE = re.compile(
    r"\b1[-\s](\d{4})\b"
)

def _fix_split_addresses(text: str) -> str:
    """'1 4300' → '14300',  '1-8900' → '18900'"""
    return _SPLIT_ADDRESS_RE.sub(lambda m: f"1{m.group(1)}", text)


# ---------------------------------------------------------------------------
# Unit name normalisation
#
# Spoken: "medic one", "truck two", "engine three"
# STT produces inconsistent capitalisation and word-number combos.
# Normalise to canonical "Medic 1" form so downstream regex always matches.
# ---------------------------------------------------------------------------

_WORD_TO_NUM = {"one": "1", "two": "2", "three": "3", "four": "4"}

_UNIT_SPOKEN_RE = re.compile(
    r"\b(engine|medic|truck|car|rescue|battalion)\s+(one|two|three|four)\b",
    re.IGNORECASE,
)

def _fix_unit_names(text: str) -> str:
    def _replace(m: re.Match) -> str:
        apparatus = m.group(1).capitalize()
        num = _WORD_TO_NUM[m.group(2).lower()]
        return f"{apparatus} {num}"
    return _UNIT_SPOKEN_RE.sub(_replace, text)


# ---------------------------------------------------------------------------
# Facility name normalisation
#
# Common mis-transcriptions of known Lakewood facilities.
# Tuples of (regex pattern, canonical replacement).
# ---------------------------------------------------------------------------

_FACILITY_FIXES: list[tuple[re.Pattern, str]] = [
    # "Lakewood Cliffs" / "Lakewood List" / "Lakewood Cliff" → "Lakewood Cliffs"
    (re.compile(r"\bLakewood\s+(?:Cliff|List|Cliffs?)\b", re.IGNORECASE), "Lakewood Cliffs"),
    # "the Westerly" / "the lastly" / "the last delay" → "the Westerly"
    (re.compile(r"\b(?:the\s+)?(?:lastly|last\s+delay|lasely|washley|lassley)\b", re.IGNORECASE), "the Westerly"),
    # "Lakewood Center North" / "Lakewood Center West" — keep as-is, just normalise case
    (re.compile(r"\bLakewood\s+Center\s+North\b", re.IGNORECASE), "Lakewood Center North"),
    (re.compile(r"\bLakewood\s+Center\s+West\b", re.IGNORECASE), "Lakewood Center West"),
    # "Rosie's Winehouse" variants
    (re.compile(r"\bRosie'?s?\s+Wine\s*house\b", re.IGNORECASE), "Rosie's Winehouse"),
    # "O'Neill" written without apostrophe
    (re.compile(r"\bO\s*Neill\b", re.IGNORECASE), "O'Neill"),
]

def _fix_facilities(text: str) -> str:
    for pattern, replacement in _FACILITY_FIXES:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# 10-code normalisation
#
# STT sometimes writes "10 4", "10-4", "104" inconsistently.
# Normalise to "10-N" form.
# ---------------------------------------------------------------------------

_TEN_CODE_RE = re.compile(
    r"\b10\s*[-–]?\s*(\d{1,2})\b"
)

def _fix_ten_codes(text: str) -> str:
    return _TEN_CODE_RE.sub(lambda m: f"10-{m.group(1)}", text)


# ---------------------------------------------------------------------------
# Whitespace cleanup
# ---------------------------------------------------------------------------

def _clean_whitespace(text: str) -> str:
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """
    Apply all normalization rules to a raw STT transcript.
    Returns the cleaned string, or the original if input is empty.
    """
    if not text or not text.strip():
        return text

    original = text
    text = _fix_split_addresses(text)
    text = _fix_unit_names(text)
    text = _fix_facilities(text)
    text = _fix_ten_codes(text)
    text = _clean_whitespace(text)

    if text != original:
        logger.debug("Normalized transcript:\n  before: %r\n  after:  %r", original, text)

    return text
