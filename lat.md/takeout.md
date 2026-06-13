---
lat:
  require-code-mention: true
---
# Takeout

Parses and filters Google Takeout watch-history exports and exposes reusable helpers for video/channel IDs used across pipeline stages.

## WatchedVideo model

Pydantic model in [[src/youtubebrain/models.py#WatchedVideo]] mirroring one record from the Takeout `watch-history.json` array.

Fields (Python name -> JSON key when aliased):

- `header: str` - top-level Takeout product header.
- `title: str` - Takeout-rendered title, prefixed `Watched ` for video watches.
- `title_url: HttpUrl | None` (alias `titleUrl`) - canonical watch URL.
- `subtitles: list[Subtitle]` - channel references, default `[]`.
- `time: datetime` - watch timestamp.
- `products: list[str]` - Takeout product tags.
- `activity_controls: list[str]` (alias `activityControls`) - Google activity-control labels.
- `description: str | None` - optional value filled by downstream description fetching.

The companion [[src/youtubebrain/models.py#Subtitle]] submodel carries `name` and `url` for one channel reference. Both models use `extra="forbid"` to surface schema drift early.

## Loader

[[src/youtubebrain/takeout.py#load_watch_history]] reads `watch-history.json`, validates it as `list[WatchedVideo]`, and filters with [[takeout#Non-watch activity filtering]] and [[takeout#Unresolved title filtering]].

The function logs parsed and kept counts and returns only videos that are meaningful for downstream markdown, summaries, and clustering.

## Video IDs

[[src/youtubebrain/takeout.py#load_video_ids]] loads filtered history and returns one `video_id` per record with a `title_url`.

The path defaults to [[src/youtubebrain/config.py#WATCH_HISTORY_PATH]] when not given. This gives stages like [[src/youtubebrain/transcripts.py#main]] and [[src/youtubebrain/summaries.py#_main_async]] a shared, cycle-free way to derive queue ids.

## Non-watch activity filtering

[[src/youtubebrain/takeout.py#is_video_watch]] keeps only records whose `title` starts with `Watched `; [[src/youtubebrain/takeout.py#load_watch_history]] silently drops anything else.

Detection rule: a record is a video watch iff `title.startswith("Watched ")`. Other activity types — most commonly community-post views with a `"Viewed "` prefix — are excluded.

**Motivation.** The Takeout `watch-history.json` array is not strictly a list of video watches. It also contains other YouTube activity types interleaved into the same export under the same `WatchedVideo` schema. The most common alternative is community-post views, which appear with a `"Viewed "` prefix and embed the full post body text directly into the `title` field. Their `titleUrl` points at `/post/<id>` rather than `/watch?v=<id>`.

These records are semantically unrelated to video watches: there is no video duration, no video metadata, and the embedded prose is sometimes long-form (announcements, apologies, link previews). Mixing them into a video-only pipeline would skew counts, embeddings and per-channel aggregations, and pollute downstream search with text that does not describe a watched video.

Filtering by the `"Watched "` prefix is the simplest, most reliable way to keep the dataset focused on actual video watches without enumerating every alternative activity type Takeout might emit. The drop is silent for the same reason as [[takeout#Unresolved title filtering]] — the count is small but non-zero on long histories, and per-record warnings would be noise rather than signal.

## Unresolved title filtering

[[src/youtubebrain/takeout.py#is_unresolved]] flags records where Takeout failed to resolve the canonical video title and fell back to `title == f"Watched {titleUrl}"`; [[src/youtubebrain/takeout.py#load_watch_history]] silently drops these entries.

Detection rule: a record is unresolved iff `title == f"Watched {titleUrl}"`. In the raw export these records additionally have no `subtitles` field, but the title equality is the precise signal — `subtitles` can also be missing for legitimate edge cases.

**Motivation.** When Google Takeout assembles the watch-history export, it joins each viewing event back to its video metadata. If the video has been deleted, made private, region-blocked, copyright-removed, or its channel terminated, the join fails and Google substitutes the URL itself — yielding records like `"title": "Watched https://www.youtube.com/watch?v=…"` with no channel info.

Such entries carry no useful signal for downstream search, summarisation or topic analysis: both title and channel are missing, leaving only an opaque video ID. They cannot be embedded into a semantic index in any meaningful way and would only pollute results with placeholder URL strings. The user has no realistic recovery path either — by the time the export is generated, the source video is already gone or inaccessible.

Filtering at load time is therefore preferred over carrying the entries downstream and filtering per-consumer. The drop is silent because the count is expected to be non-trivial in any long watch history (channel churn, copyright takedowns, privacy changes accumulate over years) and surfacing each one would be noise, not signal. If a future need arises to audit which videos were dropped, `is_unresolved` can be reused directly against the unfiltered `TypeAdapter` output.

## Video ID extraction

[[src/youtubebrain/takeout.py#video_id]] parses the `v` query parameter from a watch URL and returns it.

URLs without a `v` parameter raise `ValueError`, signalling non-watch URLs or malformed input.

## Channel ID extraction

[[src/youtubebrain/takeout.py#channel_id]] extracts the last path segment from `/channel/<id>` URLs.

Non-`/channel/<id>` URLs raise `ValueError` so invalid channel links fail loudly.

## Tests

Loader, filtering, and id-extraction behavior is verified in `tests/test_takeout.py` with inline fixtures and no dependency on a real `Takeout/` directory.

### Parses valid record

A minimal valid JSON array round-trips to `list[WatchedVideo]` with aliased fields and `title_url` populated.

### Handles empty array

An empty JSON array returns an empty list.

### Handles optional fields

Records missing `titleUrl`, `subtitles`, or `description` still parse.

### Rejects unknown field

Unknown JSON keys raise `ValidationError` due to model `extra="forbid"`.

### Filters unresolved titles

URL-placeholder titles are dropped so only resolvable videos remain.

### Filters non-watch entries

Records without a `Watched ` prefix are dropped from returned history.

### Load video ids filters records

`load_video_ids` returns one id per kept record with a `title_url`; filtered records and records without a URL contribute nothing.

### Load video ids default path

`load_video_ids` called without a path reads `config.WATCH_HISTORY_PATH`.

### Default path constant

`config.WATCH_HISTORY_PATH` points to the repo-root-relative Takeout export file.

### Video ID extraction

`video_id` returns the `v` query parameter from standard watch URLs.

### Video ID raises without v param

`video_id` raises `ValueError` for URLs missing the `v` parameter.

### Channel ID extraction

`channel_id` returns the last path segment from `/channel/<id>` URLs.

### Channel ID raises on bad URL

`channel_id` raises `ValueError` when the URL shape is not `/channel/<id>`.
