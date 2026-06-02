#!/usr/bin/env python3
import argparse
import csv
import datetime
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

FIELD_KEYWORDS = {
    "language": ["language", "lang", "locale", "lng"],
    "genre": ["genre", "genres", "style", "category", "label"],
    "license": ["license", "licence", "copyright"],
    "id": ["track_id", "trackid", "song_id", "id", "uid"],
    "source": ["source", "dataset", "collection", "url", "path", "file", "filename"],
}

SPLIT_REGEX = re.compile(r"[;|,/&]+")


def normalize(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def parse_list(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def detect_delimiter(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample)
    except csv.Error:
        return csv.get_dialect("excel")


def load_records(path: str, delimiter: str | None) -> list[dict]:
    ext = os.path.splitext(path)[1].lower()
    records: list[dict] = []

    if ext in {".jsonl", ".ndjson"}:
        with open(path, "r", encoding="utf-8") as handle:
            for idx, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                record["__row_number"] = idx
                records.append(record)
        return records

    if ext == ".json":
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            for key in ("records", "data", "items"):
                if key in payload and isinstance(payload[key], list):
                    payload = payload[key]
                    break
        if not isinstance(payload, list):
            raise ValueError("JSON file must contain a list of records or a top-level records/data/items list")
        for idx, record in enumerate(payload, start=1):
            if not isinstance(record, dict):
                raise ValueError("Each JSON record must be an object")
            record["__row_number"] = idx
            records.append(record)
        return records

    with open(path, "r", encoding="utf-8", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        dialect = detect_delimiter(sample)
        if delimiter:
            dialect.delimiter = delimiter
        reader = csv.DictReader(handle, dialect=dialect)
        for idx, row in enumerate(reader, start=1):
            row["__row_number"] = idx
            records.append(row)
    return records


def resolve_field(provided: str | None, fields: list[str], keywords: list[str], required: bool) -> str | None:
    if provided:
        if provided not in fields:
            raise ValueError(f"Field '{provided}' not found in dataset columns")
        return provided

    matches = [field for field in fields if any(keyword in field.lower() for keyword in keywords)]
    if required:
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError("Required field could not be auto-detected; specify it explicitly")
        raise ValueError(f"Multiple possible fields detected: {', '.join(matches)}. Specify one explicitly.")

    return matches[0] if matches else None


def summarize_field(records: list[dict], field: str, limit: int = 10) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for record in records:
        value = record.get(field)
        if value is None or value == "":
            continue
        counter[str(value)] += 1
    return counter.most_common(limit)


def print_inspection(records: list[dict], fields: list[str], language_field: str | None, genre_field: str | None,
                     license_field: str | None) -> None:
    print(f"Total records: {len(records)}")
    print("Available fields:")
    for field in fields:
        print(f"  - {field}")

    if language_field:
        print(f"\nLanguage field: {language_field}")
        for value, count in summarize_field(records, language_field):
            print(f"  {value}: {count}")

    if genre_field:
        print(f"\nGenre field: {genre_field}")
        for value, count in summarize_field(records, genre_field):
            print(f"  {value}: {count}")

    if license_field:
        print(f"\nLicense field: {license_field}")
        for value, count in summarize_field(records, license_field):
            print(f"  {value}: {count}")


def genre_tokens(value: object) -> list[str]:
    if value is None:
        return []
    raw = str(value)
    tokens = [token.strip() for token in SPLIT_REGEX.split(raw) if token.strip()]
    return [normalize(token) for token in tokens if token]


def assign_genre(value: object, targets: list[str], allow_subgenres: bool) -> str | None:
    tokens = genre_tokens(value)
    if not tokens:
        return None
    for target in targets:
        target_norm = normalize(target)
        for token in tokens:
            if token == target_norm:
                return target
            if allow_subgenres and (target_norm in token or token in target_norm):
                return target
    return None


def init_buckets(languages: list[str], genres: list[str]) -> dict[str, dict[str, list[dict]]]:
    return {lang: {genre: [] for genre in genres} for lang in languages}


def init_other_pool(languages: list[str]) -> dict[str, list[dict]]:
    return {lang: [] for lang in languages}


def ensure_parent_dir(path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Select balanced Hindi/Punjabi subsets from GACMIS metadata.")
    parser.add_argument("--input", required=True, help="Path to the dataset metadata file (CSV/TSV/JSON/JSONL)")
    parser.add_argument("--output", default="gacmis_subset.csv", help="Path to write the subset CSV")
    parser.add_argument("--manifest", default="gacmis_subset_manifest.json", help="Path to write the selection manifest JSON")
    parser.add_argument("--languages", default="Hindi,Punjabi", help="Comma-separated language values to include")
    parser.add_argument("--genres", help="Comma-separated list of genres to balance across")
    parser.add_argument("--per-language", type=int, default=150, dest="per_language")
    parser.add_argument("--per-genre", type=int, dest="per_genre")
    parser.add_argument("--language-field")
    parser.add_argument("--genre-field")
    parser.add_argument("--license-field")
    parser.add_argument("--id-field")
    parser.add_argument("--source-field")
    parser.add_argument("--delimiter", help="CSV delimiter override")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fallback", choices=["reduce", "expand_genres", "allow_subgenres"], default="reduce")
    parser.add_argument("--inspect", action="store_true", help="Print available fields and sample counts")
    parser.add_argument("--inspect-only", action="store_true", help="Inspect and exit without writing outputs")
    parser.add_argument("--dry-run", action="store_true", help="Run selection without writing outputs")

    args = parser.parse_args()

    records = load_records(args.input, args.delimiter)
    if not records:
        raise ValueError("No records found in input dataset")

    fields = list(records[0].keys())

    language_field = resolve_field(args.language_field, fields, FIELD_KEYWORDS["language"], required=True)
    genre_field = resolve_field(args.genre_field, fields, FIELD_KEYWORDS["genre"], required=True)
    license_field = resolve_field(args.license_field, fields, FIELD_KEYWORDS["license"], required=False)
    id_field = resolve_field(args.id_field, fields, FIELD_KEYWORDS["id"], required=False)
    source_field = resolve_field(args.source_field, fields, FIELD_KEYWORDS["source"], required=False)

    if args.inspect:
        print_inspection(records, fields, language_field, genre_field, license_field)
        if args.inspect_only:
            return 0

    languages = parse_list(args.languages)
    if not languages:
        raise ValueError("At least one language must be specified")

    target_genres = parse_list(args.genres) if args.genres else []
    allow_subgenres = args.fallback == "allow_subgenres"

    if not target_genres:
        genre_counter: Counter[str] = Counter()
        for record in records:
            if normalize(record.get(language_field)) not in {normalize(lang) for lang in languages}:
                continue
            genre_value = record.get(genre_field)
            if genre_value:
                genre_counter[str(genre_value)] += 1
        if len(genre_counter) < 3:
            raise ValueError("Not enough genres found to auto-select three; pass --genres explicitly")
        target_genres = [genre for genre, _ in genre_counter.most_common(3)]

    genre_count = len(target_genres)
    per_genre = args.per_genre
    per_language = args.per_language
    if per_genre is None:
        if per_language % genre_count != 0:
            raise ValueError("per-language must be divisible by number of genres or specify --per-genre")
        per_genre = per_language // genre_count
    else:
        per_language = per_genre * genre_count

    rng = random.Random(args.seed)
    buckets = init_buckets(languages, target_genres)
    other_pool = init_other_pool(languages)
    language_set = {normalize(lang): lang for lang in languages}

    for record in records:
        language_value = normalize(record.get(language_field))
        if language_value not in language_set:
            continue
        language_label = language_set[language_value]
        matched_genre = assign_genre(record.get(genre_field), target_genres, allow_subgenres)
        if matched_genre:
            buckets[language_label][matched_genre].append(record)
        else:
            if record.get(genre_field):
                other_pool[language_label].append(record)

    notes: list[str] = []
    if args.fallback in {"reduce", "allow_subgenres"}:
        available_min = min(
            len(buckets[lang][genre]) for lang in languages for genre in target_genres
        )
        if available_min < per_genre:
            notes.append(
                f"Reduced per-genre target from {per_genre} to {available_min} due to limited data."
            )
            per_genre = available_min
            per_language = per_genre * genre_count

    selected_records: list[dict] = []
    selection_counts: dict[str, dict[str, int]] = {lang: {} for lang in languages}

    for lang in languages:
        language_selected: list[dict] = []
        for genre in target_genres:
            bucket = buckets[lang][genre]
            rng.shuffle(bucket)
            take_count = min(per_genre, len(bucket))
            selection_counts[lang][genre] = take_count
            language_selected.extend(bucket[:take_count])
            if take_count < per_genre and args.fallback == "expand_genres":
                notes.append(
                    f"Genre '{genre}' for language '{lang}' short by {per_genre - take_count}; expanded selection."
                )

        if args.fallback == "expand_genres":
            remaining_needed = per_language - len(language_selected)
            if remaining_needed > 0:
                pool = other_pool[lang][:]
                rng.shuffle(pool)
                language_selected.extend(pool[:remaining_needed])
                if len(pool) < remaining_needed:
                    notes.append(
                        f"Language '{lang}' has only {len(language_selected)} total records available after expansion."
                    )
        else:
            if len(language_selected) < per_language:
                notes.append(
                    f"Language '{lang}' has only {len(language_selected)} records after balancing; total reduced."
                )

        selected_records.extend(language_selected)

    if not selected_records:
        raise ValueError("No records selected; check language/genre filters")

    output_fields = ["track_id", "language", "genre", "source"]
    if license_field:
        output_fields.append("license")

    rows: list[dict] = []
    for record in selected_records:
        track_id = record.get(id_field) if id_field else None
        if not track_id:
            track_id = record.get("__row_number")
        language_value = record.get(language_field)
        genre_value = record.get(genre_field)
        source_value = record.get(source_field) if source_field else None
        row = {
            "track_id": track_id,
            "language": language_value,
            "genre": genre_value,
            "source": source_value or "",
        }
        if license_field:
            row["license"] = record.get(license_field) or ""
        rows.append(row)

    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    manifest = {
        "created_at": created_at,
        "input": os.path.abspath(args.input),
        "output": os.path.abspath(args.output),
        "manifest": os.path.abspath(args.manifest),
        "language_field": language_field,
        "genre_field": genre_field,
        "license_field": license_field,
        "id_field": id_field,
        "source_field": source_field,
        "languages": languages,
        "genres": target_genres,
        "per_language_target": per_language,
        "per_genre_target": per_genre,
        "fallback": args.fallback,
        "seed": args.seed,
        "selection_counts": selection_counts,
        "total_selected": len(rows),
        "notes": notes,
        "selected": rows,
    }

    if args.dry_run:
        print(json.dumps(manifest, indent=2))
        return 0

    ensure_parent_dir(args.output)
    ensure_parent_dir(args.manifest)

    with open(args.output, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(rows)

    with open(args.manifest, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)

    print(f"Wrote {len(rows)} records to {args.output}")
    print(f"Manifest saved to {args.manifest}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
