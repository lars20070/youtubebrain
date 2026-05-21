# Phase 1.3 — Materialise clusters as wiki topic pages + raw frontmatter `topic`

## Context

Phase 1.2 fits BERTopic and persists `(assignments.json, topics.json, meta.json, bertopic_model/)` under `Markdown/clustering/`, but nothing downstream consumes it. Phase 1.3 turns those clusters into first-class wiki artefacts:

1. One `Markdown/wiki/topics/<slug>/<slug>.md` page per cluster (incl. outliers).
2. Two new fields injected into every `Markdown/raw/<id>.md` frontmatter: `topic: <kebab-slug>` and `cluster_id: <int>`.

This is the first step the Karpathy LLM-Wiki pattern needs (topic skeleton + per-video back-pointer) before later phases can compile syntheses on top.

Both steps run inline at the end of `uv run cluster`, after `save_atomic` succeeds.

## File changes

- [src/youtubebrain/clusters.py](src/youtubebrain/clusters.py) — add wiki-topics step + call it from `cluster_all`.
- [tests/test_clusters.py](tests/test_clusters.py) — add tests for the new step.
- [lat.md/clusters.md](lat.md/clusters.md) — add `Wiki topics` section + test specs; extend mermaid.

No new dependencies. Use `yaml` (already imported in [src/youtubebrain/embeddings.py](src/youtubebrain/embeddings.py)) for frontmatter round-trip.

## New code in `clusters.py`

Constants:
- `WIKI_TOPICS_DIR = Path("Markdown/wiki/topics")`

Pure helpers:
- `_resolve_slugs(topics: list[TopicInfo]) -> dict[int, str]`
  Map `cluster_id → unique kebab slug`. On collision of `label`, suffix in cluster_id order: `<label>`, `<label>-1`, `<label>-2`. Outlier (`-1`) gets slug `outliers`.
- `_render_topic_page(topic: TopicInfo, slug: str, member_id_titles: list[tuple[str, str]]) -> str`
  Minimal page: YAML frontmatter (`label`, `cluster_id`, `count`, `keywords`, `representative_ids`) + `# <slug>` + description paragraph + `## Videos` bulleted list `- <title> ([<id>](../../../raw/<id>.md))`. Match ingest YAML style: `sort_keys=False, allow_unicode=True, default_flow_style=False`.
- `_load_titles_by_id(ids: list[str]) -> dict[str, str]`
  Walk `Markdown/raw/` once; reuse [src/youtubebrain/embeddings.py#parse_raw_markdown](src/youtubebrain/embeddings.py) and keep `title` only. Missing files → omit (logged once at warning level w/ counts).
- `_inject_topic_into_raw(path: Path, slug: str, cluster_id: int) -> None`
  Read file, parse frontmatter via existing `_FRONTMATTER_FENCE` convention into a dict with `yaml.safe_load`, then `fm["topic"] = slug` and `fm["cluster_id"] = cluster_id`. Because we mutate a single dict, both fields appear **exactly once** even if the prior file already had them (or had them more than once via prior bug) — `yaml.safe_load` collapses duplicates and dict assignment overwrites. Re-dump frontmatter preserving original key order via `sort_keys=False, allow_unicode=True, default_flow_style=False`, write body verbatim, atomic `*.tmp` + `os.replace`.

Orchestrator:
- `write_wiki_topics(assignments: list[int], ids: list[str], topics: list[TopicInfo]) -> None`
  1. `slug_by_cluster = _resolve_slugs(topics)`.
  2. **Wipe & rewrite**: `shutil.rmtree(WIKI_TOPICS_DIR, ignore_errors=True)`; `WIKI_TOPICS_DIR.mkdir(parents=True, exist_ok=True)`.
  3. Build `members_by_cluster: dict[int, list[str]]` from `zip(ids, assignments)`.
  4. `titles_by_id = _load_titles_by_id(ids)`.
  5. For each `TopicInfo`:
     - Create `WIKI_TOPICS_DIR / slug / f"{slug}.md"`.
     - Write rendered page text.
  6. For each `(vid, cid)`, look up `slug_by_cluster[cid]` and call `_inject_topic_into_raw(MARKDOWN_RAW_DIR / f"{vid}.md", slug, cid)`. Skip silently if the raw file is missing (logged in aggregate).

Wiring:
- In `cluster_all`, immediately after the existing `save_atomic(...)` call, add `write_wiki_topics(assignments, ids, topics)` and log a one-line summary `wiki topics written: <n_topics>, raw frontmatter updated: <n_videos>`.

## Behaviour details

- **Topic field value**: video raw markdown gets BOTH `topic: <slug>` (stable kebab label for the run) and `cluster_id: <int>` (matches the numeric id in `assignments.json`).
- **Outliers**: rendered as `topics/outliers/outliers.md` with empty `keywords`/`representative_ids`, the synthetic description `"Videos that did not fit any cluster."`, and the full list of 112 (current) outlier videos. Outlier raw files get `topic: outliers, cluster_id: -1`.
- **Slug collisions**: suffix `-1`, `-2`, ... in cluster_id ascending order; deterministic for a given LLM output.
- **Stale folders**: `WIKI_TOPICS_DIR` is wiped at the start of every cluster run — cluster labels are not stable across runs.
- **Idempotency & no duplicates**: `_inject_topic_into_raw` always emits `topic` and `cluster_id` exactly once. Existing values are overwritten in place; any pre-existing duplicates (from prior buggy runs) are collapsed to a single entry by the safe_load → dict → safe_dump round-trip. Re-running on an already-injected file is a no-op on disk.
- **Body preservation**: raw markdown bodies (Summary / Description / Transcript sections) are written back verbatim.

## Tests to add in `test_clusters.py` (each with one `# @lat:` comment)

- `# @lat: [[clusters#Wiki topics#Tests#Slug resolution dedups]]` — `_resolve_slugs` returns `{0: "x", 1: "x-1"}` when two clusters share label `x`; outlier maps to `outliers`.
- `# @lat: [[clusters#Wiki topics#Tests#Wipe & rewrite removes stale folders]]` — pre-create `topics/stale/stale.md`; after `write_wiki_topics(...)` the `stale/` folder is gone and only current slugs exist.
- `# @lat: [[clusters#Wiki topics#Tests#Topic page rendered correctly]]` — generated `<slug>/<slug>.md` parses back via `yaml.safe_load` to the expected frontmatter (`label`, `cluster_id`, `count`, `keywords`, `representative_ids`); body contains the description paragraph and a `- <title> ([<id>](...))` line per member.
- `# @lat: [[clusters#Wiki topics#Tests#Raw frontmatter injected]]` — given a fixture raw file, `_inject_topic_into_raw` adds `topic` and `cluster_id` without touching existing keys or body; second call is a no-op (`hash(text)` unchanged).
- `# @lat: [[clusters#Wiki topics#Tests#Raw frontmatter never duplicated]]` — (a) calling `_inject_topic_into_raw` twice with *different* values leaves only one `topic:` and one `cluster_id:` line, holding the second values; (b) a fixture file that already has both fields (and a synthetic file containing a duplicate `topic:` line) is rewritten to contain each key exactly once.
- `# @lat: [[clusters#Wiki topics#Tests#Outliers folder written]]` — when assignments include `-1`s, `topics/outliers/outliers.md` exists and the corresponding raw files carry `topic: outliers, cluster_id: -1`.
- `# @lat: [[clusters#Wiki topics#Tests#Cluster all writes wiki]]` — happy-path `cluster_all` (with existing stubs) leaves both `Markdown/clustering/*.json` and `Markdown/wiki/topics/<slug>/<slug>.md` files in place, and the raw markdown fixtures get `topic`/`cluster_id` injected.
- `# @lat: [[clusters#Wiki topics#Tests#Missing raw file does not abort]]` — when `ids` contains an id with no `Markdown/raw/<id>.md`, `write_wiki_topics` logs a warning and finishes; other videos still get the injection.

## `lat.md/clusters.md` updates

- Extend the mermaid diagram with `Topics --> WikiTopics[(wiki/topics/<slug>/)]` and `Assign --> RawFM[raw/<id>.md frontmatter]`.
- Insert a new `## Wiki topics` section (with leading paragraph ≤250 chars) covering: directory layout (`topics/<slug>/<slug>.md`), slug rules + collision suffix, outlier handling, wipe-and-rewrite policy, raw frontmatter contract (`topic` + `cluster_id`, idempotent overwrite, body preserved).
- Pipeline order line in `## CLI entry` already lists `uv run cluster` last — no change needed; note that the command now also writes the wiki layer.
- Reference the new functions: `[[src/youtubebrain/clusters.py#write_wiki_topics]]`, `[[src/youtubebrain/clusters.py#_resolve_slugs]]`, `[[src/youtubebrain/clusters.py#_inject_topic_into_raw]]`, `[[src/youtubebrain/clusters.py#_render_topic_page]]`, `[[src/youtubebrain/clusters.py#_load_titles_by_id]]`.

## Verification

1. Lint + types + tests:
   ```
   uv run ruff format .
   uv run ruff check --fix .
   uv run pyright .
   uv run pytest -n auto
   ```
2. End-to-end on the real corpus:
   ```
   rm -rf Markdown/wiki/topics
   uv run cluster
   ```
   Expect `Markdown/wiki/topics/<slug>/<slug>.md` for every entry in `topics.json` incl. `outliers/`; spot-check one raw file (e.g. `Markdown/raw/UQZooauU-FQ.md`) to confirm `topic: ai-enterprise-transformation` and `cluster_id: 0` are present and the body is unchanged.
3. `lat check` passes (all new wiki refs + code refs resolve).

## Open questions

None.
