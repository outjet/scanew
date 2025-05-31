# src/utils.py
import time
import functools
import logging
from colorama import Fore, Style, init as colorama_init
import datetime
import re
import sys
from config import ALERT_PATTERNS

logger = logging.getLogger(__name__)

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