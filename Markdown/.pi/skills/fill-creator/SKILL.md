---
name: fill-creator
description: >-
  Give a YouTube channel a real wiki page (150–500 words) describing who they are
  and what their videos in this library cover, filling the seeded stub at
  wiki/creators/<channel_id>.md. Use when asked to fill or enrich a creator page.
---

# Fill Creator

Give a channel a real page describing who they are and what their videos in this library are about. Trigger with `/skill:fill-creator <channel_id>`, or when asked to enrich a specific creator page.

All paths are relative to the `Markdown/` vault root (the working directory). See `AGENTS.md` for the shared conventions this skill builds on: the [three layers](../../../AGENTS.md), absolute rules, source page format, page frontmatter, and linking.

## Steps

1. Open the stub `wiki/creators/<channel_id>.md` (frontmatter `name`, `id`, `url`; empty body) — **preserve its frontmatter**.
2. Find this channel's videos by scanning `raw/` frontmatter for `channels[].id == <channel_id>`. Read their summaries.
3. Write a body: who the creator is, the subjects/format they cover, which `[[topics]]` they contribute to, and notable individual videos (cited as relative Markdown links). 150–500 words; scale to how many videos exist.
4. Add the required frontmatter fields (`slug` = channel id, `tldr`, `aliases` including the human-readable name, `sources`, `confidence`, `last_updated`).
5. Append one line to `wiki/log.md` and update the creator's entry in `wiki/index.md`.

## Linking

- Cite a raw video from a creator page (`creators/<channel_id>.md`) with a relative Markdown link: `[Title](../../raw/<video_id>.md)`.
- Cross-link other wiki pages with `[[topic-slug]]` and the alias form `[[UC...|Creator Name]]`. Never use absolute paths.

## Constraints

- **NEVER** modify, move, or delete anything under `raw/` — it is the immutable source of truth.
- **NEVER** invent facts not present in the `raw/` sources. Every claim must trace to specific video IDs. If sources are thin, write less — do not pad.
- Process one page at a time, then stop long enough to be inspectable.
