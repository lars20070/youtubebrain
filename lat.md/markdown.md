---
lat:
  require-code-mention: true
---
# Markdown

Compiles one raw markdown file per watched video and also provides the shared parser/composition helpers used by embeddings and clusters.

The stage is intentionally pure and offline: it reads Takeout plus the three SQLite-backed caches, then writes deterministic `Markdown/raw/<video_id>.md` files with placeholders for missing rows.

## CLI entry

[[src/youtubebrain/markdown.py#main]] is the `uv run markdown` entry point.

It loads filtered videos from [[takeout#Loader]], reads the three cache read APIs ([[descriptions#Read API]], [[transcripts#Read API]], [[summaries#Read API]]), and writes every raw markdown file via [[src/youtubebrain/markdown.py#write_markdown]].

## Markdown writer

[[src/youtubebrain/markdown.py#render_markdown]] and [[src/youtubebrain/markdown.py#write_markdown]] implement the raw file format.

The document contains YAML frontmatter (`id`, `url`, `title`, `channels`, `watch_time`) and exactly three sections in order: `## Summary`, `## Description`, `## Transcript`. Missing values render as `_(unavailable)_`.

## Parsing rules

[[src/youtubebrain/markdown.py#read_frontmatter]] and [[src/youtubebrain/markdown.py#parse_raw_markdown]] parse `Markdown/raw/<id>.md` back into structured values.

`read_frontmatter` raises `ValueError` on missing or unclosed fences, malformed YAML, and non-mapping top-level YAML. `parse_raw_markdown` requires string `id` and `title`, then normalizes empty or placeholder section bodies to `None`.

## Text composition

[[src/youtubebrain/markdown.py#compose_text]] returns embeddable text from parsed markdown sections.

The policy is summary-first: emit `title + summary`, fall back to `title + description`, otherwise `None` so the caller can skip videos with no embeddable prose.

## Raw file iteration

[[src/youtubebrain/markdown.py#iter_raw_files]] yields deterministic `*.md` paths from the raw directory.

When the directory does not exist, iteration is empty so downstream stages can no-op gracefully on fresh checkouts.

## Default output directory

[[src/youtubebrain/config.py#MARKDOWN_RAW_DIR]] points at `Markdown/raw`.

The writer creates parent directories on demand, so a run does not require manual folder bootstrapping.

## Tests

Pytest covers rendering, parsing, composition, write semantics, and compile entrypoint wiring; each leaf below maps to one `# @lat:` comment in `tests/test_markdown.py`.

### Render markdown frontmatter

`render_markdown` frontmatter contains `id`, `url`, `title`, `channels`, and `watch_time` with expected values.

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

### Render markdown Transcript section

`render_markdown` includes `## Transcript` with supplied body text.

### Render transcript unavailable placeholder

`None` transcript renders `_(unavailable)_` under Transcript.

### Write markdown creates file

`write_markdown` writes `<video_id>.md` in the target output directory and returns its path.

### Write markdown overwrites

Running `write_markdown` again replaces existing file contents for idempotent re-compiles.

### Write markdown requires title URL

`write_markdown` raises `ValueError` when `title_url` is missing.

### main writes files

`main()` writes one markdown file per kept video and remains silent on stdout.

### main skips unresolved

`main()` does not write files for unresolved URL-placeholder records.

### main skips non-watch

`main()` does not write files for non-watch activity records.

### main folds transcripts

`main()` passes loaded transcript text through to the written Transcript section.

### Default output directory

`config.MARKDOWN_RAW_DIR` resolves to `Markdown/raw`.

### Frontmatter parsing

`parse_raw_markdown` returns `id`, `title`, and the Summary/Description bodies from a typical raw markdown file.

### Unavailable placeholder recognized as missing

Section bodies equal to `_(unavailable)_` are returned as `None` rather than the literal placeholder string.

### Frontmatter missing fence rejected

A file that does not start with the `---` fence raises `ValueError`, so corrupt raw files are skipped loudly by callers instead of being silently mis-parsed.

### Frontmatter unclosed fence rejected

An opening fence without a closing fence raises `ValueError`.

### Frontmatter malformed yaml rejected

Invalid YAML between the fences raises `ValueError` wrapping the underlying parser error.

### Frontmatter non-mapping rejected

A top-level YAML value that is not a mapping (e.g. a list) raises `ValueError`.

### Frontmatter empty returns empty mapping

Two adjacent fences parse to an empty dict rather than `None`, so callers can call `.get` without guarding.

### Parse requires string id and title

`parse_raw_markdown` raises `ValueError` when the frontmatter lacks string `id` and `title` values.

### Compose prefers summary

When both summary and description are present, `compose_text` returns `title + summary` and ignores the description.

### Compose falls back to description

`compose_text` with `summary=None` returns `title + description`.

### Compose skipped when both missing

`compose_text` with both `summary` and `description` `None` returns `None`.
