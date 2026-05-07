---
lat:
  require-code-mention: true
---
# Ingest

Loads YouTube watch history from a Google Takeout export and stores it for downstream analysis and search.

## WatchedVideo model

Pydantic model in [[src/youtubebrain/models.py#WatchedVideo]] mirroring one record from the Google Takeout `watch-history.json` array.

Snake_case Python fields are aliased to the camelCase JSON keys. `title_url` and `subtitles` are optional because deleted or orphaned videos lack one or both. Unknown fields raise to surface schema drift.

## Loader

[[src/youtubebrain/ingest.py#load_watch_history]] reads the Takeout `watch-history.json` array from the repo-root-relative path and validates it with a pydantic `TypeAdapter[list[WatchedVideo]]`. Running the module prints each title to stdout.

## Tests

Loader behaviour is verified by `tests/test_ingest.py` using inline JSON fixtures in `tmp_path`; no dependency on the gitignored `./Takeout/` export.

### Parses valid record

A minimal valid JSON array round-trips to `list[WatchedVideo]` with title, aliased camelCase fields and titleUrl populated.

### Handles empty array

An empty JSON array yields an empty list, with no error.

### Handles optional fields

Records missing titleUrl, subtitles or description still parse — these fields are optional on the model.

### Rejects unknown field

Unknown JSON keys raise ValidationError to surface schema drift, per `extra="forbid"` on WatchedVideo.

### main prints titles

Calling `main()` against a monkeypatched watch-history.json prints every video title to stdout, one per line.

### Default path constant

`WATCH_HISTORY_PATH` equals the repo-root-relative Takeout export path.
