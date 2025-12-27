# src/config.py

import os
import re
from pathlib import Path
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

# Attempt to load .env from the project root
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

# -------------------------------------------
# Required / Recommended environment variables
# -------------------------------------------
BASE_DIR = Path(__file__).parent.parent  # This is dispatch_transcriber/
DB_PATH: str = os.getenv("DB_PATH", "transcriptions.db")
SQLITE_DB_PATH = BASE_DIR / DB_PATH
ALERT_PATTERNS_FILE = BASE_DIR / "alert_patterns.txt"
PROMPT_FILE         = BASE_DIR / "prompt.txt"



# Filtered words are words that indicate Broadcastify is currently playing a 30-second advertisement on the stream
FILTERED_WORDS_FILE = BASE_DIR / "filtered_words.txt"

BROADCASTIFY_URL=str = os.getenv("BROADCASTIFY_URL", "").strip()

OPENAI_API_KEY = str = os.getenv("OPENAI_API_KEY", "").strip()
if not OPENAI_API_KEY:
    raise RuntimeError("Missing required environment variable: OPENAI_API_KEY")

BROADCASTIFY_URL = str = os.getenv("BROADCASTIFY_URL", "").strip()
if not BROADCASTIFY_URL:
    raise RuntimeError("Missing required environment variable: BROADCASTIFY_URL")

PUSHOVER_TOKEN = str = os.getenv("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = str = os.getenv("PUSHOVER_USER", "").strip()
USE_PUSHOVER: bool = bool(PUSHOVER_TOKEN and PUSHOVER_USER)

THRESHOLD_DB: float = float(os.getenv("THRESHOLD_DB", "-50"))
LOOKBACK_MS: int = int(os.getenv("LOOKBACK_MS", "1000"))
MIN_SILENCE_LEN: int = int(os.getenv("MIN_SILENCE_LEN", "500"))

RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", "recordings"))
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

CHROMEDRIVER_PATH = os.getenv("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")

# If set to 0/false, transcripts will only be saved locally and
# not POSTed to the remote /transcription endpoint.
POST_TRANSCRIPTIONS = os.getenv("POST_TRANSCRIPTIONS", "1").lower() not in {"0", "false", "no"}

# -------------------------------------------
# Derived / Default values
# -------------------------------------------

if PROMPT_FILE.exists():
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        DISPATCH_PROMPT = f.read().strip()
else:
    DISPATCH_PROMPT = None

if FILTERED_WORDS_FILE.exists():
    with open(FILTERED_WORDS_FILE, "r", encoding="utf-8") as f:
        FILTERED_WORDS = [line.strip().lower() for line in f if line.strip() and not line.strip().startswith("#")]
else:
    FILTERED_WORDS = []

SAMPLE_RATE = 16000
CHANNELS = 1

LOGGING_FORMAT = "%(asctime)s — %(threadName)s — %(name)s — %(levelname)s — %(message)s"

# ==============================================
# NEW: Alert Patterns
# ==============================================

def load_alert_patterns() -> list[re.Pattern]:
    """
    Reads alert_patterns.txt (ignores blank lines and lines starting with '#'),
    compiles each non‐comment line into a re.Pattern (case‐insensitive).
    Returns a list of compiled regex patterns.
    """
    patterns: list[re.Pattern] = []
    if not ALERT_PATTERNS_FILE.exists():
        return patterns

    with open(ALERT_PATTERNS_FILE, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                # compile using IGNORECASE so we catch “Wagar” as well as “wagar”
                pat = re.compile(line, flags=re.IGNORECASE)
                patterns.append(pat)
            except re.error as e:
                # If a line is not a valid regex, log and skip it
                print(f"Warning: invalid regex in alert_patterns.txt: {line!r} ({e})")
                continue
    return patterns

# Load once at import‐time
ALERT_PATTERNS: list[re.Pattern] = load_alert_patterns()

PLAY_BUTTON_SELECTOR = os.getenv("PLAY_BUTTON_SELECTOR", "button.playpause")
