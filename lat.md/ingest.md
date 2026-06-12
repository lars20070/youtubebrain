---
lat:
  require-code-mention: true
---
# Ingest

Compiles one raw markdown file per watched video by combining Takeout metadata with cached descriptions, transcripts, and summaries.

## CLI entry

[[src/youtubebrain/ingest.py#main]] is the `uv run ingest` entry point used by the current pipeline.

It reads filtered videos from [[takeout#Loader]], fetches descriptions via [[descriptions#API client]], loads cached transcripts via [[transcripts#Read API]], loads cached summaries via [[summaries#Read API]], then writes `Markdown/raw/<video_id>.md`.

## Markdown writer

[[src/youtubebrain/ingest.py#write_markdown]] writes one markdown file named `<video_id>.md` and delegates body rendering to [[src/youtubebrain/ingest.py#_render_markdown]].

The document includes YAML frontmatter (`id`, `url`, `title`, `channels`, `watch_time`) and three sections in order: `## Summary`, `## Description`, `## Transcript`. Missing values render as `_(unavailable)_`.

Video ids and channel ids are derived with [[takeout#Video ID extraction]] and [[takeout#Channel ID extraction]].

## Default output directory

[[src/youtubebrain/config.py#MARKDOWN_RAW_DIR]] is the repo-root-relative `Markdown/raw` directory.

`write_markdown` creates missing parent directories on demand, so a fresh checkout can run ingest without manual folder setup.

## Tests

Markdown rendering and ingest entrypoint behavior are verified in `tests/test_ingest.py` using inline fixtures and monkeypatched cache readers.

### Render markdown frontmatter

`_render_markdown` frontmatter contains `id`, `url`, `title`, `channels`, and `watch_time` with expected values.

### Render summary section

The rendered body places `## Summary` before Description and Transcript and keeps supplied summary text.

### Render unavailable summary

A `None` summary renders `_(unavailable)_` under `## Summary`.

### Render unavailable description

A `None` description renders `_(unavailable)_` under `## Description`.

### Render multiple channels

Multiple subtitle entries render as multiple `channels` list items in frontmatter.

### Render empty subtitles

An empty subtitles list renders `channels: []`.

### Render strips watched prefix

The leading `Watched ` prefix is removed from the rendered `title`.

### Write markdown creates file

`write_markdown` writes `<video_id>.md` in the target output directory and returns its path.

### Write markdown overwrites

Running `write_markdown` again replaces existing file contents for idempotent re-ingest.

### Write markdown requires title URL

`write_markdown` raises `ValueError` when `title_url` is missing.

### main writes files

`main()` writes one markdown file per kept video and remains silent on stdout.

### main skips unresolved

`main()` does not write files for unresolved URL-placeholder records.

### main skips non-watch

`main()` does not write files for non-watch activity records.

### Default output directory

`config.MARKDOWN_RAW_DIR` resolves to `Markdown/raw`.
