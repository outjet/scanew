# src/audio.py

import audioop
import math
import threading
import logging
from queue import Queue
from datetime import datetime
import wave
import pyaudio
from pathlib import Path

from config import SAMPLE_RATE, CHANNELS, RECORDINGS_DIR

logger = logging.getLogger(__name__)

class AudioRecorder(threading.Thread):
    """
    Continuously reads from the default recording device (loopback or stereo-mix),
    applies a simple RMS-based VAD, and whenever it detects a speech segment,
    it writes the raw frames to a temp WAV file and enqueues it for transcription.
    """

    def __init__(
        self,
        segment_queue: Queue,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        input_device_index: int | None = None
    ):
        super().__init__(daemon=True, name="AudioRecorder")
        self.segment_queue = segment_queue
        self.sample_rate = sample_rate
        self.channels = channels
        self.threshold_db = -40
        self.lookback_ms = 1000
        self.input_device_index = input_device_index

        self.chunk_size = 1024  # read in 1024-sample increments
        self.audio_interface = pyaudio.PyAudio()
        self.sample_width = self.audio_interface.get_sample_size(pyaudio.paInt16)
        self.silence_buffer_chunks = int(
            math.ceil((self.lookback_ms / 1000.0) * (self.sample_rate / self.chunk_size))
        )

        self.stream = None
        self._stop_event = threading.Event()

    def run(self):
        logger.info("Starting AudioRecorder thread.")
        try:
            logger.debug(f"Opening PyAudio stream on device index {self.input_device_index}")
            self.stream = self.audio_interface.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.input_device_index,
                frames_per_buffer=self.chunk_size
            )
        except Exception as e:
            logger.exception(f"Failed to open audio stream: {e}")
            return

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
            logger.exception(f"AudioRecorder encountered an error: {e}")
        finally:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
            self.audio_interface.terminate()
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
        silent_frame = (b"\x00" * self.chunk_size * self.channels * self.sample_width)
        for _ in range(self.silence_buffer_chunks):
            lookback_buffer.append(silent_frame)

        while not self._stop_event.is_set():
            try:
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
            except Exception as e:
                logger.warning(f"Error reading from audio stream: {e}")
                return None

            rms = audioop.rms(data, self.sample_width)
            db = 20 * math.log10(rms) if rms > 0 else -float("inf")

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