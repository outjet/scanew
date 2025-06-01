# src/transcribe.py

import logging
from pathlib import Path
from typing import Optional
import wave
from utils import log_transcription_to_console
import re
from collections import Counter

import openai
from openai._exceptions import OpenAIError

from config import OPENAI_API_KEY, DISPATCH_PROMPT
from utils import retry_on_exception
from splitter import split_on_silence

logger = logging.getLogger(__name__)
client = openai.OpenAI(api_key=OPENAI_API_KEY)

# Load the short-prompt text once (project root)
SHORT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "promptshort.txt"
SHORT_PROMPT = SHORT_PROMPT_PATH.read_text().strip()


def get_audio_duration_seconds(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
    return frames / float(rate)


def detect_repeated_phrases(text: str, min_phrase_len: int = 3, max_phrase_len: int = 6, min_repeats: int = 3):
    words = text.split()
    phrase_counts = Counter()
    for n in range(min_phrase_len, max_phrase_len + 1):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i : i + n])
            phrase_counts[phrase] += 1
    return {phrase: count for phrase, count in phrase_counts.items() if count >= min_repeats}


def is_hallucination(text: str, threshold: float = 0.4):
    words = text.split()
    total_words = len(words)
    repeats = detect_repeated_phrases(text)
    for phrase, count in repeats.items():
        word_count = len(phrase.split()) * count
        if word_count / total_words >= threshold:
            return True, phrase, count
    return False, None, 0


def smells_too_long(text: str, audio_duration_sec: float, wps_threshold: float = 5.5, min_words: int = 20) -> bool:
    word_count = len(text.split())
    if word_count < min_words:
        return False
    words_per_second = word_count / max(audio_duration_sec, 0.1)
    return words_per_second > wps_threshold


@retry_on_exception(exceptions=(OpenAIError,), max_attempts=3, initial_delay=1, backoff_factor=2)
def transcribe_chunk(chunk_path: Path, model: str = "whisper-1") -> str:
    duration = get_audio_duration_seconds(chunk_path)
    prompt_to_use = SHORT_PROMPT if duration < 2.5 else DISPATCH_PROMPT

    logger.debug(f"Transcribing {chunk_path.name} ({duration:.2f}s) with model={model}")
    with open(chunk_path, "rb") as f:
        resp = client.audio.transcriptions.create(
            model=model,
            file=f,
            temperature=0.1,
            prompt=prompt_to_use,
        )
    text = resp.text.strip()
    logger.debug(f"{model} returned: {text!r} for {chunk_path.name}")
    return text


def reprocess_with_alternate_model(
    segment_wav_path: Path,
    temp_chunks_dir: Path,
    min_silence_len: int,
    silence_thresh: int
) -> str:
    chunk_files_alt = split_on_silence(
        wav_path=segment_wav_path,
        output_dir=temp_chunks_dir,
        min_silence_len=min_silence_len,
        silence_thresh=silence_thresh,
    )
    transcripts_alt = []
    for chunk_path in chunk_files_alt:
        try:
            text_alt = transcribe_chunk(chunk_path, model="gpt-4o-mini-transcribe")
            if text_alt:
                transcripts_alt.append(text_alt)
        except Exception as e:
            logger.error(f"Failed to transcribe chunk {chunk_path.name} with gpt-4o-mini-transcribe: {e}")
    for c in chunk_files_alt:
        try:
            c.unlink()
        except Exception:
            pass
    alt_final_transcript = " ".join(transcripts_alt).strip()
    logger.debug(f"gpt-4o-mini-transcribe final result for {segment_wav_path.name!r}: {alt_final_transcript!r}")
    log_transcription_to_console(alt_final_transcript)
    return alt_final_transcript


def transcribe_full_segment(
    segment_wav_path: Path,
    temp_chunks_dir: Path,
    min_silence_len: int,
    silence_thresh: int,
) -> Optional[str]:
    temp_chunks_dir.mkdir(parents=True, exist_ok=True)

    # Split the audio into chunks
    chunk_files = split_on_silence(
        wav_path=segment_wav_path,
        output_dir=temp_chunks_dir,
        min_silence_len=min_silence_len,
        silence_thresh=silence_thresh,
    )

    if not chunk_files:
        logger.debug(f"No nonsilent chunks detected in {segment_wav_path}. Skipping transcription.")
        return None

    # Transcribe each chunk with whisper-1, skipping tiny files
    transcripts = []
    for chunk_path in chunk_files:
        duration = get_audio_duration_seconds(chunk_path)
        if duration < 0.25:
            logger.debug(f"Skipping {chunk_path.name}: too short ({duration:.3f}s)")
            try:
                chunk_path.unlink()
            except Exception:
                pass
            continue

        try:
            text = transcribe_chunk(chunk_path, model="whisper-1")
            if text:
                transcripts.append(text)
        except Exception as e:
            logger.error(f"Failed to transcribe chunk {chunk_path.name} with whisper-1: {e}")
            continue

    # Clean up chunk files
    for c in chunk_files:
        try:
            c.unlink()
        except Exception:
            pass

    # Combine into a single transcript
    final_transcript = " ".join(transcripts).strip()

    # Measure full audio duration once
    final_duration = get_audio_duration_seconds(segment_wav_path)

    # Smell test: if too many words for the audio length, retry with gpt-4o-mini-transcribe
    if smells_too_long(final_transcript, final_duration):
        logger.warning(
            f"Transcript too long ({len(final_transcript.split())} words in {final_duration:.2f}s) "
            f"for {segment_wav_path.name}. Retrying with gpt-4o-mini-transcribe."
        )
        return reprocess_with_alternate_model(segment_wav_path, temp_chunks_dir, min_silence_len, silence_thresh)

    # Check for repeated-phrase hallucinations
    flagged, phrase, count = is_hallucination(final_transcript)
    if flagged:
        logger.warning(
            f"Detected repeated phrase '{phrase}' ({count}x) in {segment_wav_path.name}. "
            f"Retrying with gpt-4o-mini-transcribe."
        )
        return reprocess_with_alternate_model(segment_wav_path, temp_chunks_dir, min_silence_len, silence_thresh)

    # Final accepted transcript
    logger.debug(f"Whisper transcript for {segment_wav_path.name!r}: {final_transcript!r}")
    log_transcription_to_console(final_transcript)
    return final_transcript, True   # If it passed hallucination/smell
    return alt_final_transcript, False  # If we had to fall back