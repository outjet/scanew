# src/stream_handler.py

import subprocess
import logging
import threading

from config import BROADCASTIFY_URL, SAMPLE_RATE, CHANNELS

logger = logging.getLogger(__name__)

def _drain_ffmpeg_stderr(process: subprocess.Popen) -> None:
    if not process.stderr:
        return
    for line in iter(process.stderr.readline, ""):
        line = line.strip()
        if line:
            logger.debug("FFmpeg: %s", line)


def start_ffmpeg_stream(stream_url: str = BROADCASTIFY_URL):
    """
    Starts an FFmpeg process to capture an audio stream and pipe it to stdout
    as raw PCM data.

    Args:
        stream_url: The URL of the audio stream to capture.

    Returns:
        A subprocess.Popen object representing the running FFmpeg process.
    """
    logger.info(f"Starting FFmpeg for stream: {stream_url}")
    args = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-nostdin",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-rw_timeout", "15000000",
        "-i", stream_url,
        "-f", "s16le",          # Output format: signed 16-bit little-endian PCM
        "-ac", str(CHANNELS),   # Number of audio channels
        "-ar", str(SAMPLE_RATE),# Audio sample rate
        "-",                    # Output to stdout
    ]
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(
            target=_drain_ffmpeg_stderr,
            args=(process,),
            daemon=True,
            name="FFmpegStderr",
        ).start()
        logger.info("FFmpeg process started successfully.")
        return process
    except FileNotFoundError:
        logger.error("FFmpeg not found. Please ensure FFmpeg is installed and in your PATH.")
        return None
    except Exception as e:
        logger.exception(f"Failed to start FFmpeg process: {e}")
        return None
