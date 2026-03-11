import argparse
import json
import os
import random
import sqlite3
import sys
import time
from collections import Counter

import requests


CLASSIFICATION_PROMPT = """Classify this Lakewood, Ohio police/fire dispatch transcript.

Return JSON with exactly one field:
{"class_code":"0"|"1"|"2"}

"1" = initial dispatch for a new incident, usually includes a location and a request for police or fire service or a reported problem
"2" = supplemental traffic for an existing incident, including status updates, transport, 10-codes, unit chatter, follow-up details, or descriptions after the dispatch
"0" = other radio noise, radio checks, administrative traffic, accidental audio, or non-dispatch content

Transcript:
"""

DEFAULT_MODELS = [
    "gemini-3-flash",
    "gemini-3.1-flash-lite-preview",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare Gemini classification models against labeled dispatch transcripts."
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("DB_PATH", "transcriptions.db"),
        help="Path to the SQLite transcriptions DB. Defaults to DB_PATH env var or transcriptions.db.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=100,
        help="Number of labeled transcripts to sample.",
    )
    parser.add_argument(
        "--min-text-length",
        type=int,
        default=int(os.getenv("CLASSIFICATION_MIN_TEXT_LENGTH", "50")),
        help="Minimum transcript length to include in the benchmark sample.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed used when sampling transcripts.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("VERTEX_API_KEY", "").strip(),
        help="Gemini API key. Defaults to VERTEX_API_KEY env var.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("VERTEX_CLASSIFICATION_TIMEOUT_SEC", "8")),
        help="Request timeout in seconds.",
    )
    parser.add_argument(
        "--pause-ms",
        type=int,
        default=0,
        help="Optional pause between requests in milliseconds.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path to write the full comparison results as JSON.",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        help="Gemini models to compare. Defaults to gemini-3-flash and gemini-3.1-flash-lite-preview.",
    )
    return parser.parse_args()


def fetch_sample_rows(db_path: str, sample_size: int, min_text_length: int, seed: int) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, timestamp, wav_filename, transcript, class
            FROM transcriptions
            WHERE class IS NOT NULL
              AND LENGTH(COALESCE(transcript, '')) >= ?
            """,
            (min_text_length,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        raise RuntimeError("No labeled transcripts found in the database matching the filter.")

    rng = random.Random(seed)
    sampled_rows = rows if len(rows) <= sample_size else rng.sample(rows, sample_size)
    return [dict(row) for row in sampled_rows]


def classify_with_model(text: str, model: str, api_key: str, timeout: float) -> tuple[int, dict]:
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": f"{CLASSIFICATION_PROMPT}{text}"
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "topP": 0.1,
            "topK": 1,
            "maxOutputTokens": 32,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "class_code": {
                        "type": "STRING",
                        "enum": ["0", "1", "2"],
                    }
                },
                "required": ["class_code"],
            },
        },
    }

    started = time.perf_counter()
    response = requests.post(
        endpoint,
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 1)

    if not response.ok:
        raise requests.HTTPError(
            f"Model {model} failed: status={response.status_code} body={response.text[:1000]}",
            response=response,
        )

    data = response.json()
    raw_text = extract_vertex_text(data)
    if not raw_text:
        raise ValueError(f"Model {model} returned no text. raw_response={json.dumps(data)[:1000]}")

    parsed = json.loads(raw_text)
    class_code = parsed.get("class_code")
    if isinstance(class_code, str) and class_code in {"0", "1", "2"}:
        class_code = int(class_code)
    if class_code not in {0, 1, 2}:
        raise ValueError(f"Model {model} returned invalid class_code={class_code!r} raw_payload={raw_text!r}")

    return class_code, {"latency_ms": latency_ms, "raw_text": raw_text}


def extract_vertex_text(data: dict) -> str:
    for candidate in data.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            text = (part.get("text") or "").strip()
            if text:
                return text
    return ""


def summarize_model(model_name: str, results: list[dict]) -> dict:
    completed = [row for row in results if row["status"] == "ok"]
    errors = [row for row in results if row["status"] != "ok"]
    correct = [row for row in completed if row["predicted_class"] == row["expected_class"]]
    confusion = Counter((row["expected_class"], row["predicted_class"]) for row in completed)
    latency_values = [row["latency_ms"] for row in completed]

    return {
        "model": model_name,
        "completed": len(completed),
        "errors": len(errors),
        "accuracy": (len(correct) / len(completed)) if completed else 0.0,
        "avg_latency_ms": (sum(latency_values) / len(latency_values)) if latency_values else 0.0,
        "confusion": {
            f"{expected}->{predicted}": count
            for (expected, predicted), count in sorted(confusion.items())
        },
    }


def print_summary(sample_count: int, summaries: list[dict]):
    print(f"Sampled {sample_count} labeled transcripts.")
    for summary in summaries:
        print(
            f"{summary['model']}: accuracy={summary['accuracy']:.1%} "
            f"completed={summary['completed']} errors={summary['errors']} "
            f"avg_latency_ms={summary['avg_latency_ms']:.1f}"
        )
        if summary["confusion"]:
            print(f"  confusion={json.dumps(summary['confusion'], sort_keys=True)}")


def main() -> int:
    args = parse_args()
    if not args.api_key:
        print("Missing Gemini API key. Set VERTEX_API_KEY or pass --api-key.", file=sys.stderr)
        return 2

    rows = fetch_sample_rows(
        db_path=args.db_path,
        sample_size=args.sample_size,
        min_text_length=args.min_text_length,
        seed=args.seed,
    )

    all_results: dict[str, list[dict]] = {}
    for model in args.models:
        model_results: list[dict] = []
        print(f"Running {model} on {len(rows)} transcripts...", file=sys.stderr)
        for index, row in enumerate(rows, start=1):
            result = {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "wav_filename": row["wav_filename"],
                "expected_class": row["class"],
            }
            try:
                predicted_class, metadata = classify_with_model(
                    text=row["transcript"],
                    model=model,
                    api_key=args.api_key,
                    timeout=args.timeout,
                )
                result.update(
                    {
                        "status": "ok",
                        "predicted_class": predicted_class,
                        "latency_ms": metadata["latency_ms"],
                        "raw_text": metadata["raw_text"],
                    }
                )
            except Exception as exc:
                result.update(
                    {
                        "status": "error",
                        "error": str(exc),
                    }
                )

            model_results.append(result)
            if args.pause_ms:
                time.sleep(args.pause_ms / 1000)
            if index % 10 == 0 or index == len(rows):
                print(f"  {model}: {index}/{len(rows)} complete", file=sys.stderr)

        all_results[model] = model_results

    summaries = [summarize_model(model, results) for model, results in all_results.items()]
    print_summary(len(rows), summaries)

    if args.output_json:
        output_payload = {
            "sample_size": len(rows),
            "seed": args.seed,
            "db_path": args.db_path,
            "summaries": summaries,
            "results": all_results,
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(output_payload, f, indent=2)
        print(f"Wrote JSON results to {args.output_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
