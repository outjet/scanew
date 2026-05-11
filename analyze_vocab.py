#!/usr/bin/env python3
"""
Vocabulary analyzer for transcriptions.db.

Extracts n-gram frequencies, flags likely mis-transcriptions, and emits
a ranked phrase list you can review and fold into deepgram_keyterms.txt
or the Google STT phrase sets.

Usage:
    python3 analyze_vocab.py [--db PATH] [--out DIR] [--min-count N]

Output files (written to --out, default ./vocab_analysis/):
    unigrams.tsv          single words, ranked by frequency
    bigrams.tsv           word pairs
    trigrams.tsv          three-word phrases
    fourgrams.tsv         four-word phrases
    addresses.tsv         candidate addresses (number + street)
    units.tsv             apparatus mentions
    suspect_words.tsv     words that look like mis-transcriptions
    summary.txt           human-readable highlights
"""

import argparse
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Known-good vocabulary — used to flag deviations as suspect
# ---------------------------------------------------------------------------

KNOWN_STREETS = {
    "abbieshire", "adeline", "alameda", "alger", "andrews", "archdale",
    "arden", "arlington", "arliss", "armin", "arthur", "athens", "atkins",
    "baldwin", "baxterly", "bayes", "beach", "belle", "blossom",
    "bonnieview", "bramley", "brockley", "brown", "bunts",
    "cannon", "captains", "carabel", "cedarwood", "chase", "chesterland",
    "clarence", "cliff", "cliffdale", "clifton", "cohassett", "concord",
    "cook", "cordova", "coutant", "cove", "cranford", "crest",
    "daleview", "davis", "delaware", "detroit", "donald", "dowd",
    "edanola", "edgewater", "edwards", "elbur", "eldred", "elmwood",
    "emerson", "emily", "erie", "esther", "estill", "ethel",
    "ferndale", "fischer", "forest", "franklin", "french", "fries", "fry",
    "garfield", "giel", "gladys", "glenbury", "graber", "grace",
    "granger", "gridley",
    "hall", "halstead", "harlon", "hathaway", "hazelwood", "highland",
    "hilda", "hilliard", "hird", "homewood", "hopkins",
    "idlewood", "indianola",
    "jackson", "kenilworth", "kenneth", "kirtland",
    "lake", "lakeland", "lakewood", "lane", "lanning", "larchmont",
    "lark", "lauderdale", "leedale", "leonard", "lewis", "lincoln",
    "madison", "magee", "maile", "manor", "maplecliff", "margaret",
    "marlowe", "mars", "mathews", "mckinley", "mcclure", "merl",
    "morrison",
    "narragansett", "nelson", "newman", "niagara", "nicholson",
    "northland", "northwood", "norton",
    "ogontz", "olive", "olivewood", "onondaga", "orchard", "overbrook",
    "overlook", "owego",
    "park", "parkhaven", "parkside", "parkway", "parkwood", "phelps",
    "plover",
    "quail",
    "ramona", "reveley", "richland", "ridgewood", "rio", "riverside",
    "riverway", "robin", "robinwood", "rockcliff", "rockway", "roosevelt",
    "rosalie", "rose", "rosewood", "roy", "roycroft",
    "saint", "charles", "scenic", "seneca", "shaw", "sloane", "spring",
    "summit", "sylvan",
    "thoreau", "thrush",
    "victoria", "virginia",
    "wagar", "warren", "wascana", "waterbury", "wayne", "webb",
    "westlake", "westwood", "whippoorwill", "wilbert", "williamson",
    "winchester", "winton", "woodford", "woodward", "wyandotte",
    # common suffixes / cross-reference words kept for tokenizer context
    "avenue", "road", "drive", "boulevard", "place", "lane", "court",
    "street", "pkwy", "exn",
}

KNOWN_UNITS = {
    # Engines
    "engine 1", "engine 2", "engine 3",
    # Medics
    "medic 1", "medic 2", "medic 3", "medic 4",
    # Trucks
    "truck 1", "truck 2",
    # Cars (Fire command/supervisor vehicles)
    "car 2", "car 4", "car 8", "car 10",
    # Other
    "rescue 1", "battalion 1",
}

# Lakewood PD patrol unit numbers are 3-digit 200-series.
# "211 Traffic" = unit 211 calling in a traffic stop — NOT an address.
POLICE_UNIT_PATTERN = re.compile(r"^2[0-2]\d$")  # 200–229

# "117" and "117th" = West 117th Street, a major cross-street.
# "117 Detroit" means the intersection of W 117th & Detroit.
INTERSECTION_MARKER = re.compile(r"^117(th)?$", re.IGNORECASE)

KNOWN_FACILITIES = {
    "lakewood center west", "lakewood park", "o'neill", "o neil",
    "cleveland clinic", "grace lutheran", "aldi", "aldi's",
    "speedway", "days inn",
}

# Words that look numeric/phonetic and usually indicate garbled transcription
# when appearing alone (common Whisper/Deepgram artefacts on scanner audio).
NOISE_TOKENS = re.compile(
    r"^(\d{1,2}|\d{4,}|[a-z]{1,2}|\buh+\b|\bum+\b|yeah|okay|ok|alright|mm)$"
)

# Lakewood address ranges (Detroit goes into the 18900s near the Rocky River border)
VALID_ADDR_RANGES = [(1000, 2500), (11700, 19000)]

def _in_valid_range(num: int) -> bool:
    return any(lo <= num <= hi for lo, hi in VALID_ADDR_RANGES)

def _classify_address_context(num_str: str, following_word: str) -> str:
    """
    Return a context label for a number + following word combination.
    Helps distinguish addresses from police codes and intersection markers.
    """
    try:
        num = int(num_str)
    except ValueError:
        return "unknown"

    # Police units calling in (e.g. "211 Traffic", "215 Copy")
    if POLICE_UNIT_PATTERN.match(num_str):
        return "police-unit-callout"

    # Intersection references ("117 Madison", "117 Detroit")
    if INTERSECTION_MARKER.match(num_str):
        return "intersection-117th"

    if _in_valid_range(num):
        return "valid"
    return "suspect"

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    """Lowercase, collapse whitespace, strip leading/trailing punctuation."""
    text = text.lower()
    text = re.sub(r"[^\w\s'\-]", " ", text)   # keep apostrophes and hyphens
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def _tokens(text: str) -> list[str]:
    return _clean(text).split()

def _ngrams(tokens: list[str], n: int):
    return zip(*[tokens[i:] for i in range(n)])

# ---------------------------------------------------------------------------
# Address extractor
# ---------------------------------------------------------------------------

_ADDR_RE = re.compile(
    r"\b(\d{3,5})\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
)

def _extract_addresses(raw: str) -> list[tuple[str, str]]:
    results = []
    for m in _ADDR_RE.finditer(raw):
        num_str = m.group(1)
        street = m.group(2).strip()
        # Skip if it looks like a police unit callout or LEADS description
        following = street.split()[0].lower() if street else ""
        ctx = _classify_address_context(num_str, following)
        if ctx in ("police-unit-callout",):
            continue
        results.append((num_str, street))
    return results

# ---------------------------------------------------------------------------
# Unit extractor (loose — to catch variant spellings)
# ---------------------------------------------------------------------------

_LOOSE_UNIT_RE = re.compile(
    r"\b(engine|medic|truck|car|rescue|battalion)\s*(\d+|one|two|three)\b",
    re.IGNORECASE,
)

_WORD_NUMS = {"one": "1", "two": "2", "three": "3"}

def _extract_units_loose(raw: str) -> list[str]:
    results = []
    for m in _LOOSE_UNIT_RE.finditer(raw):
        apparatus = m.group(1).lower()
        num_raw = m.group(2).lower()
        num = _WORD_NUMS.get(num_raw, num_raw)
        results.append(f"{apparatus.title()} {num}")
    return results

# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyze(db_path: str, out_dir: Path, min_count: int):
    out_dir.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT transcript FROM transcriptions WHERE transcript IS NOT NULL").fetchall()
    con.close()

    transcripts = [r[0].strip() for r in rows if r[0] and r[0].strip()]
    print(f"Loaded {len(transcripts):,} transcripts.", file=sys.stderr)

    uni_c: Counter = Counter()
    bi_c: Counter = Counter()
    tri_c: Counter = Counter()
    four_c: Counter = Counter()
    addr_c: Counter = Counter()
    unit_c: Counter = Counter()

    addr_ctx: dict[str, str] = {}  # address string → context label

    for raw in transcripts:
        toks = _tokens(raw)
        uni_c.update(toks)
        bi_c.update(" ".join(g) for g in _ngrams(toks, 2))
        tri_c.update(" ".join(g) for g in _ngrams(toks, 3))
        four_c.update(" ".join(g) for g in _ngrams(toks, 4))
        for num_str, street in _extract_addresses(raw):
            key = f"{num_str} {street}"
            addr_c.update([key])
            if key not in addr_ctx:
                following = street.split()[0].lower() if street else ""
                addr_ctx[key] = _classify_address_context(num_str, following)
        unit_c.update(_extract_units_loose(raw))

    # --- unigrams ---
    _write_tsv(out_dir / "unigrams.tsv", uni_c, min_count,
               header="word\tcount")

    # --- bigrams ---
    _write_tsv(out_dir / "bigrams.tsv", bi_c, min_count,
               header="bigram\tcount")

    # --- trigrams ---
    _write_tsv(out_dir / "trigrams.tsv", tri_c, min_count,
               header="trigram\tcount")

    # --- fourgrams ---
    _write_tsv(out_dir / "fourgrams.tsv", four_c, max(min_count, 3),
               header="fourgram\tcount")

    # --- addresses ---
    _write_tsv(out_dir / "addresses.tsv", addr_c, 2,
               header="address\tcount\tcontext",
               extra_fn=lambda key: "\t" + addr_ctx.get(key, "unknown"))

    # --- units ---
    _write_tsv(out_dir / "units.tsv", unit_c, 1,
               header="unit\tcount\tcanonical",
               extra_fn=lambda key: "\t" + (key if key.lower() in KNOWN_UNITS else "UNKNOWN"))

    # --- suspect words ---
    _write_suspect(out_dir / "suspect_words.tsv", uni_c, min_count=3)

    # --- street bigrams (number + word) for address validation ---
    _write_street_bigrams(out_dir / "street_mentions.tsv", bi_c, min_count=2)

    # --- suggested phrases to add to your keyterms / phrase sets ---
    _write_suggested_phrases(out_dir / "suggested_phrases.txt", bi_c, tri_c, four_c, unit_c, min_count=min_count)

    # --- summary ---
    _write_summary(
        out_dir / "summary.txt",
        uni_c, bi_c, tri_c, four_c, addr_c, unit_c, addr_ctx,
        total=len(transcripts),
    )

    print(f"\nDone. Results in: {out_dir.resolve()}", file=sys.stderr)


def _write_tsv(path: Path, counter: Counter, min_count: int, header: str,
               extra_fn=None):
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        for key, count in counter.most_common():
            if count < min_count:
                break
            extra = extra_fn(key) if extra_fn else ""
            f.write(f"{key}\t{count}{extra}\n")
    print(f"  wrote {path.name}", file=sys.stderr)


def _write_suspect(path: Path, uni_c: Counter, min_count: int):
    """
    Flag words that look like transcription errors:
    - Short runs of letters that aren't real words (e.g. "obt", "dlp")
    - Numbers that could be garbled addresses
    - Tokens that appear just rarely enough to be one-off artefacts
      but are not known vocabulary
    """
    dispatch_vocab = {
        # radio / procedural
        "dispatch", "copy", "respond", "en", "route", "scene", "service",
        "attention", "all", "companies", "code", "disregard", "traffic",
        "county", "mileage", "transfer", "transport", "patient",
        "10-4", "10-7", "10-8", "10-16", "lpd", "lfd",
        # common English
        "the", "a", "an", "and", "or", "for", "to", "at", "in", "on",
        "of", "is", "it", "he", "she", "they", "we", "i", "with", "out",
        "that", "this", "there", "here", "will", "be", "no", "not",
        "go", "get", "can", "you", "your", "his", "her", "who", "from",
        "just", "up", "down", "north", "south", "east", "west", "sir",
        # dispatch specific
        "male", "female", "complainant", "caller", "vehicle", "car",
        "truck", "black", "white", "blue", "red", "silver", "gray",
        "wearing", "plate", "wants", "clear", "number", "room", "bed",
        "floor", "outside", "inside", "front", "back", "building",
        "apartment", "address", "street", "road", "avenue", "drive",
        "lane", "court", "place", "way",
        # numbers as words
        "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "hundred", "thousand",
    } | KNOWN_STREETS

    with open(path, "w", encoding="utf-8") as f:
        f.write("word\tcount\tnote\n")
        for word, count in uni_c.most_common():
            if count < min_count:
                break
            if word in dispatch_vocab:
                continue
            note = _classify_suspect(word)
            if note:
                f.write(f"{word}\t{count}\t{note}\n")
    print(f"  wrote {path.name}", file=sys.stderr)


def _classify_suspect(word: str) -> str:
    """Return a note string if the word looks suspicious, else ''."""
    # Pure digit string
    if re.fullmatch(r"\d+", word):
        try:
            n = int(word)
            if not _in_valid_range(n) and n > 100:
                return "numeric-out-of-range"
        except ValueError:
            pass
        return ""

    # Very short alphabetic — likely initialism or artefact
    if re.fullmatch(r"[a-z]{1,3}", word) and word not in {"lpd", "lfd", "obt", "mva", "vin", "rls"}:
        return "short-token"

    # Looks like a garbled phonetic alphabet or radio code
    if re.fullmatch(r"[a-z]+-[a-z]+", word) and len(word) > 8:
        return "possible-hyphenated-artefact"

    # Mixed alpha-numeric oddities
    if re.search(r"[a-z]\d|\d[a-z]", word) and not re.fullmatch(r"\d+-\d+", word):
        return "alphanumeric-oddity"

    return ""


def _write_street_bigrams(path: Path, bi_c: Counter, min_count: int):
    """Extract bigrams where first token is a plausible house number."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("mention\tcount\tvalid_range\n")
        for phrase, count in bi_c.most_common():
            if count < min_count:
                break
            parts = phrase.split()
            if len(parts) == 2 and re.fullmatch(r"\d{3,5}", parts[0]):
                n = int(parts[0])
                valid = "YES" if _in_valid_range(n) else "SUSPECT"
                f.write(f"{phrase}\t{count}\t{valid}\n")
    print(f"  wrote {path.name}", file=sys.stderr)


def _write_suggested_phrases(
    path: Path, bi_c: Counter, tri_c: Counter, four_c: Counter, unit_c: Counter,
    min_count: int
):
    """
    Produce a curated list of phrases worth adding to your STT phrase sets
    or Deepgram keyterms file. Groups by category with counts so you can
    decide what to include.
    """
    # Already-known terms we don't need to suggest
    already_covered = {
        "medic 1", "medic 2", "medic 3", "medic 4",
        "engine 1", "engine 2", "engine 3",
        "truck 1", "truck 2", "car 2", "car 4", "car 8", "car 10",
        "rescue 1", "battalion 1",
        "detroit", "madison", "warren", "bunts", "hilliard", "clifton",
        "edgewater", "franklin", "lakewood center west", "lakewood park",
        "o'neill", "dispatch", "respond", "en route", "on scene",
        "in service", "code 3", "10-4", "10-8", "10-7", "10-16",
        "disregard", "lpd", "lfd", "mva", "fire alarm", "lift assist",
        "complainant", "go ahead", "copy",
    }

    # Dispatch/status phrases worth boosting
    dispatch_keywords = {
        "year old male", "year old female", "back in service",
        "valid no wants", "in front of", "en route to", "is en route",
        "date of birth", "are you all set", "i'll be out",
        "attention all companies", "respond to the", "dispatch medic",
        "dispatch from medic", "dispatch from car", "dispatch from engine",
        "dispatch from truck", "is on scene", "be 10-8", "no wants",
        "out of cleveland", "i'll be 10-8", "we'll be 10-8",
        "back in the city", "back in service", "out of the city",
        "can you check", "you can disregard", "i'm all set",
        "copy thank you", "we're all set",
        # fire-specific
        "structure fire", "working fire", "second alarm",
        "all companies", "respond to",
        # medical
        "unconscious person", "chest pain", "difficulty breathing",
        "fall injury", "lift assist",
    }

    lines = [
        "# Suggested additions to deepgram_keyterms.txt / Google STT phrase sets",
        "# Generated by analyze_vocab.py — review before adding.",
        "# Format: phrase  [count in DB]",
        "",
    ]

    # --- Fire/EMS unit variants found in the data ---
    lines.append("## Fire/EMS units (from DB — add any missing to phrase sets)")
    for unit, count in unit_c.most_common():
        if unit.lower() in KNOWN_UNITS and count >= 2:
            lines.append(f"{unit}  [{count}]")
    lines.append("")

    # --- High-frequency bigrams not yet in keyterms ---
    lines.append("## High-frequency bigrams (dispatch vocabulary)")
    for phrase, count in bi_c.most_common(200):
        if count < min_count:
            break
        if phrase in already_covered:
            continue
        # Only keep phrases that look like dispatch content, not filler
        words = phrase.split()
        if all(len(w) <= 2 for w in words):
            continue
        if phrase in dispatch_keywords or any(kw in phrase for kw in (
            "medic", "engine", "truck", "car 2", "car 4", "car 8", "car 10",
            "dispatch", "respond", "route", "scene", "service", "attention",
            "10-", "code", "alarm", "fire", "assist",
        )):
            lines.append(f"{phrase}  [{count}]")
    lines.append("")

    # --- High-frequency trigrams ---
    lines.append("## High-frequency trigrams (dispatch phrases)")
    for phrase, count in tri_c.most_common(300):
        if count < min_count:
            break
        if phrase in already_covered:
            continue
        if any(kw in phrase for kw in (
            "medic", "engine", "truck", "dispatch", "respond", "en route",
            "on scene", "in service", "attention", "10-", "code", "alarm",
            "year old", "no wants", "valid no", "back in service",
            "date of birth", "all companies", "all set",
        )):
            lines.append(f"{phrase}  [{count}]")
    lines.append("")

    # --- Four-grams ---
    lines.append("## Four-grams (longer dispatch formulas)")
    for phrase, count in four_c.most_common(100):
        if count < max(min_count, 3):
            break
        if any(kw in phrase for kw in (
            "medic", "engine", "truck", "dispatch", "all companies",
            "year old male", "year old female", "valid no wants",
            "back in service", "en route to",
        )):
            lines.append(f"{phrase}  [{count}]")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  wrote {path.name}", file=sys.stderr)


def _write_summary(path: Path, uni_c, bi_c, tri_c, four_c, addr_c, unit_c, addr_ctx,
                   total: int):
    lines = []
    lines.append(f"=== Vocabulary Analysis Summary ===\n")
    lines.append(f"Total transcripts analyzed: {total:,}\n")
    lines.append(f"Unique unigrams: {len(uni_c):,}\n")
    lines.append(f"Unique bigrams:  {len(bi_c):,}\n")
    lines.append(f"Unique trigrams: {len(tri_c):,}\n\n")

    lines.append("--- Top 30 unigrams (excluding stop words) ---\n")
    stop = {"the","a","an","and","or","for","to","at","in","on","of","is",
            "it","he","she","they","we","i","with","out","that","this",
            "there","here","will","be","no","not","go","get","can","you",
            "your","from","up","down","just","go","ahead","copy","s"}
    shown = 0
    for w, c in uni_c.most_common(500):
        if w not in stop and not re.fullmatch(r"\d+", w):
            lines.append(f"  {c:>6}  {w}\n")
            shown += 1
            if shown >= 30:
                break

    lines.append("\n--- Top 30 bigrams ---\n")
    for phrase, count in bi_c.most_common(30):
        lines.append(f"  {count:>6}  {phrase}\n")

    lines.append("\n--- Top 20 trigrams ---\n")
    for phrase, count in tri_c.most_common(20):
        lines.append(f"  {count:>6}  {phrase}\n")

    lines.append("\n--- Top 10 four-grams ---\n")
    for phrase, count in four_c.most_common(10):
        lines.append(f"  {count:>6}  {phrase}\n")

    lines.append("\n--- Unit mentions ---\n")
    for unit, count in unit_c.most_common():
        if count < 5:
            break
        canon = "✓" if unit.lower() in KNOWN_UNITS else "UNKNOWN — likely hallucination/bleed"
        lines.append(f"  {count:>6}  {unit}  [{canon}]\n")

    lines.append("\n--- Most common addresses (valid range) ---\n")
    shown = 0
    for addr, count in addr_c.most_common(500):
        ctx = addr_ctx.get(addr, "unknown")
        if ctx == "valid":
            lines.append(f"  {count:>6}  {addr}\n")
            shown += 1
            if shown >= 25:
                break

    lines.append("\n--- Intersection references (117th & something) ---\n")
    for addr, count in addr_c.most_common(500):
        ctx = addr_ctx.get(addr, "unknown")
        if ctx == "intersection-117th":
            lines.append(f"  {count:>6}  {addr}\n")

    lines.append("\n--- Addresses outside Lakewood ranges (worth checking) ---\n")
    shown = 0
    for addr, count in addr_c.most_common(500):
        ctx = addr_ctx.get(addr, "unknown")
        if ctx == "suspect":
            lines.append(f"  {count:>6}  {addr}   <-- check\n")
            shown += 1
            if shown >= 20:
                break

    path.write_text("".join(lines), encoding="utf-8")
    print(f"  wrote {path.name}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="transcriptions.db",
                    help="Path to transcriptions.db (default: ./transcriptions.db)")
    ap.add_argument("--out", default="vocab_analysis",
                    help="Output directory (default: ./vocab_analysis/)")
    ap.add_argument("--min-count", type=int, default=5,
                    help="Minimum occurrence count to include in n-gram files (default: 5)")
    args = ap.parse_args()

    analyze(
        db_path=args.db,
        out_dir=Path(args.out),
        min_count=args.min_count,
    )


if __name__ == "__main__":
    main()
