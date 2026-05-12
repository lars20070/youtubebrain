# Ingest: write markdown per video

## Context

`uv run ingest` currently prints titles to stdout. Replace with per-video markdown files at `Markdown/raw/<video_id>.md` carrying title, titleUrl, channels, time — to feed downstream search/analysis. Folder already exists with `.gitkeep`.

## Code changes — [src/youtubebrain/ingest.py](src/youtubebrain/ingest.py)

Add:

- `MARKDOWN_RAW_DIR = Path("Markdown/raw")`
- `_video_id(url: HttpUrl) -> str` — `parse_qs(urlparse(str(url)).query)["v"][0]`; raise `ValueError` if `v` absent or `url` is None at caller.
- `_render_markdown(video: WatchedVideo) -> str` — body:
  ```
  # {title}

  - Title URL: {title_url}
  - Time: {time.isoformat()}

  ## Channels

  - [{name}]({url})
  - ...
  ```
  Empty `subtitles` → `## Channels` heading followed by `_(none)_`.
- `write_markdown(video: WatchedVideo, out_dir: Path) -> Path` — raise `ValueError` if `video.title_url is None`; `out_dir.mkdir(parents=True, exist_ok=True)`; write `<out_dir>/<video_id>.md`; overwrite ok; return path.

Modify `main()`:

- Iterate kept videos, call `write_markdown(v, MARKDOWN_RAW_DIR)`.
- Remove `print(video.title)`. Log count of files written via loguru.

Imports: `from urllib.parse import parse_qs, urlparse`.

## Test changes — [tests/test_ingest.py](tests/test_ingest.py)

Replace existing `main` stdout tests with file-output tests. Add unit tests for new helpers.

New / replaced tests:

- `test_video_id_extracts_from_url` — `JWWDqbcQoXA` extracted from sample URL.
- `test_video_id_raises_without_v_param` — `https://www.youtube.com/post/...` raises `ValueError`.
- `test_render_markdown_contains_all_fields` — output contains title, titleUrl, channel name+url, ISO time.
- `test_render_markdown_lists_multiple_channels` — two-subtitle record renders both as bullets.
- `test_render_markdown_empty_subtitles` — empty list → `_(none)_` placeholder.
- `test_write_markdown_creates_named_file` — `<id>.md` exists in `tmp_path`.
- `test_write_markdown_overwrites_existing` — second call replaces content (idempotent re-ingest).
- `test_write_markdown_raises_without_title_url` — record with `title_url=None` raises `ValueError`.
- `test_main_writes_files` — replaces `test_main_prints_titles`; monkeypatch `WATCH_HISTORY_PATH` AND `MARKDOWN_RAW_DIR` to `tmp_path`; assert one file per kept video, correct names, no stdout output.
- `test_main_skips_unresolved` — replaces `test_main_skips_unresolved_titles`; assert unresolved record produces no file.
- `test_main_skips_non_watch` — replaces `test_main_skips_non_watch_entries`; assert non-watch record produces no file.

Keep: `test_parses_valid_record`, `test_handles_empty_array`, `test_handles_optional_fields`, `test_rejects_unknown_field`, `test_filters_unresolved_titles`, `test_filters_non_watch_entries`, `test_default_path_constant`.

## lat.md updates — [lat.md/ingest.md](lat.md/ingest.md)

- `## Loader`: change "Running the module prints each title to stdout" → "Running the module writes one markdown file per kept video to `Markdown/raw/`". Drop "pipe-friendly stdout" paragraph.
- Add `## Video ID extraction` linking `[[src/youtubebrain/ingest.py#_video_id]]` — describes `v`-param parsing and ValueError contract.
- Add `## Markdown writer` linking `[[src/youtubebrain/ingest.py#write_markdown]]` and `[[src/youtubebrain/ingest.py#_render_markdown]]` — describes file layout, naming (`<video_id>.md`), overwrite semantics, raise on missing `title_url`.
- Add `## Default output directory` — `MARKDOWN_RAW_DIR = Markdown/raw`.
- Tests section: replace stdout test specs with file-output equivalents (`main writes files`, `main skips unresolved`, `main skips non-watch`). Add specs for new helper tests (`Video ID extraction`, `Render markdown fields`, `Render multiple channels`, `Render empty subtitles`, `Write markdown creates file`, `Write markdown overwrites`, `Write markdown requires title URL`).
- Add `@lat:` code-ref comments on every new test and new function/constant.

## Verification

```bash
uv run ruff format .
uv run ruff check --fix .
uv run pyright .
uv run pytest -n auto -m "not paid"
lat check
```

End-to-end:

```bash
rm -f Markdown/raw/*.md
uv run ingest
ls Markdown/raw | head        # expect <id>.md files
cat Markdown/raw/<one-id>.md  # expect title / URL / channels / time
```

## Critical files

- [src/youtubebrain/ingest.py](src/youtubebrain/ingest.py)
- [tests/test_ingest.py](tests/test_ingest.py)
- [lat.md/ingest.md](lat.md/ingest.md)

## Unresolved questions

None — design decisions confirmed: silent stdout, raise on missing titleUrl, render all subtitles as bullet list.
