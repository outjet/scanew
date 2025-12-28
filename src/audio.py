# src/audio.py

import audioop
import math
import threading
import logging
import time
from queue import Queue
from datetime import datetime
import wave
from pathlib import Path
from typing import IO

from config import THRESHOLD_DB, LOOKBACK_MS, SAMPLE_RATE, CHANNELS, RECORDINGS_DIR, AUDIO_HEARTBEAT_SEC

logger = logging.getLogger(__name__)

class AudioRecorder(threading.Thread):
    """
    Continuously reads raw PCM data from an input stream, applies a simple
    RMS-based VAD, and whenever it detects a speech segment, it writes the raw
    frames to a temp WAV file and enqueues it for transcription.
    """

    def __init__(
        self,
        segment_queue: Queue,
        input_stream: IO[bytes],
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        threshold_db: float = THRESHOLD_DB,
        lookback_ms: int = LOOKBACK_MS,
        heartbeat_sec: int = AUDIO_HEARTBEAT_SEC,
    ):
        super().__init__(daemon=True, name="AudioRecorder")
        self.segment_queue = segment_queue
        self.input_stream = input_stream
        self.sample_rate = sample_rate
        self.channels = channels
        self.threshold_db = threshold_db
        self.lookback_ms = lookback_ms

        self.chunk_size = 1024  # read in 1024-sample increments
        self.sample_width = 2  # Corresponds to 16-bit PCM
        self.bytes_per_chunk = self.chunk_size * self.channels * self.sample_width
        self.silence_buffer_chunks = int(
            math.ceil((lookback_ms / 1000.0) * (sample_rate / self.chunk_size))
        )
        self._stop_event = threading.Event()
        self._heartbeat_interval = heartbeat_sec
        self._last_heartbeat = time.monotonic()
        self._last_read_time = time.monotonic()
        self._bytes_read_total = 0
        self._bytes_read_last = 0
        self._last_db = None
        self._last_transcription_time = None

    def run(self):
        logger.info("Starting AudioRecorder thread.")
        try:
            while not self._stop_event.is_set():
                frames = self._record_one_segment()
                if frames:
                    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    temp_wav_path = RECORDINGS_DIR / f"temp_{timestamp}.wav"
                    self._write_wav(frames, temp_wav_path)
                    logger.debug(f"Wrote temp WAV: {temp_wav_path}")
                    self.segment_queue.put(temp_wav_path)
        except Exception as e:
            # Avoid logging errors if the thread was stopped intentionally
            if not self._stop_event.is_set():
                logger.exception("AudioRecorder encountered an error")
        finally:
            logger.info("AudioRecorder thread stopped.")

    def stop(self):
        self._stop_event.set()

    def _record_one_segment(self):
        """
        Listens for one “speech segment” (from silence → speech → silence).
        Returns the list of raw PCM frames for that segment, or None if stop was requested.
        """
        lookback_buffer: list[bytes] = []
        active_buffer: list[bytes] = []
        silence_count = 0
        recording = False

        # Pre-fill the lookback buffer with silent chunks
        silent_frame = (b"\x00" * self.bytes_per_chunk)
        for _ in range(self.silence_buffer_chunks):
            lookback_buffer.append(silent_frame)

        while not self._stop_event.is_set():
            try:
                data = self.input_stream.read(self.bytes_per_chunk)
                if not data:
                    logger.warning("Audio stream ended.")
                    return None
            except Exception as e:
                logger.warning(f"Error reading from audio stream: {e}")
                return None

            rms = audioop.rms(data, self.sample_width)
            db = 20 * math.log10(rms) if rms > 0 else -float("inf")
            self._bytes_read_total += len(data)
            self._last_read_time = time.monotonic()
            self._last_db = db
            if self._heartbeat_interval > 0:
                now = time.monotonic()
                if now - self._last_heartbeat >= self._heartbeat_interval:
                    delta = self._bytes_read_total - self._bytes_read_last
                    elapsed = now - self._last_heartbeat
                    rate = (delta / elapsed) if elapsed > 0 else 0.0
                    logger.info(
                        "Audio heartbeat: bytes=%d bytes_per_sec=%.1f last_db=%.1f last_transcription_age=%s",
                        delta,
                        rate,
                        db,
                        self._format_transcription_age(now),
                    )
                    self._bytes_read_last = self._bytes_read_total
                    self._last_heartbeat = now

            if db > self.threshold_db:
                # We're “in speech”
                if not recording:
                    recording = True
                    # Dump lookback_buffer
                    active_buffer.extend(lookback_buffer)
                active_buffer.append(data)
                silence_count = 0
            else:
                if recording:
                    silence_count += 1
                    active_buffer.append(data)
                    if silence_count >= self.silence_buffer_chunks:
                        return active_buffer
                else:
                    # Still in lookback phase
                    lookback_buffer.pop(0)
                    lookback_buffer.append(data)
            # Loop continues until either stop event or segment detected

        # If stop was requested mid-segment
        return None

    def last_read_age(self) -> float:
        return time.monotonic() - self._last_read_time

    def last_db(self):
        return self._last_db

    def mark_transcription(self) -> None:
        self._last_transcription_time = time.monotonic()

    def last_transcription_age(self):
        if self._last_transcription_time is None:
            return None
        return time.monotonic() - self._last_transcription_time

    def _format_transcription_age(self, now: float) -> str:
        if self._last_transcription_time is None:
            return "n/a"
        age = now - self._last_transcription_time
        return f"{age:.0f}s"

    def _write_wav(self, frames: list[bytes], wav_path: Path):
        """
        Takes a list of raw PCM frames, writes them out to `wav_path`.
        """
        try:
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(self.sample_width)
                wf.setframerate(self.sample_rate)
                wf.writeframes(b"".join(frames))
        except Exception as e:
            logger.error(f"Failed to write WAV file {wav_path}: {e}")
