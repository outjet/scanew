# Dispatch Transcriber

A Python-based system that:

* Opens a Broadcastify Emergency Dispatch stream in Chrome
* Captures system audio (via a loopback or “Stereo Mix” device) in real time
* Splits each detected speech segment into shorter WAV files whenever silence is detected
* Uses OpenAI’s Whisper to transcribe each chunk (with automatic retries)
* Filters out low-value transcripts (numeric-only, profanity, gibberish)
* Saves valid transcripts into a local SQLite database
* Checks each transcript against high-priority regex patterns (from `alert_patterns.txt`)
* Sends a Pushover push notification whenever a transcript matches one of those patterns

Transcripts that do not match any high-priority pattern are still stored locally, but no push notifications are sent.

To start the full pipeline, run:

```bash
python src/main.py
```

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [File Structure](#file-structure)
3. [Environment Variables](#environment-variables)
4. [Installation](#installation)
5. [Usage](#usage)
6. [How It Works](#how-it-works)
7. [Alert Patterns](#alert-patterns)
8. [Troubleshooting](#troubleshooting)
9. [Customization](#customization)

---

## Prerequisites

* **Python 3.8+**
* **pip** (to install dependencies)
* **Chrome** (latest stable version)
* **ChromeDriver** (auto-downloaded via `webdriver-manager`)
* **ffmpeg** installed and in PATH (required by `pydub`)
* A virtual audio loopback device:

  * **Windows:** Enable “Stereo Mix” and set it as your default recording device.
  * **macOS:** Install BlackHole and create a Multi-Output Device.
  * **Linux:** Use PulseAudio or JACK.
* **OpenAI API key** (for Whisper transcription)
* (Optional) **Pushover** account + credentials for push notifications

To list available audio devices:

```bash
python src/testaudio.py
```

---

## File Structure

```
dispatch_transcriber/
├── alert_patterns.txt         # Regex patterns for high-priority alerts
├── filtered_words.txt         # Optional list of profanity/unwanted words
├── prompt.txt                 # Optional Whisper prompt for dispatch style
├── recordings/                # Final validated WAVs are saved here
├── .env.example               # Example environment variables
├── requirements.txt           # pip dependencies
├── README.md                  # (This file)
└── src/
    ├── main.py                # Entry point
    ├── config.py              # Loads env vars and constants
    ├── broadcaster.py         # Selenium logic
    ├── audio.py               # PyAudio + VAD logic
    ├── splitter.py            # Splits WAVs on silence
    ├── transcribe.py          # Whisper transcription
    ├── filters.py             # Filters profanity, gibberish, etc
    ├── db.py                  # SQLite DB logic
    ├── notifier.py            # Pushover logic
    └── utils.py               # Logging, retry, etc
```

---

## Environment Variables

Create a `.env` file (or export these manually). Copy from `.env.example`.

```ini
# Whisper
OPENAI_API_KEY=sk-xxx

# Broadcastify feed URL
BROADCASTIFY_URL=https://www.broadcastify.com/listen/feed/12345/thumbnail

# Pushover (optional)
PUSHOVER_TOKEN=your_pushover_app_token
PUSHOVER_USER=your_pushover_user_key

# Voice Activity Detection
THRESHOLD_DB=-50
LOOKBACK_MS=1000
MIN_SILENCE_LEN=500

# Audio Input Device
INPUT_DEVICE_INDEX=-1

# File paths
DB_PATH=transcriptions.db
RECORDINGS_DIR=recordings
PROMPT_FILE=prompt.txt
FILTERED_WORDS_FILE=filtered_words.txt

# Selenium
CHROMEDRIVER_PATH=/usr/local/bin/chromedriver
PLAY_BUTTON_SELECTOR=button.playpause
```

---

## Installation

```bash
git clone ...  # or copy the repo
cd dispatch_transcriber
pip install -r requirements.txt
cp .env.example .env  # then edit your API key, Broadcastify URL, etc.
```

---

## Usage

Start the system:

```bash
python src/main.py
```

You should see logs in:

* The console
* `dispatch_transcriber.log`

**What it does:**

1. Opens Chrome and plays your Broadcastify stream
2. Captures audio from your loopback input
3. Splits audio on silence
4. Transcribes chunks using Whisper
5. Filters unimportant results
6. Saves valid transcripts and WAVs
7. Sends push alerts if a transcript matches an alert pattern

---

## How It Works

### 1. Threads

* **Thread 1:** Broadcastify stream monitor (via Selenium)
* **Thread 2:** Audio recorder (via PyAudio)
* **Main thread:** Handles transcription, filtering, DB insert, and optional push

### 2. Audio Capture & VAD

* Records PCM audio from `INPUT_DEVICE_INDEX`
* When audio exceeds `THRESHOLD_DB`, it begins recording
* Stops recording after sustained silence (≥ `MIN_SILENCE_LEN` ms)

### 3. Splitting & Transcription

* Splits segments on additional silence
* Sends chunks to Whisper API (`model=whisper-1`)
* Retries up to 3x on failure
* Concatenates final transcript

### 4. Filtering

* Drops profanity/ads based on `filtered_words.txt`
* Drops numeric-only or gibberish-like segments

### 5. Saving + Notifying

* Saves validated transcript + WAV
* Pushes alert if transcript matches `alert_patterns.txt` (cooldown: 10 min)

---

## Alert Patterns

Edit `alert_patterns.txt`. Each line is a Python regex (case-insensitive).

```txt
# Priority phrases
\bpursuit\b
\bcode\s3\b
\bstep\sit\sup\b

# Locations
\b1597\b
\bwagar\b
\b1602\slewis\b
```

Lines starting with `#` or blank lines are ignored.

---

## Troubleshooting

**No audio captured?**

* Run `src/testaudio.py` to confirm device index
* Set correct `INPUT_DEVICE_INDEX` in `.env`
* Ensure loopback is set as default recording device

**Playback doesn’t start?**

* Manually inspect Broadcastify and confirm the play button CSS selector
* Update `PLAY_BUTTON_SELECTOR` in `.env` if needed

**Whisper errors or rate limits?**

* Ensure `OPENAI_API_KEY` is valid and has quota
* Code retries 3x but may fail if throttled too often

**No Pushover alert?**

* Set both `PUSHOVER_TOKEN` and `PUSHOVER_USER`
* Check regex in `alert_patterns.txt`

**Database locked?**

* If interrupted mid-write, remove `transcriptions.db-journal`

**Silent drops?**

* Review logs for gibberish/profanity filtering messages
* Adjust thresholds in `filters.py` if needed

---

## Customization

* **Tweak VAD settings:** Adjust `THRESHOLD_DB`, `LOOKBACK_MS`, `MIN_SILENCE_LEN`
* **Change regexes:** Edit `alert_patterns.txt`
* **Disable push:** Leave `PUSHOVER_TOKEN` or `PUSHOVER_USER` blank
* **Log more:** Modify log level by editing `main.py` or running with `LOGLEVEL=DEBUG`
* **Run headless:** Default is headless Chrome; comment `--headless=new` in `broadcaster.py` to show the browser

---

## Final Notes

To verify the system end-to-end:

1. Play a test WAV or audio stream into your loopback
2. Confirm WAVs get created in `recordings/`
3. Confirm transcripts are accurate
4. Confirm alerts are sent when regex matches

You can run this permanently using `tmux`, `screen`, or as a `systemd` service.

Enjoy your intelligent, regex-aware emergency transcription and alerting system.
