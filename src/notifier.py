# src/notifier.py

import requests
import logging
from datetime import datetime, timezone
import re

from config import PUSHOVER_TOKEN, PUSHOVER_USER, USE_PUSHOVER, ALERT_PATTERNS

logger = logging.getLogger(__name__)
_last_notification_time: datetime = None

def matches_alert_pattern(text: str) -> bool:
    """
    Returns True if `text` matches any of the compiled regexes in ALERT_PATTERNS.
    """
    for pat in ALERT_PATTERNS:
        if pat.search(text):
            logger.debug(f"ALERT: transcript matched pattern {pat.pattern!r}")
            return True
    return False

def send_pushover(title: str, message: str, force: bool = False) -> int:
    """
    Sends a Pushover notification only if:
      - force=True, or
      - message/text matches an alert pattern (via `matches_alert_pattern`),
      - and USE_PUSHOVER is True.
    Respects a 10-minute cooldown between notifications (unless force=True).
    Returns the HTTP status code (200 if successful), or 0 if skipped.
    """
    global _last_notification_time

    if not USE_PUSHOVER:
        logger.debug("Pushover is disabled (USE_PUSHOVER=False).")
        return 0

    now = datetime.now(timezone.utc)
    if not force:
        # Only notify if the full `message` text matches at least one pattern
        if not matches_alert_pattern(message):
            logger.debug("Transcript did not match any alert pattern; skipping Pushover.")
            return 0

    # 10‐minute cooldown
    if _last_notification_time is not None and not force:
        diff = (now - _last_notification_time).total_seconds()
        if diff < 600:
            logger.debug(f"Last Pushover was {diff:.1f}s ago; skipping to avoid spam.")
            return 0

    payload = {
        "token": PUSHOVER_TOKEN,
        "user": PUSHOVER_USER,
        "title": title,
        "message": message
    }
    try:
        r = requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=10)
        if r.status_code == 200:
            _last_notification_time = now
            logger.info("Pushover notification sent successfully.")
        else:
            logger.error(f"Pushover returned status {r.status_code}: {r.text}")
        return r.status_code
    except Exception as e:
        logger.error(f"Error sending Pushover: {e}")
        return 0
