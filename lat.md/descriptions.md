---
lat:
  require-code-mention: true
---
# Descriptions

Fetches YouTube descriptions into a resumable SQLite cache so markdown compilation can read them offline without API calls.

Google Takeout exports include title and channel metadata but not long-form video descriptions, so this stage fills that gap via the YouTube Data API.

## CLI entry

[[src/youtubebrain/descriptions.py#main]] is the `uv run descriptions` entrypoint.

It removes legacy JSON cache files, loads ids from [[takeout#Video IDs]], enqueues rows, then runs the fetch loop.

## SQLite schema

[[src/youtubebrain/descriptions.py#init_db]] creates a `descriptions` status-row table through [[cache#Schema initialization]].

Rows use statuses `pending`, `ok`, `missing`, and `error`; `missing` is API-confirmed absent and terminal, while `error` is retryable until attempt cap.

## Enqueue

[[src/youtubebrain/descriptions.py#enqueue]] delegates to [[cache#Enqueue]] and inserts pending rows for new ids only.

Repeated runs are resumable because existing primary keys are not overwritten.

## Read API

[[src/youtubebrain/descriptions.py#load_descriptions]] wraps [[src/youtubebrain/cache.py#StatusCache#load_ok]] and returns `{video_id: text_or_none}`.

Missing databases and non-ok rows map to `None`, which markdown renders as `_(unavailable)_` in the Description section.

## Fetch loop

[[src/youtubebrain/descriptions.py#fetch_descriptions]] selects retryable ids via [[src/youtubebrain/cache.py#StatusCache#pending_ids]], batches in groups of 50, and writes outcomes with [[src/youtubebrain/cache.py#StatusCache#record_result]] / [[src/youtubebrain/cache.py#StatusCache#record_attempt]].

The API key is only read when there are pending/error rows, so fully settled caches can be read without credentials.

## API client

[[src/youtubebrain/descriptions.py#_fetch_batch]] uses a synchronous `httpx.Client` against `https://www.googleapis.com/youtube/v3/videos` with `part=snippet` and up to 50 ids per request.

It returns only ids present in the API response, with empty-string descriptions preserved as valid `ok` values.

## Missing videos

IDs omitted from a successful API response are marked `missing` by [[src/youtubebrain/descriptions.py#fetch_descriptions]].

This captures deleted/private/region-blocked videos as terminal `None` values rather than retrying forever.

## API failures

Per-batch `httpx.HTTPError` failures are recorded as `error` attempts and processing continues with later batches.

Rows in `error` status remain retryable while attempts stay below the cap.

## API key requirement

[[src/youtubebrain/descriptions.py#_get_api_key]] reads `API_KEY_YOUTUBE` after [[src/youtubebrain/config.py#load_env]] and raises a clear `RuntimeError` when missing.

The error text points to Google Cloud setup and `.env` configuration.

## Tests

Coverage in `tests/test_descriptions.py` verifies read API behavior, batching, retry semantics, and the CLI wiring/legacy-cleanup path.

### Read API missing db

`load_descriptions` returns a complete id-to-`None` map when the SQLite file is absent.

### Read API ok only

`load_descriptions` returns text only for `ok` rows and maps non-ok rows to `None`.

### Batches in fifties

75 queued ids produce exactly two API calls, confirming 50-id batch chunking.

### Missing rows become missing status

Ids absent from the API response are persisted as `missing` and read back as `None`.

### Persists per batch

If a later batch fails, earlier successful rows remain committed while failed rows become `error`.

### Error rows retryable

Rows marked `error` are retried on later runs and can transition to `ok` with incremented attempts.

### API key only when pending

Missing `API_KEY_YOUTUBE` raises only when pending/error rows exist; settled caches skip key lookup.

### Main wiring and legacy cleanup

`main()` removes legacy JSON files, enqueues Takeout ids, and invokes the fetch loop.
