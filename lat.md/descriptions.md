---
lat:
  require-code-mention: true
---
# Descriptions

Fetches YouTube video descriptions via the YouTube Data API v3 and caches them on disk so re-runs do not re-fetch.

Google Takeout exports do not contain video descriptions, only titles and channel references. [[src/youtubebrain/descriptions.py#fetch_descriptions]] takes a list of video IDs (extracted from Takeout titleUrls by [[ingest#Video ID extraction]]) and returns a `{video_id: description_or_None}` mapping that [[ingest#Markdown writer]] folds into each markdown file under a `## Description` heading.

## API client

[[src/youtubebrain/descriptions.py#fetch_descriptions]] is the only public entry point. It deduplicates the input IDs, splits the cache misses into batches of 50 (the API maximum), and calls [[src/youtubebrain/descriptions.py#_fetch_batch]] for each batch via an `httpx.AsyncClient` with a 30-second timeout.

The API endpoint is `https://www.googleapis.com/youtube/v3/videos` with `part=snippet&id=<csv>&key=<API_KEY_YOUTUBE>`. Cost is 1 quota unit per call, so a 10 K-video history burns ~200 of the free 10 K daily units. Items whose ID does not appear in the response are deleted, private, or region-blocked; they are still recorded in the cache as `null` so they are not re-fetched on subsequent runs.

## Cache

A JSON object keyed by video ID with description (or `null`) as the value, persisted at `Markdown/.cache/descriptions.json`.

[[src/youtubebrain/descriptions.py#DESCRIPTIONS_CACHE_PATH]] points at the file. [[src/youtubebrain/descriptions.py#_load_cache]] returns an empty dict when it is missing; [[src/youtubebrain/descriptions.py#_save_cache]] pretty-prints with sorted keys for diff-friendliness and creates the parent directory on demand.

The cache is written after every batch, so a partial run that fails on batch N still preserves batches 1 through N-1 — restarting the pipeline picks up from where it stopped. The folder is gitignored.

## Missing videos

Videos the API cannot return are cached as `null` and surfaced to the caller as `None`.

Causes: deleted, private, age-restricted, region-blocked, or terminated-channel videos. The downstream markdown renderer maps `None` to the `_(unavailable)_` placeholder under the Description heading so the file is still written with the Takeout metadata that survived.

A yt-dlp fallback for these videos is intentionally deferred — for a 10 K-video personal archive the API alone covers 85–95 % of records, and yt-dlp adds bot-detection risk and a heavyweight dependency for marginal gain.

## API failures

HTTP errors (4xx/5xx) and network failures (timeouts, connect errors) from `_fetch_batch` are caught per batch in [[src/youtubebrain/descriptions.py#fetch_descriptions]] and logged at warning level. The failed batch's IDs are **not** written to the cache, so a subsequent run retries them.

The caller still receives a complete `{video_id: description_or_None}` mapping — IDs from failed batches surface as `None` because `cache.get(vid)` returns `None` when the key is absent. Downstream [[ingest#Markdown writer]] therefore writes `_(unavailable)_` rather than crashing the run.

This is distinct from [[descriptions#Missing videos]]: a cached `null` means "API confirmed the video does not exist" and is never retried; an absent cache entry after a failed call means "we could not ask the API this run" and will retry next time.

## API key requirement

[[src/youtubebrain/descriptions.py#_get_api_key]] reads `API_KEY_YOUTUBE` from the environment after `load_dotenv()`, raising `RuntimeError` if unset.

The error message points at the Google Cloud Console and the `.env` file. The check happens only when at least one ID is uncached, so a fully cached re-run does not require a key in scope.

## Tests

Behaviour is verified by `tests/test_descriptions.py` using `respx` to mock the YouTube API endpoint and `tmp_path` for the cache file. The autouse `_set_api_key` fixture sets `API_KEY_YOUTUBE=test-key` so each test starts from a known environment.

### Uses cache first

A pre-seeded cache file means no HTTP request is issued for that video ID; the respx route is asserted not-called.

### Batches in fifties

75 uncached IDs trigger exactly two GET calls against the YouTube videos endpoint, confirming the 50-per-batch chunking.

### Stores None for missing

When the API returns only a subset of the requested IDs, the missing ones are written to the cache as `null` and returned to the caller as `None`.

### Persists cache per batch

If a later batch errors, the cache file still contains every ID from earlier successful batches — the fetcher writes after each batch, so restarts resume rather than re-fetch.

The failed batch's IDs are returned as `None` to the caller and stay absent from the cache.

### Handles API failure

A single-batch HTTP 500 response returns `None` for every requested ID, writes nothing to the cache, and does not raise — the caller can keep going.

### Handles network error

A `httpx.ConnectError` from the API call returns `None` for every requested ID, writes nothing to the cache, and does not raise.

### Raises without API key

`fetch_descriptions` raises `RuntimeError` mentioning `API_KEY_YOUTUBE` when the environment variable is unset and `.env` does not supply it.

### Deduplicates input ids

Duplicate IDs in the input list collapse to a single API fetch and a single result key, so a watch history with rewatches does not cost extra quota.
