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

# Load prompt text files once (project root)
BASE_DIR = Path(__file__).resolve().parent.parent
SHORT_PROMPT_PATH = BASE_DIR / "promptshort.txt"
BASIC_PROMPT_PATH = BASE_DIR / "promptbasic.txt"

SHORT_PROMPT = SHORT_PROMPT_PATH.read_text().strip()
if BASIC_PROMPT_PATH.exists():
    BASIC_PROMPT = BASIC_PROMPT_PATH.read_text().strip()
else:
    BASIC_PROMPT = ""


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
            return True
    return False


def smells_too_long(text: str, audio_duration_sec: float, wps_threshold: float = 5.5, min_words: int = 20) -> bool:
    word_count = len(text.split())
    if word_count < min_words:
        return False
    words_per_second = word_count / max(audio_duration_sec, 0.1)
    return words_per_second > wps_threshold


def normalize_text(text: str) -> str:
    """Lowercase, remove extra whitespace, and strip punctuation for robust matching."""
    text = text.lower()
    text = re.sub(r"[\s]+", " ", text)  # collapse whitespace
    text = re.sub(r"[\.,;:!?\-\(\)\[\]{}'\"]", "", text)  # remove punctuation
    return text.strip()


def contains_prompt_snippet(text: str, prompt_text: str, char_threshold: int = 24) -> bool:
    """
    Returns True if any substring of length `char_threshold` from the prompt appears in the transcript, after normalization.
    """
    norm_text = normalize_text(text)
    norm_prompt = normalize_text(prompt_text)
    if len(norm_prompt) < char_threshold or len(norm_text) < char_threshold:
        return False
    for i in range(len(norm_prompt) - char_threshold + 1):
        snippet = norm_prompt[i : i + char_threshold]
        if snippet in norm_text:
            return True
    return False


@retry_on_exception(exceptions=(OpenAIError,), max_attempts=3, initial_delay=1, backoff_factor=2)
def transcribe_chunk(
    chunk_path: Path,
    model: str = "whisper-1",
    *,
    use_prompt: bool = True,
) -> str:
    duration = get_audio_duration_seconds(chunk_path)

    prompt_to_use = None
    if use_prompt:
        if model == "gpt-4o-mini-transcribe":
            prompt_to_use = SHORT_PROMPT if duration < 2.0 else BASIC_PROMPT
        else:
            prompt_to_use = SHORT_PROMPT if duration < 2.0 else DISPATCH_PROMPT

    logger.debug(
        f"Transcribing {chunk_path.name} ({duration:.2f}s) with model={model}"
        + (" using prompt" if prompt_to_use else " without prompt")
    )
    with open(chunk_path, "rb") as f:
        kwargs = {
            "model": model,
            "file": f,
            "temperature": 0.1,
        }
        if prompt_to_use:
            kwargs["prompt"] = prompt_to_use
        resp = client.audio.transcriptions.create(**kwargs)
    text = resp.text.strip()
    logger.debug(f"{model} returned: {text!r} for {chunk_path.name}")
    return text


def _alt_transcribe(
    *,
    segment_wav_path: Path,
    temp_chunks_dir: Path,
    use_prompt: bool = True,
) -> str:
    """Helper to transcribe a segment using gpt-4o-mini-transcribe."""
    chunk_files = split_on_silence(
        wav_path=segment_wav_path,
        output_dir=temp_chunks_dir,
        min_silence_len=1000,
        silence_thresh=-40,
    )
    transcripts = []
    for chunk_path in chunk_files:
        try:
            text = transcribe_chunk(
                chunk_path,
                model="gpt-4o-mini-transcribe",
                use_prompt=use_prompt,
            )
            if text:
                transcripts.append(text)
        except Exception as e:
            logger.error(
                f"Failed to transcribe chunk {chunk_path.name} with gpt-4o-mini-transcribe: {e}"
            )
    for c in chunk_files:
        try:
            c.unlink()
        except Exception:
            pass
    final_text = " ".join(transcripts).strip()
    logger.debug(
        f"gpt-4o-mini-transcribe ({'prompt' if use_prompt else 'no prompt'}) result for {segment_wav_path.name!r}: {final_text!r}"
    )
    return final_text


def reprocess_with_alternate_model(
    segment_wav_path: Path,
    temp_chunks_dir: Path,
) -> str:
    final_duration = get_audio_duration_seconds(segment_wav_path)

    # First attempt with prompt
    transcript = _alt_transcribe(
        segment_wav_path=segment_wav_path,
        temp_chunks_dir=temp_chunks_dir,
        use_prompt=True,
    )

    flagged = is_hallucination(transcript)
    if (
        smells_too_long(transcript, final_duration)
        or flagged
        or contains_prompt_snippet(transcript, DISPATCH_PROMPT)
        or contains_prompt_snippet(transcript, SHORT_PROMPT)
        or contains_prompt_snippet(transcript, BASIC_PROMPT)
    ):
        if final_duration < 1.0:
            logger.info(
                f"Alternate model output for {segment_wav_path.name} sounds like the prompt and is under 1s. Skipping."
            )
            return ""

        logger.warning(
            f"Alternate model output for {segment_wav_path.name} triggered heuristics; retrying without prompt.\n"
            f"Original alternate transcript: {transcript!r}"
        )
        transcript = _alt_transcribe(
            segment_wav_path=segment_wav_path,
            temp_chunks_dir=temp_chunks_dir,
            use_prompt=False,
        )
        flagged = is_hallucination(transcript)

    # Drop if transcript still looks like the prompt or fails heuristics
    if (
        contains_prompt_snippet(transcript, DISPATCH_PROMPT)
        or contains_prompt_snippet(transcript, SHORT_PROMPT)
        or contains_prompt_snippet(transcript, BASIC_PROMPT)
        or smells_too_long(transcript, final_duration)
        or flagged
    ):
        logger.warning(
            f"Alternate model output for {segment_wav_path.name} appears invalid after retries."
        )
        return ""

    log_transcription_to_console(transcript)
    return transcript


def transcribe_full_segment(
    segment_wav_path: Path,
    temp_chunks_dir: Path,
) -> Optional[str]:
    temp_chunks_dir.mkdir(parents=True, exist_ok=True)

    # Split the audio into chunks
    chunk_files = split_on_silence(
        wav_path=segment_wav_path,
        output_dir=temp_chunks_dir,
        min_silence_len=1000,
        silence_thresh=-40,
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
            f"for {segment_wav_path.name}. Retrying with gpt-4o-mini-transcribe.\n"
            f"Original transcript: {final_transcript!r}"
        )
        return reprocess_with_alternate_model(segment_wav_path, temp_chunks_dir)

    # Check for repeated-phrase hallucinations
    flagged = is_hallucination(final_transcript)
    if flagged:
        logger.warning(
            f"Detected repeated phrase in {segment_wav_path.name}. "
            f"Retrying with gpt-4o-mini-transcribe.\n"
            f"Original transcript: {final_transcript!r}"
        )
        return reprocess_with_alternate_model(segment_wav_path, temp_chunks_dir)

    
    if (
        contains_prompt_snippet(final_transcript, DISPATCH_PROMPT)
        or contains_prompt_snippet(final_transcript, SHORT_PROMPT)
        or contains_prompt_snippet(final_transcript, BASIC_PROMPT)
    ):
        if final_duration < 1.0:
            logger.info(
                f"Transcript for {segment_wav_path.name} sounds like the prompt and is under 1s. Skipping."
            )
            return None
        
        logger.warning(
            f"Detected at least 24 consecutive chars of the prompt in transcript for {segment_wav_path.name}. "
            f"Retrying with gpt-4o-mini-transcribe.\n"
            f"Original transcript: {final_transcript!r}"
        )
        return reprocess_with_alternate_model(segment_wav_path, temp_chunks_dir)

    # Final accepted transcript
    logger.debug(f"Whisper transcript for {segment_wav_path.name!r}: {final_transcript!r}")
    log_transcription_to_console(final_transcript)
    return final_transcript