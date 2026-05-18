# Deployment Notes — May 2026

Changes on branch `claude/agitated-dirac-2d955a`. Merge to `main` when ready.

---

## What changed

### New files
| File | Purpose |
|------|---------|
| `src/transcribe_google.py` | Google Cloud Speech-to-Text V2 provider (Chirp 2/Chirp 3) |
| `src/unit_extractor.py` | Deterministic fire/EMS unit extraction — no LLM involvement |
| `src/transcript_normalizer.py` | Fixes systematic STT errors before anything else sees the text |
| `analyze_vocab.py` | One-off analysis script — run locally, not deployed |

### Modified files
| File | What changed |
|------|-------------|
| `src/config.py` | Added `GOOGLE_STT_MODEL`, `GOOGLE_STT_LOCATION`, `GOOGLE_STT_TIMEOUT_SEC`; registered `google` as a valid `TRANSCRIPTION_PROVIDER` |
| `src/transcribe.py` | Wired in Google provider path |
| `src/routes.py` | `/recent_incidents` now validates LLM-returned units against deterministic extractor — drops any units the model invented |
| `src/main.py` | Calls `normalize_transcript()` on every transcript between STT and filter |

---

## 1. Transcript normalizer (no config required — active immediately)

The normalizer runs on every transcript automatically after merging. No environment variables needed.

**What it fixes:**
- **Split addresses** — dispatchers say "fourteen-three-hundred" and the STT writes `1-4300 Detroit` or `1 4300 Detroit`. Normalizer rejoins these to `14300 Detroit`. Affects the 14000s and 18000s heavily (Westerly, Lakewood Cliffs, Lakewood Center).
- **Spoken unit numbers** — `medic one` → `Medic 1`, `engine two` → `Engine 2`
- **Facility names** — `Lakewood List` / `lastly` / `last delay` → `Westerly`; `Lakewood Cliff` → `Lakewood Cliffs`; `O Neill` → `O'Neill`
- **10-codes** — `10 4` / `104` → `10-4`

No action required. Just deploy.

---

## 2. Unit extractor (no config required — active immediately)

The `/recent_incidents` endpoint now cross-checks every unit the LLM returns against units deterministically extracted from the raw transcripts. If the LLM invents a unit that wasn't explicitly present in a dispatch context, it's silently dropped and logged as a warning.

Known Lakewood fire/EMS roster encoded in `src/unit_extractor.py`:

```
Engine 1, Engine 2, Engine 3
Medic 1, Medic 2, Medic 3, Medic 4
Truck 1, Truck 2
Car 2, Car 4, Car 8, Car 10
Rescue 1, Battalion 1
```

To add a unit (e.g. if apparatus changes): edit `_UNIT_REGISTRY` in `src/unit_extractor.py`.

No action required. Just deploy.

---

## 3. Google Cloud Speech-to-Text V2 (optional — Deepgram/OpenAI unchanged)

The existing Deepgram and OpenAI providers are **completely unchanged**. Google STT is a third option you can switch to by changing one environment variable. Nothing breaks if you never set it up.

### Prerequisites

**a) Install `google-auth`**

```bash
pip install google-auth
```

Add to `requirements.txt`:
```
google-auth>=2.29.0
```

**b) GCP credentials**

The provider uses Application Default Credentials. On the server, one of:

```bash
# Option 1 — service account key file
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json

# Option 2 — workload identity / attached service account (GCE/Cloud Run)
# No env var needed if the VM has the right service account attached

# Option 3 — developer machine only
gcloud auth application-default login
```

The service account needs the **Cloud Speech-to-Text User** IAM role (`roles/speech.user`).

**c) GCP project ID**

```bash
export GCP_PROJECT_ID=your-gcp-project-id
```

(Already likely set if you're using `google-cloud-secret-manager`.)

**d) Enable the API** in your GCP project if not already on:
```
https://console.cloud.google.com/apis/library/speech.googleapis.com
```

### Environment variables

| Variable | Default | Notes |
|----------|---------|-------|
| `TRANSCRIPTION_PROVIDER` | `openai` | Set to `google` to activate |
| `GOOGLE_STT_MODEL` | `chirp_2` | Use `chirp_2` (GA). Try `chirp_3` if available in your region. |
| `GOOGLE_STT_LOCATION` | `us-central1` | Chirp 2 is available here. |
| `GOOGLE_STT_TIMEOUT_SEC` | `60` | Per-chunk HTTP timeout |
| `GCP_PROJECT_ID` | *(from ADC)* | Required if ADC can't resolve a project |

### To activate

```bash
export TRANSCRIPTION_PROVIDER=google
export GCP_PROJECT_ID=your-project-id
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json
```

Then restart the service.

### Phrase adaptation

Vocabulary boosts are configured inline in `src/transcribe_google.py` in `LAKEWOOD_PHRASE_SETS`. No GCP resource creation needed — uses the wildcard `_` recognizer. Boost values:

| Category | Boost | Examples |
|----------|-------|---------|
| Apparatus | 15 | Engine 2, Medic 1, Car 10 |
| Facilities | 12 | Lakewood Center West, O'Neill |
| Streets | 8 | Bunts, Hilliard, Wagar, Marlowe |
| Dispatch phrases | 6 | lift assist, en route, code 3 |

To add a street or phrase: edit the relevant list in `LAKEWOOD_PHRASE_SETS`.

### Model notes

- `chirp_2` is GA and supports phrase adaptation. Recommended starting point.
- `chirp_3` — if available in `us-central1`, set `GOOGLE_STT_MODEL=chirp_3`. Check availability: https://cloud.google.com/speech-to-text/v2/docs/speech-to-text-supported-languages
- The pipeline is **file-based** (chunks of silence-split WAV), not streaming, so batch `recognize` is used. Streaming would require a larger architectural change.

---

## 4. Vocab analyzer (local use only — not deployed)

`analyze_vocab.py` is a local analysis tool. Run it any time you want a fresh look at what's in the DB:

```bash
cd /path/to/scanew
python3 analyze_vocab.py --db transcriptions.db --out vocab_analysis --min-count 5
```

Output goes to `vocab_analysis/`. Open in Finder with **⌘⇧G** → paste path. All `.tsv` files open in Numbers with columns split.

| File | Use for |
|------|---------|
| `suggested_phrases.txt` | Start here — curated list to fold into keyterms / phrase boosts |
| `units.tsv` | Verify apparatus roster; UNKNOWN entries are hallucinations |
| `addresses.tsv` | `valid` / `intersection-117th` / `suspect` context column |
| `trigrams.tsv` | Search for a term to see all phrase contexts around it |
| `summary.txt` | Quick overview without opening a spreadsheet |

---

## Rollback

All changes are additive. To roll back individual pieces:

- **Normalizer only**: remove the `normalize_transcript()` call from `src/main.py` (one line)
- **Unit extractor guardrail**: remove the validation block in `routes.py` `recent_incidents()` (marked with comment)
- **Google provider**: set `TRANSCRIPTION_PROVIDER` back to `deepgram` or `openai` — the new code path is never entered
