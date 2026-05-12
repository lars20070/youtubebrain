# Ingest: add YouTube descriptions to markdown

## Context

Per-video markdown files in `Markdown/raw/` currently carry Takeout metadata only (title, titleUrl, time, channels). Google Takeout does not include video descriptions — they must be fetched. Goal: enrich each markdown file with a `## Description` section sourced from the YouTube Data API v3.

Scope: descriptions only. No summaries, no transcripts, no SQLite, no yt-dlp fallback (deferred — bot-detection risk, heavy dep; API alone handles ~85–95 % of videos for free).

## Design decisions (confirmed)

- Cache: JSON file `Markdown/.cache/descriptions.json`, keyed by video_id. Re-runs read it first, only fetch missing IDs.
- Missing/deleted/private videos → render `_(unavailable)_` under the `## Description` heading.
- API key required: raise on startup if `API_KEY_YOUTUBE` unset.
- Cost: 10 K videos ÷ 50 per batch × 1 unit = ~200 quota units; ≪ 10 K/day free tier. Runtime ≈ 5 min.

## Dependency changes — [pyproject.toml](pyproject.toml)

- Add `"httpx>=0.27"` to `[project].dependencies`.
- Add `"respx>=0.21"` to `[dependency-groups].dev` for mocking httpx in tests.

## New module — `src/youtubebrain/descriptions.py`

Pure HTTP + cache layer. No side effects on markdown.

- `DESCRIPTIONS_CACHE_PATH = Path("Markdown/.cache/descriptions.json")`
- `YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"`
- `_load_cache(path: Path) -> dict[str, str | None]` — return `{}` if missing.
- `_save_cache(path: Path, cache: dict[str, str | None]) -> None` — ensures parent dir; pretty-prints for diff-ability.
- `_get_api_key() -> str` — reads `API_KEY_YOUTUBE` from env; raises `RuntimeError` with actionable message if unset.
- `_chunks(seq: list[str], n: int = 50) -> Iterable[list[str]]` — batch of 50 (API max).
- `async def _fetch_batch(client: httpx.AsyncClient, ids: list[str], api_key: str) -> dict[str, str]` — calls `videos.list?part=snippet&id=…`; returns `{id: description}` only for items the API returned. IDs absent from the response are deleted/private.
- `async def fetch_descriptions(video_ids: list[str], cache_path: Path = DESCRIPTIONS_CACHE_PATH) -> dict[str, str | None]` — main entry. Returns `{video_id: description_or_None}` for every input ID. Cache hit → skip; cache miss → batch-fetch; missing-from-API → store `None`. Writes cache after every batch (resumable). Uses `httpx.AsyncClient` with `timeout=30.0`.

Use `loguru` logger for progress (`Fetching N descriptions in M batches…`, `Cached H, fetched F, missing M`).

## Modifications — [src/youtubebrain/ingest.py](src/youtubebrain/ingest.py)

- `_render_markdown(video: WatchedVideo, description: str | None) -> str` — append:
  ```
  ## Description

  {description or "_(unavailable)_"}
  ```
- `write_markdown(video: WatchedVideo, out_dir: Path, description: str | None) -> Path` — accept and forward `description`.
- `main()` — orchestrator:
  1. `videos = load_watch_history(WATCH_HISTORY_PATH)`
  2. `ids = [_video_id(v.title_url) for v in videos if v.title_url is not None]`
  3. `descriptions = asyncio.run(fetch_descriptions(ids))`
  4. For each video: `write_markdown(video, MARKDOWN_RAW_DIR, descriptions.get(vid))`

Imports: `import asyncio`, `from youtubebrain.descriptions import fetch_descriptions`.

## Config — [.env.example](.env.example)

Add:

```
# YouTube Data API v3 (https://console.cloud.google.com/, enable "YouTube Data API v3")
API_KEY_YOUTUBE="YOUR-API-KEY"
```

## .gitignore

Add `Markdown/.cache/` so the description cache stays local.

## Tests — `tests/test_descriptions.py` (new) + [tests/test_ingest.py](tests/test_ingest.py)

New tests (use `respx` to mock httpx; `tmp_path` for cache file):

- `test_fetch_descriptions_uses_cache_first` — pre-seeded cache returns without HTTP call (assert no requests).
- `test_fetch_descriptions_batches_in_50s` — 75 IDs → exactly two POST/GET calls (respx route assertion).
- `test_fetch_descriptions_stores_none_for_missing` — API returns only 1 of 3 IDs → cache has `None` for the other two, returned dict has `None` for them.
- `test_fetch_descriptions_writes_cache_after_each_batch` — cache file exists with expected keys after first batch even if second batch errors.
- `test_fetch_descriptions_raises_without_api_key` — `monkeypatch.delenv("API_KEY_YOUTUBE")`; `RuntimeError` with helpful message.

Updates in [tests/test_ingest.py](tests/test_ingest.py):

- `test_render_markdown_contains_all_fields` — extend to assert `## Description` heading and description body present when passed.
- `test_render_markdown_unavailable_description` (new) — `description=None` renders `_(unavailable)_` placeholder.
- `test_write_markdown_creates_named_file` — pass a description; assert it appears in file.
- `test_main_writes_files` — `monkeypatch.setattr(ingest, "fetch_descriptions", AsyncMock(return_value={"abc123": "hello"}))` so main() doesn't touch network; assert description appears in file.
- All other existing tests pass through with `description=None` default arg (keep signature change backward-compat at the test seam by giving `_render_markdown` / `write_markdown` `description: str | None = None`).

## lat.md updates — [lat.md/ingest.md](lat.md/ingest.md) + new [lat.md/descriptions.md](lat.md/descriptions.md)

- New `lat.md/descriptions.md` with sections: `# Descriptions`, `## Cache`, `## API client`, `## Missing videos`, `## API key requirement`, `## Tests`.
- Update `lat.md/ingest.md`:
  - `## Loader` paragraph: note that `main()` fetches descriptions before writing files.
  - `## Markdown writer` file-layout block: add `## Description` section at the bottom.
  - Add `@lat:` refs from new code → new sections.
  - Tests subsection: add specs for `Render with description`, `Render unavailable description`.

## Verification

```bash
uv sync
uv run ruff format .
uv run ruff check --fix .
uv run pyright .
uv run pytest -n auto -m "not paid"
lat check
```

End-to-end (requires real API key):

```bash
export API_KEY_YOUTUBE="<your-key>"
rm -rf Markdown/raw/* Markdown/.cache
uv run ingest
ls Markdown/.cache              # descriptions.json exists
cat Markdown/raw/<one-id>.md    # ## Description section present
# Re-run: should be near-instant (all cache hits)
uv run ingest
```

## Critical files

- new: [src/youtubebrain/descriptions.py](src/youtubebrain/descriptions.py)
- new: [tests/test_descriptions.py](tests/test_descriptions.py)
- new: [lat.md/descriptions.md](lat.md/descriptions.md)
- modified: [src/youtubebrain/ingest.py](src/youtubebrain/ingest.py)
- modified: [tests/test_ingest.py](tests/test_ingest.py)
- modified: [lat.md/ingest.md](lat.md/ingest.md)
- modified: [pyproject.toml](pyproject.toml), [.env.example](.env.example), [.gitignore](.gitignore)

## Unresolved questions

None — cache strategy, missing-video placeholder, yt-dlp deferral, and API-key strictness all confirmed.
