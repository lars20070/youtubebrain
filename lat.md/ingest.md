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


