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

[[src/youtubebrain/ingest.py#load_watch_history]] reads the Takeout `watch-history.json` array, validates it as `list[WatchedVideo]`, then applies [[ingest#Non-watch activity filtering]] and [[ingest#Unresolved title filtering]] before returning.

Running the module fetches descriptions via [[descriptions#API client]], reads any cached transcripts via [[transcripts#Read API]], and writes one markdown file per kept video to [[ingest#Default output directory]]. Progress and per-run record counts (parsed, kept, files written) are emitted via the project loguru logger to `youtubebrain.log`; stdout is silent.

## Non-watch activity filtering

[[src/youtubebrain/ingest.py#_is_video_watch]] keeps only records whose `title` starts with `"Watched "`. `load_watch_history` silently drops anything else.

Detection rule: a record is a video watch iff `title.startswith("Watched ")`. Other activity types — most commonly community-post views with a `"Viewed "` prefix — are excluded.

**Motivation.** The Takeout `watch-history.json` array is not strictly a list of video watches. It also contains other YouTube activity types interleaved into the same export under the same `WatchedVideo` schema. The most common alternative is community-post views, which appear with a `"Viewed "` prefix and embed the full post body text directly into the `title` field. Their `titleUrl` points at `/post/<id>` rather than `/watch?v=<id>`.

These records are semantically unrelated to video watches: there is no video duration, no video metadata, and the embedded prose is sometimes long-form (announcements, apologies, link previews). Mixing them into a video-only pipeline would skew counts, embeddings and per-channel aggregations, and pollute downstream search with text that does not describe a watched video.

Filtering by the `"Watched "` prefix is the simplest, most reliable way to keep the dataset focused on actual video watches without enumerating every alternative activity type Takeout might emit. The drop is silent for the same reason as [[ingest#Unresolved title filtering]] — the count is small but non-zero on long histories, and per-record warnings would be noise rather than signal.

## Unresolved title filtering

[[src/youtubebrain/ingest.py#_is_unresolved]] flags records where Takeout failed to resolve the canonical video title and fell back to embedding the URL in the `title` field. `load_watch_history` silently drops these entries.

Detection rule: a record is unresolved iff `title == f"Watched {titleUrl}"`. In the raw export these records additionally have no `subtitles` field, but the title equality is the precise signal — `subtitles` can also be missing for legitimate edge cases.

**Motivation.** When Google Takeout assembles the watch-history export, it joins each viewing event back to its video metadata. If the video has been deleted, made private, region-blocked, copyright-removed, or its channel terminated, the join fails and Google substitutes the URL itself — yielding records like `"title": "Watched https://www.youtube.com/watch?v=…"` with no channel info.

Such entries carry no useful signal for downstream search, summarisation or topic analysis: both title and channel are missing, leaving only an opaque video ID. They cannot be embedded into a semantic index in any meaningful way and would only pollute results with placeholder URL strings. The user has no realistic recovery path either — by the time the export is generated, the source video is already gone or inaccessible.

Filtering at ingest is therefore preferred over carrying the entries downstream and filtering per-consumer. The drop is silent because the count is expected to be non-trivial in any long watch history (channel churn, copyright takedowns, privacy changes accumulate over years) and surfacing each one would be noise, not signal. If a future need arises to audit which videos were dropped, `_is_unresolved` can be reused directly against the unfiltered `TypeAdapter` output.

## Video ID extraction

[[src/youtubebrain/ingest.py#_video_id]] parses the `v` query parameter from a YouTube watch URL — for example `https://www.youtube.com/watch?v=JWWDqbcQoXA` yields `JWWDqbcQoXA`.

A URL without a `v` parameter (e.g. a community-post URL of the form `/post/<id>`) raises `ValueError`. Non-watch records are already dropped by [[ingest#Non-watch activity filtering]] before reaching this function, so the raise is a hard schema-drift signal rather than an expected runtime branch.

## Channel ID extraction

[[src/youtubebrain/ingest.py#_channel_id]] returns the last path segment from a canonical `/channel/<id>` URL — for example `https://www.youtube.com/channel/UCvPXiKxH-eH9xq-80vpgmKQ` yields `UCvPXiKxH-eH9xq-80vpgmKQ`.

URLs that are not `/channel/<id>` (e.g. `/@handle` vanity URLs) raise `ValueError`, matching the hard schema-drift posture of [[ingest#Video ID extraction]].

## Markdown writer

[[src/youtubebrain/ingest.py#write_markdown]] writes a markdown file for one `WatchedVideo` into the configured output directory, named `<video_id>.md` where the ID comes from [[ingest#Video ID extraction]]. The body is produced by [[src/youtubebrain/ingest.py#_render_markdown]].

Descriptions come from [[descriptions#API client]]; plain transcript text (when present) comes from [[transcripts#Read API]] and is rendered under a `## Transcript` heading after `## Description`.

File layout:

```
---
id: {video_id}
url: {title_url}
title: {title}
channels:
  - name: {channel_name}
    id: {channel_id}
    url: {channel_url}
watch_time: {iso_time}
---

## Description

{description}

## Transcript

{transcript}
```

Video metadata (`id`, `url`, `title`, `channels`, `watch_time`) lives in YAML frontmatter serialised via PyYAML. Channel `id` values come from [[ingest#Channel ID extraction]] on each subtitle URL.

The leading `Watched ` substring is stripped from the title before rendering — every kept record carries that prefix by construction of [[ingest#Non-watch activity filtering]], so the boilerplate adds no signal and dropping it keeps titles readable.

Multiple subtitles become separate entries in the `channels` list. An empty subtitles list renders `channels: []`. A `None` description (video deleted or otherwise unavailable per [[descriptions#Missing videos]]) renders the `_(unavailable)_` placeholder under the Description heading.

Writes are idempotent: a second call overwrites the file in place, so re-running `uv run ingest` against an updated Takeout export refreshes existing files without leaving stale duplicates.

`write_markdown` raises `ValueError` if `title_url` is `None`. Such records survive `load_watch_history` (only URL-placeholder titles are dropped, not records missing the URL entirely), so the raise is a hard signal of an unusual Takeout shape rather than expected runtime behaviour.

## Default output directory

[[src/youtubebrain/ingest.py#MARKDOWN_RAW_DIR]] is the repo-root-relative `Markdown/raw` folder.

`write_markdown` creates the directory (and any missing parents) on first call via `mkdir(parents=True, exist_ok=True)`, so a fresh checkout works without manual setup. The folder is checked in with a `.gitkeep` placeholder.

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

### Filters unresolved titles

`load_watch_history` drops records whose `title` equals `Watched <titleUrl>`, leaving only resolvable videos in the returned list.

### Filters non-watch entries

`load_watch_history` drops records whose `title` does not start with `Watched `, removing community-post views and other non-watch activity from the returned list.

### Default path constant

`WATCH_HISTORY_PATH` equals the repo-root-relative Takeout export path.

### Video ID extraction

`_video_id` returns the `v` query parameter from a standard watch URL.

### Video ID raises without v param

`_video_id` raises `ValueError` on URLs lacking a `v` query parameter, such as `/post/<id>` community-post URLs.

### Channel ID extraction

`_channel_id` returns the last path segment from a `/channel/<id>` URL.

### Channel ID raises on bad URL

`_channel_id` raises `ValueError` on URLs that are not `/channel/<id>`, such as `/@handle` vanity URLs.

### Render markdown frontmatter

`_render_markdown` YAML frontmatter includes `id`, `url`, `title`, `channels` (each with `name`, `id`, `url`), and `watch_time`.

### Render multiple channels

A record with multiple subtitles renders each channel as its own entry in the `channels` YAML list.

### Render empty subtitles

An empty subtitles list renders `channels: []` in the frontmatter.

### Render strips watched prefix

The `Watched ` prefix from Takeout titles is removed before the value is written to the `title` frontmatter key.

### Render unavailable description

A `None` description argument renders the `_(unavailable)_` placeholder under the Description heading.

### Write markdown creates file

`write_markdown` writes a file named `<video_id>.md` in the output directory and returns its path.

### Write markdown overwrites

A second `write_markdown` call replaces an existing file's contents, so re-ingesting against an updated export is idempotent.

### Write markdown requires title URL

`write_markdown` raises `ValueError` when the record has no `title_url`, since there is no video ID to use as the filename.

### main writes files

Calling `main()` against a monkeypatched watch-history.json and output directory writes one `<video_id>.md` file per kept video and prints nothing to stdout.

### main skips unresolved

`main()` does not write a file for unresolved (URL-placeholder) records.

### main skips non-watch

`main()` does not write a file for non-watch activity such as community-post views.

### Default output directory

`MARKDOWN_RAW_DIR` equals the repo-root-relative `Markdown/raw` folder.
