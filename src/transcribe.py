# src/transcribe.py

import logging
from pathlib import Path
from typing import Optional
from utils import log_transcription_to_console

import openai
from openai._exceptions import OpenAIError

from config import OPENAI_API_KEY, DISPATCH_PROMPT
from utils import retry_on_exception
from splitter import split_on_silence

logger = logging.getLogger(__name__)

client = openai.OpenAI(api_key=OPENAI_API_KEY)

@retry_on_exception(exceptions=(OpenAIError,), max_attempts=3, initial_delay=1, backoff_factor=2)
def transcribe_chunk(chunk_path: Path) -> str:
    logger.debug(f"Transcribing chunk via Whisper: {chunk_path}")
    with open(chunk_path, "rb") as f:
        resp = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            temperature=0.1,
            prompt=DISPATCH_PROMPT
        )
    text = resp.text.strip()
    logger.debug(f"Whisper returned: {text!r} for {chunk_path.name}")
    return text

def transcribe_full_segment(
    segment_wav_path: Path,
    temp_chunks_dir: Path,
    min_silence_len: int,
    silence_thresh: int
) -> Optional[str]:
    temp_chunks_dir.mkdir(parents=True, exist_ok=True)

    chunk_files = split_on_silence(
        wav_path=segment_wav_path,
        output_dir=temp_chunks_dir,
        min_silence_len=min_silence_len,
        silence_thresh=silence_thresh
    )

    if not chunk_files:
        logger.info(f"No nonsilent chunks detected in {segment_wav_path}. Skipping transcription.")
        return None

    transcripts = []
    for chunk_path in chunk_files:
        try:
            text = transcribe_chunk(chunk_path)
            if text:
                transcripts.append(text)
        except Exception as e:
            logger.error(f"Failed to transcribe chunk {chunk_path}: {e}")
            continue

    for c in chunk_files:
        try:
            c.unlink()
        except Exception:
            pass

    final_transcript = " ".join(transcripts).strip()
    logger.debug(f"Full transcript for {segment_wav_path.name!r}: {final_transcript!r}")
    log_transcription_to_console(final_transcript)
    return final_transcript
