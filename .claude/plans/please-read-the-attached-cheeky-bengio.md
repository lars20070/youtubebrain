# Cluster + label with BERTopic

## Context

Phase 1 point 2 of the YouTube-wiki plan. Embeddings already land in `Markdown/embeddings/` (npy + ids.json + meta.json) via [src/youtubebrain/embeddings.py](src/youtubebrain/embeddings.py). Next: turn the 384-d point cloud into 50–150 named topic clusters that seed `wiki/topics/`. Deterministic UMAP+HDBSCAN does the partition; one LLM call per cluster names it. Output is a side-car artifact under `Markdown/clustering/` aligned 1:1 with `embeddings/ids.json` — `Markdown/raw/` stays immutable.

## New module: `src/youtubebrain/clusters.py`

Mirrors [embeddings.py](src/youtubebrain/embeddings.py) idioms: constants at top, lazy heavy imports, atomic write, `# @lat:` anchors, sync `main()` CLI entry.

### Constants

```
CLUSTERING_DIR              = Path("Markdown/clustering")
ASSIGNMENTS_JSON_PATH     = CLUSTERING_DIR / "assignments.json"
TOPICS_JSON_PATH          = CLUSTERING_DIR / "topics.json"
META_JSON_PATH            = CLUSTERING_DIR / "meta.json"
MODEL_DIR                 = CLUSTERING_DIR / "bertopic_model"

_DEFAULT_MIN_CLUSTER_SIZE = 50
_MIN_SIZE_ENV             = "CLUSTER_MIN_SIZE"
_UMAP_N_COMPONENTS        = 5
_UMAP_N_NEIGHBORS         = 15
_UMAP_RANDOM_STATE        = 42
_MMR_DIVERSITY            = 0.3
_REPRESENTATIVE_DOCS      = 4
```

### Public API

- `cluster_all() -> int` — load embeddings, fit BERTopic, label, save atomically, return n_clusters.
- `load_existing() -> tuple[list[int], list[TopicInfo], dict]` — read back the trio; mismatched lengths → empty + warning (mirrors embeddings).
- `save_atomic(assignments, topics, meta) -> None` — `*.tmp` + `os.replace` in order assignments → topics → meta → model dir.
- `main()` — CLI entry for `uv run cluster`.

### Internal helpers

- `_min_cluster_size()` — read `CLUSTER_MIN_SIZE` (int, default 50).
- `_build_umap(n_components=5, n_neighbors=15, random_state=42)` — lazy `from umap import UMAP`.
- `_build_hdbscan(min_cluster_size)` — lazy `from hdbscan import HDBSCAN`, `metric="euclidean"`, `cluster_selection_method="eom"`, `prediction_data=True`.
- `_build_topic_model(umap_model, hdbscan_model, representation_model)` — lazy `from bertopic import BERTopic`; `calculate_probabilities=False`.
- `_PydanticAILabeler` — subclass of `bertopic.representation.BaseRepresentation`; in `extract_topics(topic_model, documents, c_tf_idf, topics)` it calls the labelling agent per cluster via `asyncio.run`. Skips cluster `-1`. Falls back to top-keyword string label on agent error.
- `_build_label_agent()` — `Agent(create_model(), output_type=TopicLabel, system_prompt=_LABEL_PROMPT, retries=3)` reusing [provider.create_model](src/youtubebrain/provider.py).

### Output schema

`TopicInfo` and `TopicLabel` as `pydantic.BaseModel`:

```
class TopicLabel(BaseModel):
    label: str        # kebab-case, 3–6 words, e.g. "rust-async-runtime"
    description: str  # 3 sentences

class TopicInfo(BaseModel):
    cluster_id: int
    count: int
    label: str            # falls back to "topic-<id>-<top-keyword>" if LLM failed
    description: str      # falls back to top-keyword string
    keywords: list[str]   # c-TF-IDF top-10 from BERTopic
    representative_ids: list[str]  # video ids, MMR-diversified, len <= _REPRESENTATIVE_DOCS
```

Output files:

- `assignments.json` — JSON list, length == `len(ids.json)` from embeddings, each entry an int cluster id (`-1` = outlier).
- `topics.json` — JSON list of `TopicInfo`, one entry per cluster, including cluster `-1` if any outliers (with `label="outliers"`, no LLM call).
- `meta.json` — `{embedding_model, embedding_dim, n_videos, n_clusters, n_outliers, min_cluster_size, llm_provider, llm_model, bertopic_version, updated_at}`.
- `bertopic_model/` — `topic_model.save(MODEL_DIR, serialization="safetensors")`; safe to re-run, replaced atomically by writing to `MODEL_DIR.tmp` then `os.replace`.

### Worker flow (`cluster_all`)

1. `arr, ids, emb_meta = embeddings.load_existing()`; raise `ValueError("no embeddings to cluster")` when `len(ids) == 0`.
2. Refuse when `len(ids) < 2 * min_cluster_size` (cluster math degenerate). Raise with hint to lower `CLUSTER_MIN_SIZE`.
3. Reload raw texts (title + summary/description) for representative-doc context, via the existing [embeddings.parse_raw_markdown](src/youtubebrain/embeddings.py) + [embeddings.compose_text](src/youtubebrain/embeddings.py) — keyed by id, aligned to `ids`.
4. Build pipeline; `topic_model.fit_transform(documents=texts, embeddings=arr)` → `topics: list[int]`.
5. After fit: pull `topic_model.get_topic_info()` → keywords + sizes; `topic_model.get_representative_docs(topic_id)` → representative texts → map back to ids.
6. The `_PydanticAILabeler` ran during `fit_transform`, so `topic_model.get_topic_info()` already has `Name`/`Representation` populated; map those to `TopicLabel`. (Outliers `-1` skipped.)
7. Build `assignments` (list[int] aligned to `ids`) and `topics` (list[TopicInfo]).
8. `save_atomic(...)`. Log `n_clusters`, `n_outliers`.

### LLM labelling prompt

System prompt: "You name a cluster of YouTube videos. Given top keywords and the 4 most representative titles+summaries, return a 3–6 word kebab-case label and a 3-sentence description suitable as a wiki topic page intro." Strict structured output via `output_type=TopicLabel`.

User prompt template:

```
KEYWORDS: {", ".join(keywords[:10])}

REPRESENTATIVE VIDEOS:
1. {title_summary_excerpt_1}
2. ...

Return TopicLabel.
```

Per-cluster, runs once during BERTopic fit, ~10K tokens × ~100 clusters ≈ $1–$3 on a cheap model.

### CLI

Add to [pyproject.toml](pyproject.toml) `[project.scripts]`:

```
cluster = "youtubebrain.clusters:main"
```

Run order: `uv run ingest` → `uv run transcripts` → `uv run summaries` → `uv run embed` → `uv run cluster`.

### Env vars

- `PROVIDER`, `MODEL` — already wired through [provider.create_model](src/youtubebrain/provider.py); labelling uses whatever is configured. Recommend cheap (`MODEL=qwen3:8b` or `claude-haiku-4-5-20251001` via openrouter) for this step.
- `CLUSTER_MIN_SIZE` — int, default `50`. Tune for 50–150 clusters; halve if `n_clusters < 50`, double if `> 150`.

### Re-cluster policy

Each `uv run cluster` is a full retrain (deterministic via `random_state=42`, but LLM labels may vary). Cluster ids are NOT stable across runs — downstream consumers must read `topics.json` for human-readable label, not int id. Existing artifacts overwritten atomically; abort on dim-mismatch between embeddings/`meta.json` and current store before writing anything.

## Dependencies

Add to [pyproject.toml](pyproject.toml) `[project.dependencies]`:

```
"bertopic>=0.16,<0.18",
"umap-learn>=0.5",
"hdbscan>=0.8",
```

Pulls `numba` + `scikit-learn` transitively. All heavy; lazy-imported inside the build helpers so unit tests stay offline.

## Tests: `tests/test_clusters.py`

Stub everything BERTopic-shaped; never import the real library in default test runs. Pattern: see [tests/test_embeddings.py](tests/test_embeddings.py).

Test cases (each `# @lat:` anchored to a leaf in `lat.md/clusters.md`):

1. `save_atomic` + `load_existing` round-trip preserves assignments / topics / meta.
2. Saved `assignments.json` length matches `len(ids.json)` from a fixture embeddings store.
3. `meta.json` records `n_clusters`, `n_outliers`, `min_cluster_size`, `llm_model`.
4. `CLUSTER_MIN_SIZE` env override is forwarded to `_build_hdbscan`.
5. `cluster_all` with stubbed `_build_topic_model` produces aligned assignments and one `TopicInfo` per cluster.
6. Refuses when `Markdown/embeddings/` is empty (`ValueError("no embeddings")`).
7. Refuses when `len(ids) < 2 * min_cluster_size` with actionable message.
8. Outlier cluster (`-1`) gets a synthetic `TopicInfo` with `label="outliers"` and no LLM call.
9. `_PydanticAILabeler` falls back to `topic-<id>-<top-keyword>` on agent exception.
10. Atomic write leaves no `*.tmp` siblings if save crashes mid-write.
11. `@pytest.mark.slow_embedding` smoke test: real BERTopic on 100 synthetic 384-d vectors, asserts `n_clusters > 0`. Skipped by default (existing `addopts` filter covers it; add a `slow_clustering` marker if a separate gate is preferred).

## Docs: `lat.md/clusters.md`

New file, frontmatter `lat: require-code-mention: true`. Sections (each with leading paragraph ≤250 chars):

```
Clusters
  CLI entry
  Storage layout
  Pipeline components
  Representative-doc plumbing
  LLM labeller
  Re-cluster policy
  Env vars
  Tests
    (one leaf per pytest case above)
```

Add `[[clusters]]` link to [lat.md/lat.md](lat.md/lat.md). Run `lat check` before reporting done.

## Files touched

- new: [src/youtubebrain/clusters.py](src/youtubebrain/clusters.py)
- new: [tests/test_clusters.py](tests/test_clusters.py)
- new: [lat.md/clusters.md](lat.md/clusters.md)
- edit: [pyproject.toml](pyproject.toml) — deps + script
- edit: [lat.md/lat.md](lat.md/lat.md) — `[[clusters]]` index entry

## Verification

```bash
uv sync                                           # pulls bertopic/umap/hdbscan
uv run ruff format . && uv run ruff check --fix .
uv run pyright .
uv run pytest -n auto                             # default filter excludes slow_*
uv run pytest -m slow_embedding tests/test_clusters.py  # real BERTopic smoke
uv run cluster                                    # end-to-end on real Markdown/embeddings/
ls Markdown/clustering/                             # assignments.json topics.json meta.json bertopic_model/
jq 'length' Markdown/clustering/assignments.json    # equals len(Markdown/embeddings/ids.json)
jq 'length' Markdown/clustering/topics.json         # 50–150 (excluding outliers)
lat check                                         # all @lat refs resolve
```

## Confirmed choices

- Cluster ids: not stable across runs; full retrain each `uv run cluster`.
- Labelling model: reuse `PROVIDER`/`MODEL`; user sets cheap model at cluster time if desired.
- Persist `bertopic_model/` to disk to enable `partial_fit` in Phase 5.
- Include outlier cluster (`-1`) in `topics.json` with synthetic `TopicInfo`, no LLM call.
