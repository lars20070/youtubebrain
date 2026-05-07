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

[[src/youtubebrain/ingest.py#load_watch_history]] reads the Takeout `watch-history.json` array from the repo-root-relative path, validates it with a pydantic `TypeAdapter[list[WatchedVideo]]`, and applies [[ingest#Unresolved title filtering]] before returning. Running the module prints each title to stdout.

## Unresolved title filtering

[[src/youtubebrain/ingest.py#_is_unresolved]] flags records where Takeout failed to resolve the canonical video title and fell back to embedding the URL in the `title` field. `load_watch_history` silently drops these entries.

Detection rule: a record is unresolved iff `title == f"Watched {titleUrl}"`. In the raw export these records additionally have no `subtitles` field, but the title equality is the precise signal — `subtitles` can also be missing for legitimate edge cases.

**Motivation.** When Google Takeout assembles the watch-history export, it joins each viewing event back to its video metadata. If the video has been deleted, made private, region-blocked, copyright-removed, or its channel terminated, the join fails and Google substitutes the URL itself — yielding records like `"title": "Watched https://www.youtube.com/watch?v=…"` with no channel info.

Such entries carry no useful signal for downstream search, summarisation or topic analysis: both title and channel are missing, leaving only an opaque video ID. They cannot be embedded into a semantic index in any meaningful way and would only pollute results with placeholder URL strings. The user has no realistic recovery path either — by the time the export is generated, the source video is already gone or inaccessible.

Filtering at ingest is therefore preferred over carrying the entries downstream and filtering per-consumer. The drop is silent because the count is expected to be non-trivial in any long watch history (channel churn, copyright takedowns, privacy changes accumulate over years) and surfacing each one would be noise, not signal. If a future need arises to audit which videos were dropped, `_is_unresolved` can be reused directly against the unfiltered `TypeAdapter` output.

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

### Filters unresolved titles

`load_watch_history` drops records whose `title` equals `Watched <titleUrl>`, leaving only resolvable videos in the returned list.

### main skips unresolved titles

`main()` does not print URL-shaped placeholder titles — only resolvable video titles reach stdout.

### Default path constant

`WATCH_HISTORY_PATH` equals the repo-root-relative Takeout export path.
