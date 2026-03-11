# src/db.py
import sqlite3
import logging

from config import SQLITE_DB_PATH

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcriptions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    wav_filename  TEXT,
    transcript    TEXT NOT NULL,
    notified      INTEGER DEFAULT 0,
    pushover_code INTEGER,
    response_code INTEGER,
    alert         BOOLEAN DEFAULT 0,
    bestof        BOOLEAN DEFAULT 0,
    initialdispatch BOOLEAN DEFAULT 0,
    class         TINYINT,
    classification_model TEXT,
    classification_prompt_tokens INTEGER,
    classification_candidate_tokens INTEGER,
    classification_total_tokens INTEGER
);
"""

EXPECTED_COLUMNS = {
    "timestamp": "TEXT NOT NULL",
    "wav_filename": "TEXT",
    "transcript": "TEXT NOT NULL",
    "notified": "INTEGER DEFAULT 0",
    "pushover_code": "INTEGER",
    "response_code": "INTEGER",
    "alert": "BOOLEAN DEFAULT 0",
    "bestof": "BOOLEAN DEFAULT 0",
    "initialdispatch": "BOOLEAN DEFAULT 0",
    "class": "TINYINT",
    "classification_model": "TEXT",
    "classification_prompt_tokens": "INTEGER",
    "classification_candidate_tokens": "INTEGER",
    "classification_total_tokens": "INTEGER",
}


def _get_connection() -> sqlite3.Connection:
    return sqlite3.connect(str(SQLITE_DB_PATH))


def _migrate_transcriptions_table(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(transcriptions)")
    existing_columns = {row[1] for row in cur.fetchall()}

    for column_name, column_def in EXPECTED_COLUMNS.items():
        if column_name in existing_columns:
            continue
        cur.execute(f'ALTER TABLE transcriptions ADD COLUMN "{column_name}" {column_def}')
        logger.info("Added missing transcriptions column: %s", column_name)

def initialize_database():
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(SCHEMA)
        _migrate_transcriptions_table(conn)
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
    pushover_code: int = None,
    response_code: int = None,
    alert: bool = False,
    bestof: bool = False,
):
    """
    Inserts one row into the transcriptions table.
    """
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO transcriptions
            (timestamp, wav_filename, transcript, notified, pushover_code, response_code, alert, bestof)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp_iso,
                wav_filename,
                transcript,
                int(notified),
                pushover_code,
                response_code,
                int(alert),
                int(bestof),
            )
        )
        conn.commit()
        row_id = cur.lastrowid
        logger.debug(f"Inserted transcription row: {timestamp_iso}, {wav_filename}")
    except Exception as e:
        logger.error(f"Error inserting into DB: {e}")
        raise
    finally:
        conn.close()
    return row_id


def update_transcription_classification(row_id: int, class_code: int):
    conn = _get_connection()
    try:
        conn.execute(
            'UPDATE transcriptions SET "class" = ?, initialdispatch = ? WHERE id = ?',
            (class_code, int(class_code == 1), row_id),
        )
        conn.commit()
    except Exception as e:
        logger.error("Error updating classification for row %s: %s", row_id, e)
        raise
    finally:
        conn.close()


def update_transcription_classification_usage(
    row_id: int,
    *,
    model: str | None,
    prompt_tokens: int | None,
    candidate_tokens: int | None,
    total_tokens: int | None,
):
    conn = _get_connection()
    try:
        conn.execute(
            """
            UPDATE transcriptions
            SET classification_model = ?,
                classification_prompt_tokens = ?,
                classification_candidate_tokens = ?,
                classification_total_tokens = ?
            WHERE id = ?
            """,
            (model, prompt_tokens, candidate_tokens, total_tokens, row_id),
        )
        conn.commit()
    except Exception as e:
        logger.error("Error updating classification usage for row %s: %s", row_id, e)
        raise
    finally:
        conn.close()


def update_transcription_response_code(row_id: int, response_code: int):
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE transcriptions SET response_code = ? WHERE id = ?",
            (response_code, row_id),
        )
        conn.commit()
    except Exception as e:
        logger.error("Error updating response code for row %s: %s", row_id, e)
        raise
    finally:
        conn.close()
