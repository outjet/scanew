# src/utils.py
import time
import functools
import requests
import logging
from colorama import Fore, Style, init as colorama_init
import datetime
import re
import sys
from config import (
    ALERT_PATTERNS,
    CLASSIFICATION_MIN_TEXT_LENGTH,
    VERTEX_API_KEY,
    VERTEX_CLASSIFICATION_ENABLED,
    VERTEX_CLASSIFICATION_TIMEOUT_SEC,
    VERTEX_EXPRESS_ENDPOINT,
)
import json
import paramiko
import os

from db import update_transcription_response_code

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """Classify this Lakewood, Ohio police/fire dispatch transcript.

Return JSON with exactly one field:
{"class_code":"0"|"1"|"2"}

"1" = initial dispatch for a new incident, usually includes a location and a request for police or fire service or a reported problem
"2" = supplemental traffic for an existing incident, including status updates, transport, 10-codes, unit chatter, follow-up details, or descriptions after the dispatch
"0" = other radio noise, radio checks, administrative traffic, accidental audio, or non-dispatch content

Transcript:
"""

def retry_on_exception(
    *,
    exceptions: tuple = (Exception,),
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0
):
    """
    Decorator to retry a function if it raises one of the specified exceptions.
    Waits initial_delay seconds before first retry, then multiplies by backoff_factor each time.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            attempt = 1
            while True:
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    if attempt >= max_attempts:
                        logger.error(f"Function {fn.__name__} failed after {attempt} attempts: {e}")
                        raise
                    else:
                        logger.warning(
                            f"Function {fn.__name__} raised {e.__class__.__name__} on attempt {attempt}, "
                            f"retrying in {delay} seconds..."
                        )
                        time.sleep(delay)
                        delay *= backoff_factor
                        attempt += 1
        return wrapper
    return decorator

colorama_init()

def log_transcription_to_console(text: str, source: str = "Dispatch"):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")

    # Check for alert match
    matched = any(p.search(text) for p in ALERT_PATTERNS)

    if matched:
        # RED + BOLD
        output = (
            f"{Fore.RED}{Style.BRIGHT}[{timestamp}] {source:<10}:{Style.RESET_ALL} {text}"
        )
        # Optional terminal beep:
        sys.stdout.write("\a")  # <- system bell
        sys.stdout.flush()
    else:
        # Normal green
        output = (
            f"{Fore.GREEN}[{timestamp}] {source:<10}:{Style.RESET_ALL} {text}"
        )

    print(output)

def post_transcription_with_retry(timestamp: str, url: str, text: str, row_id: int):
    # The destination URL is now configurable via environment variable
    post_url = os.getenv("TRANSCRIPTION_POST_URL", "https://lkwd.agency/transcription")
    
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ScannerStream0.7"
    }
    data = {
        "timestamp": timestamp,
        "url": url,
        "text": text
    }

    max_retries = 5
    delay = 1

    for attempt in range(max_retries):
        try:
            logger.debug(f"POST payload: {json.dumps(data)}")
            logger.debug(f"POSTING to {post_url}: {data}")
            response = requests.post(post_url, headers=headers, json=data, timeout=10)
            response.raise_for_status()
            update_transcription_response_code(row_id, response.status_code)
            return response.status_code
        except requests.exceptions.RequestException as e:
            logger.error(f"Post failed (attempt {attempt + 1}): {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response content: {e.response.content}")
                if e.response.status_code:
                    update_transcription_response_code(row_id, e.response.status_code)
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
    logger.error("Final failure after retries.")
    return 0


@retry_on_exception(
    exceptions=(requests.RequestException,),
    max_attempts=3,
    initial_delay=0.5,
    backoff_factor=2,
)
def classify_transcript_intent(text: str) -> int:
    if len(text or "") <= CLASSIFICATION_MIN_TEXT_LENGTH:
        return 0
    if not VERTEX_CLASSIFICATION_ENABLED:
        return 0
    if not VERTEX_API_KEY:
        logger.warning("VERTEX_API_KEY is not configured; defaulting classification to OTHER.")
        return 0

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": f"{CLASSIFICATION_PROMPT}{text}"
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "topP": 0.1,
            "topK": 1,
            "maxOutputTokens": 32,
            "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "class_code": {
                            "type": "STRING",
                            "enum": ["0", "1", "2"],
                        }
                    },
                    "required": ["class_code"],
                },
        },
    }

    response = requests.post(
        VERTEX_EXPRESS_ENDPOINT,
        params={"key": VERTEX_API_KEY},
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=VERTEX_CLASSIFICATION_TIMEOUT_SEC,
    )
    if not response.ok:
        raise requests.HTTPError(
            f"Vertex classification request failed: status={response.status_code} body={response.text[:1000]}",
            response=response,
        )
    data = response.json()
    raw_text = _extract_vertex_text(data)
    if not raw_text:
        logger.warning("Empty classification payload; defaulting to OTHER. raw_response=%s", json.dumps(data)[:1000])
        return 0

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.warning("Malformed classification payload %r; defaulting to OTHER. raw_response=%s", raw_text, json.dumps(data)[:1000])
        return 0

    class_code = parsed.get("class_code")
    if isinstance(class_code, str) and class_code in {"0", "1", "2"}:
        class_code = int(class_code)
    if class_code not in {0, 1, 2}:
        logger.warning("Unexpected class_code %r; defaulting to OTHER. raw_payload=%s", class_code, raw_text)
        return 0
    return class_code


def _extract_vertex_text(data: dict) -> str:
    candidates = data.get("candidates") or []
    for candidate in candidates:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            text = (part.get("text") or "").strip()
            if text:
                return text
    return ""

def copy_to_raspberry_pi(local_file_path, remote_file_name, max_retries=3):
    """
    (DISABLED) This function is a placeholder and does not copy files.
    In the cloud-native architecture, files are served locally by a webserver.
    Returns True to ensure downstream logic continues to operate as expected.
    """
    logger.debug(f"Skipping SFTP copy for {remote_file_name} in cloud-native mode.")
    return True
