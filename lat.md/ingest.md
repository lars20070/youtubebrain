---
lat:
  require-code-mention: true
---
# Ingest

Loads YouTube watch history from a Google Takeout export and stores it for downstream analysis and search.

## WatchedVideo model

Pydantic model in [[src/youtubebrain/models.py#WatchedVideo]] mirroring one record from the Google Takeout `watch-history.json` array.

Snake_case Python fields are aliased to the camelCase JSON keys. `title_url` and `subtitles` are optional because deleted or orphaned videos lack one or both. Unknown fields raise to surface schema drift.
