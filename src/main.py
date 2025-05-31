# src/main.py

import logging
import sys
import threading
import tempfile
import shutil
import pathlib
from datetime import datetime, timezone
from queue import Queue
import sqlite3
from config import SQLITE_DB_PATH
from config import (
    LOGGING_FORMAT,
    SAMPLE_RATE,
    CHANNELS,
    MIN_SILENCE_LEN,
    THRESHOLD_DB,
    LOOKBACK_MS,
    RECORDINGS_DIR,
)
from broadcaster import start_and_monitor_broadcastify
from audio import AudioRecorder
from transcribe import transcribe_full_segment
from filters import filter_transcript
from db import initialize_database, insert_transcription
from notifier import send_pushover, matches_alert_pattern
from utils import post_transcription_with_retry

# ---------------------------
# Basic Logging Configuration
# ---------------------------

logging.basicConfig(
    level=logging.INFO,
    format=LOGGING_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("dispatch_transcriber.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)
#hide httpx INFO 
logging.getLogger("httpx").setLevel(logging.WARNING)

timestamp_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def main():
    # 1) Initialize SQLite
    initialize_database()

    # 2) Create a queue for audio segments (paths to temp WAVs)
    segment_queue: Queue = Queue()

    # 3) Launch the Selenium/Broadcastify thread
    broadcaster_thread = threading.Thread(
        target=start_and_monitor_broadcastify,
        name="BroadcasterMonitor",
        daemon=True
    )
    broadcaster_thread.start()
    logger.info("Started BroadcasterMonitor thread.")

    # 4) Launch the AudioRecorder thread
    audio_recorder = AudioRecorder(
        segment_queue=segment_queue,
        sample_rate=SAMPLE_RATE,
        channels=CHANNELS,
        threshold_db=THRESHOLD_DB,
        lookback_ms=LOOKBACK_MS
    )
    audio_recorder.start()
    logger.debug("Started AudioRecorder thread.")

    # 5) Main loop: whenever there's a new segment path, process it
    while True:
        try:
            segment_path = segment_queue.get()  # e.g. recordings/temp_2025-05-31_14-32-15.wav
            if not segment_path or not segment_path.exists():
                continue

            logger.debug(f"Processing new audio segment: {segment_path.name}")

            # 6) Transcribe → uses a temporary directory for chunk WAVs
            with tempfile.TemporaryDirectory() as tmpdirname:
                tmpdir = pathlib.Path(tmpdirname)
                transcript = transcribe_full_segment(
                    segment_wav_path=segment_path,
                    temp_chunks_dir=tmpdir,
                    min_silence_len=MIN_SILENCE_LEN,
                    silence_thresh=THRESHOLD_DB
                )

            # 7) If no transcript or only whitespace, skip & delete
            if not transcript:
                logger.info("No transcript returned (maybe blank); deleting temp file.")
                try:
                    segment_path.unlink()
                except Exception:
                    pass
                continue

            # 8) Apply profanity / gibberish filters
            filtered = filter_transcript(transcript)
            if not filtered:
                logger.info("Transcript was filtered out; deleting temp file.")
                try:
                    segment_path.unlink()
                except Exception:
                    pass
                continue

            # 9) Move the temp WAV into a final timestamped filename
            final_stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            final_wav_filename = f"{final_stamp}.wav"
            final_wav_path = RECORDINGS_DIR / final_wav_filename
            shutil.move(str(segment_path), str(final_wav_path))
            logger.debug(f"Saved validated segment WAV as: {final_wav_filename}")

            # 10) Insert into SQLite
            timestamp_iso = datetime.now().isoformat()
            conn = sqlite3.connect(str(SQLITE_DB_PATH))
            row_id = insert_transcription(
                timestamp_iso=timestamp_iso,
                wav_filename=final_wav_filename,
                transcript=filtered,
                notified=False,
                pushover_code=None,
                response_code=None
            )

            # Construct URL if needed
            file_url = f"https://lkwd.agency/recordings/{final_wav_filename}"
            post_transcription_with_retry(timestamp_iso, file_url, filtered, row_id, conn)
            conn.close()

            # 11) **Notification (only if matches an alert pattern)**
            #     We pass filtered transcript (or transcript) to check patterns
            if matches_alert_pattern(filtered):
                # Send only the first 100 characters as message
                msg = (filtered[:100] + "...") if len(filtered) > 100 else filtered
                pushover_code = send_pushover(
                    title="🚨 Priority Dispatch Alert",
                    message=msg,
                    force=False
                )
                logger.info(f"Pushover returned HTTP code: {pushover_code}")
            else:
                logger.debug("Transcript did not match any high‐priority pattern; no Pushover sent.")

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received. Stopping audio recorder thread.")
            audio_recorder.stop()
            break
        except Exception as e:
            logger.exception(f"Error in main loop: {e}")
            continue


if __name__ == "__main__":
    main()
