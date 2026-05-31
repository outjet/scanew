# src/config.py

import os
import re
from pathlib import Path
from dotenv import load_dotenv
import logging

try:
    from google.cloud import secretmanager
except ImportError:  # pragma: no cover - optional dependency until installed
    secretmanager = None

try:
    import google.auth
except ImportError:  # pragma: no cover - optional dependency until installed
    google = None

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

BROADCASTIFY_URL = str = os.getenv("BROADCASTIFY_URL", "").strip()
if not BROADCASTIFY_URL:
    raise RuntimeError("Missing required environment variable: BROADCASTIFY_URL")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "").strip()

TRANSCRIPTION_PROVIDER = os.getenv("TRANSCRIPTION_PROVIDER", "openai").strip().lower()
if TRANSCRIPTION_PROVIDER == "whisper":
    TRANSCRIPTION_PROVIDER = "openai"
if TRANSCRIPTION_PROVIDER not in {"openai", "deepgram"}:
    raise RuntimeError(
        "Invalid TRANSCRIPTION_PROVIDER. Expected one of: openai, whisper, deepgram"
    )

OPENAI_TRANSCRIPTION_MODEL = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1").strip()
DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "nova-3").strip()
TRANSCRIPTION_MODEL = os.getenv("TRANSCRIPTION_MODEL", "").strip() or (
    DEEPGRAM_MODEL if TRANSCRIPTION_PROVIDER == "deepgram" else OPENAI_TRANSCRIPTION_MODEL
)
DEEPGRAM_LANGUAGE = os.getenv("DEEPGRAM_LANGUAGE", "en").strip()

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _resolve_gcp_project_id() -> str:
    for env_name in ("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "PROJECT_ID"):
        value = os.getenv(env_name, "").strip()
        if value:
            return value

    if google is None:
        return ""

    try:
        _, project_id = google.auth.default()
        return (project_id or "").strip()
    except Exception as e:
        logger.debug("Could not resolve GCP project ID from ADC: %s", e)
        return ""


def _load_secret(secret_name: str) -> str:
    if secretmanager is None:
        logger.debug("google-cloud-secret-manager is not installed; cannot load %s from Secret Manager.", secret_name)
        return ""

    project_id = _resolve_gcp_project_id()
    if not project_id:
        logger.warning("No GCP project ID available; cannot load %s from Secret Manager.", secret_name)
        return ""

    try:
        client = secretmanager.SecretManagerServiceClient()
        secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": secret_path})
        return response.payload.data.decode("utf-8").strip()
    except Exception as e:
        logger.warning("Failed to load secret %s from Secret Manager: %s", secret_name, e)
        return ""

DEEPGRAM_NUMERALS = _env_bool("DEEPGRAM_NUMERALS", True)
DEEPGRAM_SMART_FORMAT = _env_bool("DEEPGRAM_SMART_FORMAT", True)
DEEPGRAM_KEYTERMS_FILE = BASE_DIR / os.getenv("DEEPGRAM_KEYTERMS_FILE", "deepgram_keyterms.txt")
DEEPGRAM_KEYTERMS_EXTRA = os.getenv("DEEPGRAM_KEYTERMS_EXTRA", "").strip()

def _load_deepgram_keyterms() -> list[str]:
    terms: list[str] = []
    if DEEPGRAM_KEYTERMS_FILE.exists():
        with open(DEEPGRAM_KEYTERMS_FILE, "r", encoding="utf-8") as f:
            for raw_line in f:
                term = raw_line.strip()
                if not term or term.startswith("#"):
                    continue
                terms.append(term)
    if DEEPGRAM_KEYTERMS_EXTRA:
        terms.extend([t.strip() for t in DEEPGRAM_KEYTERMS_EXTRA.split(",") if t.strip()])

    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(term)
    return deduped

DEEPGRAM_KEYTERMS = _load_deepgram_keyterms()

if TRANSCRIPTION_PROVIDER == "openai" and not OPENAI_API_KEY:
    raise RuntimeError("Missing required environment variable for provider=openai: OPENAI_API_KEY")
if TRANSCRIPTION_PROVIDER == "deepgram" and not DEEPGRAM_API_KEY:
    raise RuntimeError("Missing required environment variable for provider=deepgram: DEEPGRAM_API_KEY")

PUSHOVER_TOKEN = str = os.getenv("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = str = os.getenv("PUSHOVER_USER", "").strip()
USE_PUSHOVER: bool = bool(PUSHOVER_TOKEN and PUSHOVER_USER)

THRESHOLD_DB: float = float(os.getenv("THRESHOLD_DB", "-50"))
LOOKBACK_MS: int = int(os.getenv("LOOKBACK_MS", "1000"))
MIN_SILENCE_LEN: int = int(os.getenv("MIN_SILENCE_LEN", "500"))
AUDIO_HEARTBEAT_SEC: int = int(os.getenv("AUDIO_HEARTBEAT_SEC", "60"))
AUDIO_STALL_SECONDS: int = int(os.getenv("AUDIO_STALL_SECONDS", "300"))
TRANSCRIPTION_STALL_SECONDS: int = int(os.getenv("TRANSCRIPTION_STALL_SECONDS", "600"))
SILENCE_DB_THRESHOLD: float = float(os.getenv("SILENCE_DB_THRESHOLD", "-80"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

VERTEX_API_KEY = os.getenv("VERTEX_API_KEY", "").strip() or _load_secret("VERTEX_API_KEY")
VERTEX_CLASSIFICATION_ENABLED = _env_bool("VERTEX_CLASSIFICATION_ENABLED", True)
VERTEX_CLASSIFICATION_MODEL = os.getenv("VERTEX_CLASSIFICATION_MODEL", "gemini-3.1-flash-lite").strip()
VERTEX_CLASSIFICATION_TIMEOUT_SEC = float(os.getenv("VERTEX_CLASSIFICATION_TIMEOUT_SEC", "8"))
CLASSIFICATION_MIN_TEXT_LENGTH = int(os.getenv("CLASSIFICATION_MIN_TEXT_LENGTH", "50"))
VERTEX_EXPRESS_ENDPOINT = os.getenv(
    "VERTEX_EXPRESS_ENDPOINT",
    f"https://generativelanguage.googleapis.com/v1beta/models/{VERTEX_CLASSIFICATION_MODEL}:generateContent",
).strip()

RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", "recordings"))
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

CHROMEDRIVER_PATH = os.getenv("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")

# If set to 0/false, transcripts will only be saved locally and
# not POSTed to the remote /transcription endpoint.
POST_TRANSCRIPTIONS = os.getenv("POST_TRANSCRIPTIONS", "1").lower() not in {"0", "false", "no"}

DASHBOARD_WEBHOOK_URL = os.getenv("DASHBOARD_WEBHOOK_URL", "").strip()
DASHBOARD_WEBHOOK_SECRET = os.getenv("DASHBOARD_WEBHOOK_SECRET", "").strip()

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
