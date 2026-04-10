import argparse
import datetime as dt
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

try:
    from .config import RECORDINGS_DIR, SQLITE_DB_PATH
except ImportError:  # pragma: no cover - allows running as a top-level script module
    from config import RECORDINGS_DIR, SQLITE_DB_PATH


DEFAULT_BATCH_SIZE = 1000
DEFAULT_PROGRESS_EVERY = 1000


def _infer_day_for_file(path: Path) -> str:
    stem = path.stem
    if len(stem) >= 10:
        candidate = stem[:10]
        try:
            dt.date.fromisoformat(candidate)
            return candidate
        except ValueError:
            pass
    modified = dt.datetime.fromtimestamp(path.stat().st_mtime)
    return modified.strftime("%Y-%m-%d")


def _iter_top_level_wavs(recordings_dir: Path):
    for path in recordings_dir.iterdir():
        if path.is_file() and path.suffix.lower() == ".wav":
            yield path


def _iter_dated_wavs(recordings_dir: Path):
    for day_dir in recordings_dir.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            dt.date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        for path in day_dir.iterdir():
            if path.is_file() and path.suffix.lower() == ".wav":
                yield path


def _build_moved_file_index(recordings_dir: Path) -> dict[str, str]:
    candidates: dict[str, list[str]] = defaultdict(list)
    for path in _iter_dated_wavs(recordings_dir):
        relative = path.relative_to(recordings_dir).as_posix()
        candidates[path.name].append(relative)

    unique: dict[str, str] = {}
    for basename, matches in candidates.items():
        if len(matches) == 1:
            unique[basename] = matches[0]
    return unique


def _fetch_bare_db_filenames(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.execute(
        """
        SELECT DISTINCT wav_filename
        FROM transcriptions
        WHERE wav_filename IS NOT NULL
          AND wav_filename != ''
          AND instr(wav_filename, '/') = 0
        """
    )
    return [row[0] for row in cursor.fetchall()]


def _chunked(items, size: int):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _print_preview(title: str, pairs: list[tuple[str, str]], *, limit: int = 20):
    print(f"{title}: {len(pairs)}")
    for old_name, new_name in pairs[:limit]:
        print(f"{old_name} -> {new_name}")
    if len(pairs) > limit:
        print(f"... and {len(pairs) - limit} more")


def _reconcile_existing_dated_files(
    conn: sqlite3.Connection,
    recordings_dir: Path,
    *,
    apply: bool,
    batch_size: int,
    progress_every: int,
) -> int:
    moved_index = _build_moved_file_index(recordings_dir)
    if not moved_index:
        print("Reconcile candidates: 0")
        return 0

    bare_names = _fetch_bare_db_filenames(conn)
    reconcile_pairs = [
        (bare_name, moved_index[bare_name])
        for bare_name in bare_names
        if bare_name in moved_index
    ]

    _print_preview("Reconcile candidates", reconcile_pairs)
    if not reconcile_pairs:
        return 0
    if not apply:
        return 0

    updated_rows = 0
    processed = 0
    for chunk in _chunked(reconcile_pairs, batch_size):
        before_changes = conn.total_changes
        with conn:
            conn.executemany(
                """
                UPDATE transcriptions
                SET wav_filename = ?
                WHERE wav_filename = ?
                """,
                [(new_name, old_name) for old_name, new_name in chunk],
            )
        changes = conn.total_changes - before_changes
        updated_rows += changes
        processed += len(chunk)
        if processed % progress_every == 0 or processed == len(reconcile_pairs):
            print(
                f"Reconciled {processed}/{len(reconcile_pairs)} filenames "
                f"across {updated_rows} transcription rows."
            )
    return updated_rows


def _plan_top_level_moves(
    recordings_dir: Path, *, max_files: int | None = None
) -> list[tuple[Path, Path, str, str]]:
    planned_moves: list[tuple[Path, Path, str, str]] = []
    for source in _iter_top_level_wavs(recordings_dir):
        if max_files is not None and len(planned_moves) >= max_files:
            break
        day = _infer_day_for_file(source)
        relative_target = Path(day) / source.name
        target = recordings_dir / relative_target
        if source == target:
            continue
        planned_moves.append((source, target, source.name, relative_target.as_posix()))
    return planned_moves


def _apply_top_level_moves(
    conn: sqlite3.Connection,
    planned_moves: list[tuple[Path, Path, str, str]],
    *,
    batch_size: int,
    progress_every: int,
    sleep_seconds: float,
    max_files: int | None,
) -> tuple[int, int]:
    moved_files = 0
    updated_rows = 0

    for chunk in _chunked(planned_moves, batch_size):
        if max_files is not None and moved_files >= max_files:
            break
        if max_files is not None:
            remaining = max_files - moved_files
            if remaining <= 0:
                break
            chunk = chunk[:remaining]

        before_changes = conn.total_changes
        with conn:
            for source, target, old_name, new_name in chunk:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise FileExistsError(f"Refusing to overwrite existing file: {target}")
                source.rename(target)
                conn.execute(
                    """
                    UPDATE transcriptions
                    SET wav_filename = ?
                    WHERE wav_filename = ?
                    """,
                    (new_name, old_name),
                )
                moved_files += 1
        updated_rows += conn.total_changes - before_changes

        if moved_files % progress_every == 0 or moved_files == len(planned_moves):
            print(
                f"Moved {moved_files}/{len(planned_moves)} files "
                f"and updated {updated_rows} transcription rows."
            )
        if sleep_seconds > 0 and (max_files is None or moved_files < max_files):
            time.sleep(sleep_seconds)

    return moved_files, updated_rows


def organize_recordings_by_day(
    *,
    apply: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    sleep_seconds: float = 0.0,
    max_files: int | None = None,
    skip_reconcile: bool = False,
) -> tuple[int, int, int]:
    """
    Reconciles DB rows for files already in YYYY-MM-DD folders, then moves any
    remaining top-level WAV files into YYYY-MM-DD subfolders and updates
    wav_filename in SQLite to the new relative path.
    """
    recordings_dir = RECORDINGS_DIR
    db_path = SQLITE_DB_PATH

    if not recordings_dir.is_dir():
        raise FileNotFoundError(f"Directory not found: {recordings_dir}")
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if progress_every <= 0:
        raise ValueError("progress_every must be positive")
    if sleep_seconds < 0:
        raise ValueError("sleep_seconds must be non-negative")
    if max_files is not None and max_files <= 0:
        raise ValueError("max_files must be positive when provided")

    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        if skip_reconcile:
            reconciled_rows = 0
        else:
            reconciled_rows = _reconcile_existing_dated_files(
                conn,
                recordings_dir,
                apply=apply,
                batch_size=batch_size,
                progress_every=progress_every,
            )

        planned_moves = _plan_top_level_moves(recordings_dir, max_files=max_files)
        move_pairs = [(old_name, new_name) for _, _, old_name, new_name in planned_moves]
        _print_preview("Planned file moves", move_pairs)

        if not apply:
            print("Dry run only. Re-run with --apply to reconcile paths, move files, and update the DB.")
            return len(planned_moves), 0, 0

        moved_files, updated_rows = _apply_top_level_moves(
            conn,
            planned_moves,
            batch_size=batch_size,
            progress_every=progress_every,
            sleep_seconds=sleep_seconds,
            max_files=max_files,
        )
        print(
            "Done. "
            f"Reconciled {reconciled_rows} rows, moved {moved_files} files, "
            f"and updated {updated_rows} rows during moves."
        )
        return len(planned_moves), reconciled_rows, updated_rows
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Reconcile moved recordings, then move top-level WAV files into YYYY-MM-DD folders and update wav_filename paths in SQLite."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply DB/path reconciliation and file moves. Without this flag, the script only prints a dry run.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"How many filenames to process per transaction. Default: {DEFAULT_BATCH_SIZE}.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help=f"How often to print progress updates. Default: {DEFAULT_PROGRESS_EVERY}.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="How long to sleep after each batch commit. Default: 0.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        help="Optional cap on how many top-level WAV files to move in this run.",
    )
    parser.add_argument(
        "--skip-reconcile",
        action="store_true",
        help=(
            "Skip the reconciliation pass that scans already-dated subdirectories. "
            "Use this for incremental runs once the initial reconciliation is complete."
        ),
    )
    args = parser.parse_args()
    organize_recordings_by_day(
        apply=args.apply,
        batch_size=args.batch_size,
        progress_every=args.progress_every,
        sleep_seconds=args.sleep_seconds,
        max_files=args.max_files,
        skip_reconcile=args.skip_reconcile,
    )


if __name__ == "__main__":
    main()
