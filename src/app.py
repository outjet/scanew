from flask import Flask, jsonify, request, render_template, send_from_directory
import sqlite3
from config import SQLITE_DB_PATH
import threading
import json
import os
from openai import OpenAI
from openai._exceptions import OpenAIError
from utils import retry_on_exception
from transcribe import transcribe_chunk
from pathlib import Path
import tempfile

# New OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app = Flask(__name__)

# Serve static files (wav files)
@app.route('/wav/<filename>')
def serve_wav(filename):
    directory = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'recordings'))
    return send_from_directory(directory, filename)

# Index page
@app.route('/')
def index():
    # serve the main HTML page
    return render_template('index.html')

# List all transcripts
@app.route('/api/transcripts')
def list_transcripts():
    conn = get_db_connection()
    transcripts = conn.execute('SELECT * FROM transcriptions ORDER BY id DESC').fetchall()
    conn.close()
    result = [dict(row) for row in transcripts]
    return jsonify(result)

# Get individual transcript
@app.route('/api/transcript/<int:transcript_id>')
def get_transcript(transcript_id):
    conn = get_db_connection()
    row = conn.execute('SELECT * FROM transcriptions WHERE id = ?', (transcript_id,)).fetchone()
    conn.close()
    if row:
        return jsonify(dict(row))
    else:
        return jsonify({'error': 'Not found'}), 404

# Serve WAV audio
@app.route('/api/wav/<int:transcript_id>')
def get_wav(transcript_id):
    conn = get_db_connection()
    row = conn.execute('SELECT wav_filename FROM transcriptions WHERE id = ?', (transcript_id,)).fetchone()
    conn.close()
    if row:
        filename = row['wav_filename']
        return serve_wav(filename)
    else:
        return jsonify({'error': 'File not found'}), 404

# Trigger re-transcription with GPT-4
@app.route('/api/retry/<int:transcript_id>', methods=['POST'])
def retry_transcript(transcript_id):
    conn = get_db_connection()
    row = conn.execute('SELECT * FROM transcriptions WHERE id = ?', (transcript_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Transcript not found'}), 404

    wav_filename = row['wav_filename']
    wav_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'recordings', wav_filename))
    try:
        with open(wav_path, 'rb') as f:
            audio_data = f.read()

        revised_text = run_retry_transcription(audio_data)

        conn.execute('UPDATE transcriptions SET transcript = ? WHERE id = ?', (revised_text, transcript_id))
        conn.commit()
        conn.close()
        return jsonify({'transcript': revised_text})
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500

@retry_on_exception(exceptions=(OpenAIError,), max_attempts=3, initial_delay=1, backoff_factor=2)
def run_retry_transcription(audio_data: bytes) -> str:
    """Transcribe raw audio bytes using whisper-1 via transcribe_chunk."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_data)
        tmp_path = Path(tmp.name)

    try:
        text = transcribe_chunk(
            tmp_path,
            model="whisper-1",
            use_prompt=True,
        )
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    return text.strip()

# Helper to get DB connection

def get_db_connection():
    conn = sqlite3.connect(str(SQLITE_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5500, debug=True)
