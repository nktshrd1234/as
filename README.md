# GACMIS Subset Tooling

This repository includes a small utility to inspect GACMIS metadata and build a balanced Hindi/Punjabi subset across three genres.

## Prerequisites

- Python 3.10+ (required for PEP 604 union type hints used in the script)

## Inspect the dataset fields

```bash
python gacmis_subset.py \
  --input /path/to/gacmis_metadata.csv \
  --inspect --inspect-only
```

This prints available fields and sample counts so you can confirm which columns hold language, genre, and license data.

## Create a balanced subset

```bash
python gacmis_subset.py \
  --input /path/to/gacmis_metadata.csv \
  --language-field language \
  --genre-field genre \
  --license-field license \
  --id-field track_id \
  --source-field source \
  --languages Hindi,Punjabi \
  --genres Pop,Rock,Folk \
  --per-language 150 \
  --fallback reduce
```

Outputs:

- `gacmis_subset.csv` containing `track_id`, `language`, `genre`, `source`, and `license` (when provided)
- `gacmis_subset_manifest.json` with the selection parameters and chosen records for reproducibility

## Fallback strategies

- `reduce` (default): reduce per-genre counts to the minimum available so every language/genre bucket stays balanced.
- `allow_subgenres`: match subgenres by substring (e.g., `Bollywood Pop` for `Pop`) and then reduce if still short.
- `expand_genres`: fill shortfalls with additional genres outside the selected three.
