# src/filters.py
import re
import logging
from typing import Optional

from config import FILTERED_WORDS

logger = logging.getLogger(__name__)

def contains_filtered_word(text: str) -> bool:
    """
    Returns True if any substring in FILTERED_WORDS is found in text.lower().
    """
    lower = text.lower()
    for w in FILTERED_WORDS:
        if w in lower:
            logger.info(f"Dropping transcript because it contains filtered word: {w!r}")
            return True
    return False

def is_purely_numeric(text: str) -> bool:
    """
    True if text consists solely of digits and whitespace (no letters).
    """
    stripped = text.strip()
    if not stripped:
        return True
    return all(char.isdigit() or char.isspace() for char in stripped)

def is_gibberish(text: str, min_words: int = 2) -> bool:
    """
    A heuristic: if the transcript has fewer than `min_words` words, or if
    > 75% of its tokens are single‐character or improbable, consider it gibberish.
    """
    words = text.strip().split()
    if len(words) < min_words:
        logger.info("Dropping transcript: fewer than minimum words.")
        return True

    # Count how many “valid” words we have (e.g., length ≥ 2 and alphabetic)
    valid = sum(1 for w in words if re.fullmatch(r"[A-Za-z0-9]{2,}", w))
    ratio = valid / len(words)
    if ratio < 0.25:
        logger.info("Dropping transcript: too many invalid tokens (gibberish).")
        return True

    return False

def filter_transcript(text: str) -> Optional[str]:
    """
    Returns text if it passes all filters; otherwise returns None.
    """
    if contains_filtered_word(text):
        return None
    #if is_purely_numeric(text):
    #    return None
    #if is_gibberish(text):
    #    return None
    return text.strip()
