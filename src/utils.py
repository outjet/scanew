# src/utils.py
import time
import functools
import requests
import logging
from colorama import Fore, Style, init as colorama_init
import datetime
import re
import sys
from config import ALERT_PATTERNS
import json
import paramiko
import os

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

def post_transcription_with_retry(timestamp: str, url: str, text: str, row_id: int, conn):
    post_url = "https://lkwd.agency/transcription"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ScannerStream0.6"
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
            conn.execute("UPDATE transcriptions SET response_code = ? WHERE id = ?", (response.status_code, row_id))
            conn.commit()
           #logger.info(f"Posted transcript ({row_id}) OK: {response.status_code}")
            return response.status_code
        except requests.exceptions.RequestException as e:
            logger.error(f"Post failed (attempt {attempt + 1}): {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response content: {e.response.content}")
                if e.response.status_code:
                    conn.execute("UPDATE transcriptions SET response_code = ? WHERE id = ?", (e.response.status_code, row_id))
                    conn.commit()
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
    logger.error("Final failure after retries.")
    return 0

# Raspberry Pi SFTP constants
RASPBERRY_PI_IP = "172.16.0.40"
RASPBERRY_PI_USER = "outjet"
RASPBERRY_PI_PATH = "/home/outjet/Projects/dispatch/static/recordings"

def copy_to_raspberry_pi(local_file_path, remote_file_name, max_retries=3):
    """
    Copy a file to the Raspberry Pi using SFTP with SSH key authentication.
    Returns True on success, False on failure after retries.
    """
    for attempt in range(max_retries):
        ssh = None
        sftp = None
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                RASPBERRY_PI_IP,
                username=RASPBERRY_PI_USER,
                key_filename=os.path.expanduser('~/.ssh/id_rsa'),
                timeout=10
            )
            sftp = ssh.open_sftp()
            # Ensure destination directory exists
            try:
                sftp.stat(RASPBERRY_PI_PATH)
            except IOError:
                sftp.mkdir(RASPBERRY_PI_PATH)
            remote_path = f"{RASPBERRY_PI_PATH}/{remote_file_name}"
            sftp.put(local_file_path, remote_path)
            return True
        except Exception as e:
            logger.warning(f"SFTP attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
        finally:
            if sftp is not None:
                sftp.close()
            if ssh is not None:
                ssh.close()
    return False