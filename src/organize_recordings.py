import argparse
import datetime as dt
import re
import sqlite3
from pathlib import Path

try:
    from .config import RECORDINGS_DIR, SQLITE_DB_PATH
except ImportError:  # pragma: no cover - allows running as a top-level script module
    from config import RECORDINGS_DIR, SQLITE_DB_PATH


FILENAME_DATE_RE = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})_")


def _extract_day_from_name(filename: str) -> str | None:
    match = FILENAME_DATE_RE.match(Path(filename).name)
    if not match:
        return None
    return match.group("day")


def _infer_day_for_file(path: Path) -> str:
    day = _extract_day_from_name(path.name)
    if day:
        return day
    modified = dt.datetime.fromtimestamp(path.stat().st_mtime)
    return modified.strftime("%Y-%m-%d")


def _iter_top_level_wavs(recordings_dir: Path):
    for path in recordings_dir.iterdir():
        if path.is_file() and path.suffix.lower() == ".wav":
            yield path


def organize_recordings_by_day(*, apply: bool = False) -> tuple[int, int]:
    """
    Moves top-level WAV files into YYYY-MM-DD subfolders and updates wav_filename
    in the SQLite DB so rows continue to point to the moved files.
    """
    recordings_dir = RECORDINGS_DIR
    db_path = SQLITE_DB_PATH

    if not recordings_dir.is_dir():
        raise FileNotFoundError(f"Directory not found: {recordings_dir}")
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    planned_moves: list[tuple[Path, Path, str, str]] = []
    for source in _iter_top_level_wavs(recordings_dir):
        day = _infer_day_for_file(source)
        relative_target = Path(day) / source.name
        target = recordings_dir / relative_target
        if source == target:
            continue
        planned_moves.append((source, target, source.name, relative_target.as_posix()))

    if not planned_moves:
        print("No top-level WAV files need migration.")
        return 0, 0

    print(f"Planned file moves: {len(planned_moves)}")
    for source, target, old_name, new_name in planned_moves[:20]:
        print(f"{old_name} -> {new_name}")
    if len(planned_moves) > 20:
        print(f"... and {len(planned_moves) - 20} more")

    if not apply:
        print("Dry run only. Re-run with --apply to move files and update the DB.")
        return len(planned_moves), 0

    conn = sqlite3.connect(str(db_path))
    try:
        updated_rows = 0
        with conn:
            for source, target, old_name, new_name in planned_moves:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise FileExistsError(f"Refusing to overwrite existing file: {target}")
                source.rename(target)
                cursor = conn.execute(
                    """
                    UPDATE transcriptions
                    SET wav_filename = ?
                    WHERE wav_filename = ?
                    """,
                    (new_name, old_name),
                )
                updated_rows += cursor.rowcount
        print(f"Moved {len(planned_moves)} files.")
        print(f"Updated {updated_rows} transcription rows.")
        return len(planned_moves), updated_rows
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Move top-level recordings into YYYY-MM-DD folders and update wav_filename paths in SQLite."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply file moves and DB updates. Without this flag, the script only prints a dry run.",
    )
    args = parser.parse_args()
    organize_recordings_by_day(apply=args.apply)


if __name__ == "__main__":
    main()
