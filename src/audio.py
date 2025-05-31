# src/audio.py
import audioop
import math
import threading
import logging
from queue import Queue
from datetime import datetime
import wave
from config import INPUT_DEVICE_INDEX
import pyaudio

from config import THRESHOLD_DB, LOOKBACK_MS, SAMPLE_RATE, CHANNELS, RECORDINGS_DIR

logger = logging.getLogger(__name__)

class AudioRecorder(threading.Thread):
    """
    Continuously reads from the default recording device (loopback or stereo-mix),
    applies a simple RMS‐based VAD, and whenever it detects a speech segment,
    it writes the raw frames to a temp WAV file and enqueues it for transcription.
    """

    def __init__(self, segment_queue: Queue, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS,
                 threshold_db: float = THRESHOLD_DB, lookback_ms: int = LOOKBACK_MS):
        super().__init__(daemon=True, name="AudioRecorder")
        self.segment_queue = segment_queue
        self.sample_rate = sample_rate
        self.channels = channels
        self.threshold_db = threshold_db
        self.lookback_ms = lookback_ms

        self.chunk_size = 1024  # read in 1024-sample increments
        self.sample_width = pyaudio.PyAudio().get_sample_size(pyaudio.paInt16)
        self.silence_buffer_chunks = int(math.ceil((lookback_ms / 1000.0) * (sample_rate / self.chunk_size)))

        self.audio_interface = pyaudio.PyAudio()
        self.stream = None
        self._stop_event = threading.Event()

    def run(self):
        logger.info("Starting AudioRecorder thread.")
        self.stream = self.audio_interface.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=INPUT_DEVICE_INDEX,
            frames_per_buffer=self.chunk_size
        )
        try:
            while not self._stop_event.is_set():
                frames = self._record_one_segment()
                if frames:
                    # Write the raw frames to a temp WAV, then enqueue the path
                    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    temp_wav_path = RECORDINGS_DIR / f"temp_{timestamp}.wav"
                    self._write_wav(frames, temp_wav_path)
                    logger.debug(f"Wrote temp WAV: {temp_wav_path}")
                    self.segment_queue.put(temp_wav_path)
        except Exception as e:
            logger.exception(f"AudioRecorder encountered an error: {e}")
        finally:
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
        lookback_buffer = []
        active_buffer = []
        silence_count = 0
        recording = False

        # Pre-fill the lookback buffer with silence chunks (for initial padding)
        for _ in range(self.silence_buffer_chunks):
            lookback_buffer.append(None)

        while not self._stop_event.is_set():
            data = self.stream.read(self.chunk_size, exception_on_overflow=False)
            rms = audioop.rms(data, self.sample_width)  # root-mean-square of the raw PCM
            db = 20 * math.log10(rms) if rms > 0 else -float("inf")

            if db > self.threshold_db:
                # We’re “in speech”
                if not recording:
                    # First chunk above threshold → start recording
                    recording = True
                    # Dump lookback_buffer (ignoring the initial None placeholders)
                    for buffered_frame in lookback_buffer:
                        if buffered_frame:
                            active_buffer.append(buffered_frame)
                active_buffer.append(data)
                silence_count = 0
            else:
                if recording:
                    # We’re in silence now, but after recording has started
                    silence_count += 1
                    active_buffer.append(data)
                    if silence_count >= self.silence_buffer_chunks:
                        # End of this speech segment
                        return active_buffer
                else:
                    # Still in lookback phase; rotate the lookback buffer
                    lookback_buffer.pop(0)
                    lookback_buffer.append(data)
            # If not recording yet, just keep filling lookback_buffer

        # If stop was requested mid‐segment, return what we have
        return None

    def _write_wav(self, frames, wav_path):
        """
        Takes a list of raw PCM frames, writes them out to `wav_path`.
        """
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.sample_width)
            wf.setframerate(self.sample_rate)
            wf.writeframes(b"".join(frames))

