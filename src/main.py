# src/main.py

import logging
import sys
import threading
import time
import json
import tempfile
import shutil
import pathlib
from datetime import datetime, timezone
from queue import Queue

from redis import Redis
try:
    from .config import (
        LOGGING_FORMAT,
        SAMPLE_RATE,
        CHANNELS,
        MIN_SILENCE_LEN,
        THRESHOLD_DB,
        LOOKBACK_MS,
        RECORDINGS_DIR,
        POST_TRANSCRIPTIONS,
        AUDIO_STALL_SECONDS,
        AUDIO_HEARTBEAT_SEC,
        REDIS_URL,
        TRANSCRIPTION_STALL_SECONDS,
        SILENCE_DB_THRESHOLD,
        TRANSCRIPTION_PROVIDER,
        TRANSCRIPTION_MODEL,
    )
    from .stream_handler import start_ffmpeg_stream
    from .audio import AudioRecorder
    from .transcribe import transcribe_full_segment
    from .filters import filter_transcript
    from .db import (
        initialize_database,
        insert_transcription,
        update_transcription_classification,
        update_transcription_classification_usage,
    )
    from .notifier import send_pushover, matches_alert_pattern
    from .utils import (
        classify_transcript_intent_with_metadata,
        post_transcription_with_retry,
        copy_to_raspberry_pi,
    )
except ImportError:  # pragma: no cover - allows running as a top-level script module
    from config import (
        LOGGING_FORMAT,
        SAMPLE_RATE,
        CHANNELS,
        MIN_SILENCE_LEN,
        THRESHOLD_DB,
        LOOKBACK_MS,
        RECORDINGS_DIR,
        POST_TRANSCRIPTIONS,
        AUDIO_STALL_SECONDS,
        AUDIO_HEARTBEAT_SEC,
        REDIS_URL,
        TRANSCRIPTION_STALL_SECONDS,
        SILENCE_DB_THRESHOLD,
        TRANSCRIPTION_PROVIDER,
        TRANSCRIPTION_MODEL,
    )
    from stream_handler import start_ffmpeg_stream
    from audio import AudioRecorder
    from transcribe import transcribe_full_segment
    from filters import filter_transcript
    from db import (
        initialize_database,
        insert_transcription,
        update_transcription_classification,
        update_transcription_classification_usage,
    )
    from notifier import send_pushover, matches_alert_pattern
    from utils import (
        classify_transcript_intent_with_metadata,
        post_transcription_with_retry,
        copy_to_raspberry_pi,
    )

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
logging.getLogger("paramiko").setLevel(logging.WARNING)

timestamp_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def main():
    logger.info(
        "Transcription backend configured: provider=%s model=%s",
        TRANSCRIPTION_PROVIDER,
        TRANSCRIPTION_MODEL,
    )

    # 1) Initialize SQLite
    initialize_database()

    # 2) Create a queue for audio segments (paths to temp WAVs)
    segment_queue: Queue = Queue()
    classification_queue: Queue = Queue()

    def publish_transcription_update(payload: dict):
        try:
            redis_client = Redis.from_url(REDIS_URL)
            redis_client.publish("sse_channel", json.dumps(payload))
        except Exception as e:
            logger.warning("Failed to publish transcription update to Redis: %s", e)

    def classification_worker():
        while True:
            row_id, timestamp_iso, wav_filename, transcript = classification_queue.get()
            try:
                class_code, usage_metadata = classify_transcript_intent_with_metadata(transcript)
                update_transcription_classification(row_id, class_code)
                update_transcription_classification_usage(
                    row_id,
                    model=usage_metadata.get("model"),
                    prompt_tokens=usage_metadata.get("prompt_tokens"),
                    candidate_tokens=usage_metadata.get("candidate_tokens"),
                    total_tokens=usage_metadata.get("total_tokens"),
                )
                payload = {
                    "type": "classification_update",
                    "id": row_id,
                    "timestamp": timestamp_iso,
                    "wav_filename": wav_filename,
                    "class_code": class_code,
                    "initialdispatch": class_code == 1,
                }
                publish_transcription_update(payload)
            except Exception:
                logger.exception("Failed to classify transcription row %s", row_id)
            finally:
                classification_queue.task_done()

    def start_pipeline():
        ffmpeg_proc = start_ffmpeg_stream()
        if not ffmpeg_proc or not ffmpeg_proc.stdout:
            logger.critical("Failed to start FFmpeg stream.")
            return None, None
        recorder = AudioRecorder(
            segment_queue=segment_queue,
            input_stream=ffmpeg_proc.stdout,
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
            threshold_db=THRESHOLD_DB,
            lookback_ms=LOOKBACK_MS,
            heartbeat_sec=AUDIO_HEARTBEAT_SEC,
        )
        recorder.start()
        logger.debug("Started AudioRecorder thread.")
        return ffmpeg_proc, recorder

    def stop_pipeline(ffmpeg_proc, recorder):
        if recorder:
            recorder.stop()
        if ffmpeg_proc:
            ffmpeg_proc.terminate()
            try:
                ffmpeg_proc.wait(timeout=5)
            except Exception:
                ffmpeg_proc.kill()
        if recorder and recorder.is_alive():
            recorder.join(timeout=2)

    # 3) Start FFmpeg to capture the audio stream
    ffmpeg_process, audio_recorder = start_pipeline()
    if not ffmpeg_process or not audio_recorder:
        logger.critical("Failed to start FFmpeg stream. Exiting.")
        sys.exit(1)

    state_lock = threading.Lock()
    state = {"ffmpeg": ffmpeg_process, "recorder": audio_recorder}

    def restart_pipeline(reason):
        logger.warning("Restarting FFmpeg pipeline: %s", reason)
        with state_lock:
            old_ffmpeg = state["ffmpeg"]
            old_recorder = state["recorder"]
            new_ffmpeg, new_recorder = start_pipeline()
            if new_ffmpeg and new_recorder:
                stop_pipeline(old_ffmpeg, old_recorder)
                state["ffmpeg"] = new_ffmpeg
                state["recorder"] = new_recorder
            else:
                logger.error("Failed to restart FFmpeg pipeline; keeping existing process.")

    def monitor_pipeline():
        while True:
            time.sleep(5)
            with state_lock:
                ffmpeg_proc = state["ffmpeg"]
                recorder = state["recorder"]
            if ffmpeg_proc and ffmpeg_proc.poll() is not None:
                restart_pipeline(f"ffmpeg exited with code {ffmpeg_proc.returncode}")
                continue
            if recorder and recorder.last_read_age() > AUDIO_STALL_SECONDS:
                restart_pipeline(f"no audio bytes for {AUDIO_STALL_SECONDS}s")
                continue
            if recorder:
                transcription_age = recorder.last_transcription_age()
                last_db = recorder.last_db()
                if (
                    transcription_age is not None
                    and transcription_age > TRANSCRIPTION_STALL_SECONDS
                    and last_db is not None
                    and last_db <= SILENCE_DB_THRESHOLD
                ):
                    restart_pipeline(
                        f"no transcriptions for {int(transcription_age)}s and silence db={last_db:.1f}"
                    )

    threading.Thread(target=monitor_pipeline, daemon=True, name="StreamWatchdog").start()
    threading.Thread(target=classification_worker, daemon=True, name="Classifier").start()

    # 5) Main loop: whenever there's a new segment path, process it
    while True:
        try:
            segment_path = segment_queue.get()
            if not segment_path or not segment_path.exists():
                continue

            logger.debug(f"Processing new audio segment: {segment_path.name}")

            # 6) Transcribe
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
                logger.debug("No transcript returned; deleting temp file.")
                try:
                    segment_path.unlink()
                except Exception:
                    pass
                continue

            filtered = filter_transcript(transcript)
            if not filtered:
                logger.debug("Transcript filtered out; deleting temp file.")
                try:
                    segment_path.unlink()
                except Exception:
                    pass
                continue

            # 9) Move the temp WAV into a day-based folder with a unique timestamped filename.
            final_dt = datetime.now()
            day_folder = final_dt.strftime("%Y-%m-%d")
            final_stamp = final_dt.strftime("%Y-%m-%d_%H-%M-%S_%f")
            final_wav_filename = f"{day_folder}/{final_stamp}.wav"
            final_wav_path = RECORDINGS_DIR / day_folder / f"{final_stamp}.wav"
            final_wav_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(segment_path), str(final_wav_path))
            logger.debug(f"Saved WAV as: {final_wav_filename}")

            # Copy to Raspberry Pi
            copy_success = copy_to_raspberry_pi(str(final_wav_path), final_wav_filename)
            if not copy_success:
                logger.warning(f"Failed to copy {final_wav_filename} to Raspberry Pi. No public URL will be generated.")
                final_wav_filename = None

            timestamp_iso = datetime.now(timezone.utc).isoformat()
            alert_match = matches_alert_pattern(filtered)
            row_id = insert_transcription(
                timestamp_iso=timestamp_iso,
                wav_filename=final_wav_filename,
                transcript=filtered,
                notified=False,
                pushover_code=None,
                response_code=None,
                alert=alert_match,
                bestof=False,
            )
            with state_lock:
                current_recorder = state.get("recorder")
            if current_recorder:
                current_recorder.mark_transcription()
            if final_wav_filename:
                file_url = f"https://lkwd.agency/recordings/{final_wav_filename}"
                if POST_TRANSCRIPTIONS:
                    post_transcription_with_retry(timestamp_iso, file_url, filtered, row_id)

            payload = {
                "type": "transcription",
                "id": row_id,
                "timestamp": timestamp_iso,
                "wav_filename": final_wav_filename,
                "transcript": filtered,
                "alert_match": alert_match,
                "formatted_timestamp": datetime.now().strftime("%a %d-%b %H:%M:%S"),
                "text": filtered,
                "url": f"/recordings/{final_wav_filename}" if final_wav_filename else None,
                "class_code": None,
                "initialdispatch": False,
            }
            publish_transcription_update(payload)
            classification_queue.put((row_id, timestamp_iso, final_wav_filename, filtered))

            if alert_match:
                msg = filtered[:100] + "..." if len(filtered) > 100 else filtered
                code = send_pushover(
                    title="🚨 Priority Dispatch Alert",
                    message=msg,
                    force=False
                )
                if code == 0:
                    logger.warning("Pushover returned 0—see previous logs for the exception.")
                else:
                    logger.info(f"Pushover succeeded: {code}")
            # else: no alert, keep looping

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received; shutting down.")
            with state_lock:
                current_ffmpeg = state["ffmpeg"]
                current_recorder = state["recorder"]
            if current_recorder:
                current_recorder.stop()
            if current_ffmpeg:
                current_ffmpeg.terminate()
            try:
                # Wait a moment for the process to terminate
                if current_ffmpeg:
                    current_ffmpeg.wait(timeout=5)
            except Exception:
                logger.warning("FFmpeg process did not terminate gracefully, killing.")
                if current_ffmpeg:
                    current_ffmpeg.kill()
            break
        except Exception as e:
            logger.exception(f"Error in main loop: {e}")
            continue


if __name__ == "__main__":
    main()
