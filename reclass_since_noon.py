#!/usr/bin/env python3
"""
Reclassify transcriptions with class IS NULL since noon ET (16:00 UTC) today.
Run on the GCP VM from /opt/scanew:
    python3 reclass_since_noon.py
"""

import sys
import time
import sqlite3
from pathlib import Path

# Allow running from /opt/scanew as a top-level script
sys.path.insert(0, str(Path(__file__).parent))

from src.config import SQLITE_DB_PATH
from src.utils import classify_transcript_intent_with_metadata
from src.db import update_transcription_classification

SINCE_UTC = "2026-05-25T16:00:00"
MIN_LEN = 50

def main():
    con = sqlite3.connect(SQLITE_DB_PATH)
    cur = con.execute(
        """
        SELECT id, transcript FROM transcriptions
        WHERE timestamp >= ?
          AND length(transcript) > ?
          AND class IS NULL
        ORDER BY id
        """,
        (SINCE_UTC, MIN_LEN),
    )
    rows = cur.fetchall()
    con.close()

    total = len(rows)
    if total == 0:
        print("Nothing to reclassify.")
        return

    print(f"Reclassifying {total} rows since {SINCE_UTC} UTC (len > {MIN_LEN}) ...")

    ok = skipped = errors = 0
    for i, (row_id, transcript) in enumerate(rows, 1):
        try:
            class_code, _ = classify_transcript_intent_with_metadata(transcript)
            update_transcription_classification(row_id, class_code)
            label = {0: "other", 1: "DISPATCH", 2: "alert"}.get(class_code, str(class_code))
            print(f"  [{i}/{total}] #{row_id} → {label}  "{transcript[:60]}"")
            ok += 1
        except Exception as e:
            print(f"  [{i}/{total}] #{row_id} ERROR: {e}")
            errors += 1
        time.sleep(0.15)   # stay well under rate limits

    print(f"\nDone. {ok} classified, {errors} errors, {skipped} skipped.")

if __name__ == "__main__":
    main()
