# src/db.py
import sqlite3
import logging
from pathlib import Path

from config import SQLITE_DB_PATH

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcriptions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    wav_filename  TEXT,
    transcript    TEXT NOT NULL,
    notified      INTEGER DEFAULT 0,
    pushover_code INTEGER
);
"""

def initialize_database():
    conn = sqlite3.connect(str(SQLITE_DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute(SCHEMA)
        conn.commit()
        logger.info(f"Initialized or verified DB at {SQLITE_DB_PATH}")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise
    finally:
        conn.close()

def insert_transcription(
    timestamp_iso: str,
    wav_filename: str,
    transcript: str,
    notified: bool = False,
    pushover_code: int = None
):
    """
    Inserts one row into the transcriptions table.
    """
    conn = sqlite3.connect(str(SQLITE_DB_PATH))  # ← this must return a connection object only
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO transcriptions
            (timestamp, wav_filename, transcript, notified, pushover_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (timestamp_iso, wav_filename, transcript, int(notified), pushover_code)
        )
        conn.commit()
        logger.debug(f"Inserted transcription row: {timestamp_iso}, {wav_filename}")
    except Exception as e:
        logger.error(f"Error inserting into DB: {e}")
        raise
    finally:
        conn.close()
