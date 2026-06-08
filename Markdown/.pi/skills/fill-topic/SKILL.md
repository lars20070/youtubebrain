---
name: fill-topic
description: >-
  Enrich one seeded topic page in wiki/topics/<slug>/ into a standalone 400–1000
  word synthesis of everything its videos cover. Use when instructed to fill,
  enrich, or write up a topic page (by slug or cluster_id).
---

# Fill Topic

Enrich one topic page so it reads as a standalone synthesis of everything its videos cover. Trigger with `/skill:fill-topic <slug>` (or a `cluster_id`), or when asked to enrich a specific topic page.

All paths are relative to the `Markdown/` vault root (the working directory). See `AGENTS.md` for the shared conventions this skill builds on: the [three layers](../../../AGENTS.md), absolute rules, source page format, page frontmatter, and linking.

## Steps

1. Open the seeded page `wiki/topics/<slug>/<slug>.md` and read its frontmatter (`cluster_id`, `keywords`, `representative_ids`) and `## Videos` list.
2. Read the `## Summary` (and `## Description` as needed) of the member videos — start with `representative_ids`, then sample broadly across the cluster. For large clusters (> ~150 videos) sample representatively rather than reading all.
3. Rewrite the body below the heading into a 400–1000 word synthesis: the dominant themes, key claims, recurring people/tools/works, tensions and contradictions, and how the topic evolves over the watch period. Cross-link related `[[topics]]` and the relevant `[[creators|...]]`.
4. **Preserve the `## Videos` list** (it is the page's source index). Keep video citations as relative Markdown links: from a topic page, `[Title](../../../raw/<video_id>.md)`.
5. Update/add the required frontmatter (`slug`, `tldr`, `aliases`, `sources`, `confidence`, `last_updated`). `sources` should list the video IDs you actually drew claims from.
6. Append one line to `wiki/log.md`.
7. Update the topic's entry in `wiki/index.md` (add it if missing).

## Constraints

- **NEVER** modify, move, or delete anything under `raw/` — it is the immutable source of truth.
- **NEVER** invent facts not present in the `raw/` sources. Every synthesised claim must trace to specific video IDs. If sources are thin, write less — do not pad.
- When videos disagree, do not pick a winner. Write a short `### Contradictions` stating each position and citing the video ID(s) behind it (see the Contradiction Policy in `AGENTS.md`).
- Process one page at a time, then stop long enough to be inspectable.
