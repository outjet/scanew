# src/splitter.py
import logging
from pathlib import Path
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

logger = logging.getLogger(__name__)

def split_on_silence(
    wav_path: Path,
    output_dir: Path,
    min_silence_len: int = 500,
    silence_thresh: int = -50
) -> list[Path]:
    """
    Given a WAV file at wav_path, split it into multiple sub‐WAVs at nonsilent regions.
    Saves each chunk into `output_dir/chunk_##.wav`.
    Returns a list of the new chunk file paths (in time order).
    """
    audio = AudioSegment.from_file(str(wav_path))
    nonsilent_ranges = detect_nonsilent(audio, min_silence_len=min_silence_len, silence_thresh=silence_thresh)

    chunk_files: list[Path] = []
    for idx, (start_ms, end_ms) in enumerate(nonsilent_ranges):
        chunk = audio[start_ms:end_ms]
        chunk_path = output_dir / f"chunk_{idx:05d}.wav"
        chunk.export(str(chunk_path), format="wav")
        chunk_files.append(chunk_path)
        logger.debug(f"Exported chunk: {chunk_path} (ms {start_ms}-{end_ms})")

    return chunk_files
