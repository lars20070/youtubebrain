# BERTopic clustering + labelling

## Context

Phase 1 / point 2 of YouTube-wiki plan. Embeddings already produced in `Markdown/embeddings/{embeddings.npy,ids.json,meta.json}` via [src/youtubebrain/embeddings.py](src/youtubebrain/embeddings.py): 384-d, `float32`, L2-normalised, row-aligned to `ids.json`. Next stage: deterministic UMAP+HDBSCAN+c-TF-IDF over those vectors → named clusters under `Markdown/clustering/`. `Markdown/raw/` stays immutable.

Scale: current corpus is 517 (test sample), **target is 10K+**. Pipeline must work without code change at both scales. Hand-tuning `min_cluster_size` per corpus is acceptable for the LLM labels but the default must give a sensible cluster count at any `n`.

Labelling decoupled from BERTopic fit: deterministic representations only (KeyBERTInspired + MMR) during `fit_transform`; LLM labels run in a second pass via pydantic-ai with `asyncio.gather` + semaphore. Simpler, retryable without re-clustering, easy to stub in tests.

## Scale: 517 → 10K+

| n_videos | min_cluster_size (√n heuristic) | expected clusters | UMAP + HDBSCAN fit | LLM labels @ sem=4 |
|---|---|---|---|---|
| 517    | 23  | 15–30   | ~5 s   | ~10 s  |
| 2 000  | 45  | 30–60   | ~10 s  | ~30 s  |
| 10 000 | 100 | 60–150  | ~60 s  | ~60 s  |
| 20 000 | 141 | 100–200 | ~3 min | ~2 min |

Heuristic: **`_DEFAULT_MIN_CLUSTER_SIZE = max(_MIN_SIZE_FLOOR, round(sqrt(n_videos)))`** computed at run time. `CLUSTER_MIN_SIZE` env var overrides. Phase 5 incremental (`partial_fit` on new arrivals) needs only the saved BERTopic model; no extra code in this stage.

## New module: `src/youtubebrain/clusters.py`

Mirrors [embeddings.py](src/youtubebrain/embeddings.py): constants at top, lazy heavy imports, atomic writes, `# @lat:` anchors, sync `main()` CLI.

### Constants

```python
CLUSTERING_DIR        = Path("Markdown/clustering")
ASSIGNMENTS_JSON_PATH = CLUSTERING_DIR / "assignments.json"
TOPICS_JSON_PATH      = CLUSTERING_DIR / "topics.json"
META_JSON_PATH        = CLUSTERING_DIR / "meta.json"
MODEL_DIR             = CLUSTERING_DIR / "bertopic_model"

_MIN_SIZE_FLOOR           = 10            # heuristic floor; raised by sqrt(n)
_MIN_SIZE_ENV             = "CLUSTER_MIN_SIZE"
_UMAP_N_COMPONENTS        = 5
_UMAP_N_NEIGHBORS         = 15
_UMAP_MIN_DIST            = 0.0
_UMAP_RANDOM_STATE        = 42
_HDBSCAN_MIN_SAMPLES      = 5
_MMR_DIVERSITY            = 0.3
_REPRESENTATIVE_DOCS      = 4
_AGENT_RETRIES            = 3
_DEFAULT_LABEL_CONCURRENCY = 4
_LABEL_CONCURRENCY_ENV    = "LABEL_CONCURRENCY"
_REP_TEXT_CHAR_BUDGET     = 600           # per representative doc in the LLM prompt
```

### Public API

- `cluster_all() -> int` — load embeddings, fit, label, save atomically. Returns `n_clusters` (excl. outliers).
- `load_existing() -> tuple[list[int], list[TopicInfo], dict]` — read trio; mismatched lengths → empty + warning (mirrors embeddings).
- `save_atomic(assignments, topics, meta, topic_model) -> None` — `*.tmp` + `os.replace`; order: assignments → topics → meta → model dir (`rmtree` old + `os.replace` tmp dir).
- `main()` — CLI entry for `uv run cluster`.

### Internal helpers

- `_resolve_min_cluster_size(n_videos) -> int` — env override → fall back to `max(_MIN_SIZE_FLOOR, round(sqrt(n_videos)))`.
- `_label_concurrency() -> int` — env override `LABEL_CONCURRENCY`, default 4.
- `_build_umap()` — lazy `from umap import UMAP`, `metric="cosine"`, constants applied.
- `_build_hdbscan(min_cluster_size)` — lazy `from hdbscan import HDBSCAN`; `metric="euclidean"` (UMAP output is no longer cosine-meaningful), `cluster_selection_method="eom"`, `prediction_data=True` (so Phase 5 `partial_fit` is unblocked).
- `_build_topic_model(umap_model, hdbscan_model)` — lazy `from bertopic import BERTopic`; representation `{"Main": [KeyBERTInspired(), MaximalMarginalRelevance(diversity=_MMR_DIVERSITY)]}`; `calculate_probabilities=False`, `verbose=True`.
- `_build_label_agent()` — lazy pydantic-ai import; `Agent(create_model(), output_type=TopicLabel, system_prompt=_LABEL_SYSTEM_PROMPT, retries=_AGENT_RETRIES)`. Reuses [provider.create_model](src/youtubebrain/provider.py).
- `_load_texts_by_id(ids)` — walk `Markdown/raw/` once, reuse [embeddings.parse_raw_markdown](src/youtubebrain/embeddings.py) + [embeddings.compose_text](src/youtubebrain/embeddings.py); return `dict[str, str]`. Missing id → log + skip (cluster step doesn't fail on this; the row keeps its assignment but uses keywords-only fallback for labelling).
- `_label_clusters(infos, texts_by_id, agent, concurrency) -> list[TopicLabel | None]` — async; `asyncio.Semaphore(concurrency)`; per cluster build user prompt from keywords + truncated rep doc texts; on agent error log + return `None` → caller applies `_fallback_label`.
- `_fallback_label(cluster_id, keywords) -> TopicLabel` — `label=f"topic-{cluster_id}-{(keywords[0] if keywords else 'unknown')}"`, `description=", ".join(keywords[:5])`.

### Output schema

```python
class TopicLabel(BaseModel):
    label: str        # kebab-case, 3–6 words
    description: str  # 3 sentences, ≤500 chars, plain prose

class TopicInfo(BaseModel):
    cluster_id: int                # -1 reserved for outliers
    count: int
    label: str
    description: str
    keywords: list[str]            # c-TF-IDF top-10
    representative_ids: list[str]  # MMR-diversified, len ≤ _REPRESENTATIVE_DOCS
```

### Output files

- `assignments.json` — JSON list, `len == len(ids.json)`, entries are int cluster ids; `-1` = outlier. Row-aligned to `Markdown/embeddings/ids.json`.
- `topics.json` — JSON list of `TopicInfo`. Includes outlier cluster `-1` with synthetic `TopicInfo(label="outliers", description="Videos that did not fit any cluster.", keywords=[], representative_ids=[])` when any outliers present. No LLM call for `-1`.
- `meta.json` — `{embedding_model, embedding_dim, n_videos, n_clusters, n_outliers, min_cluster_size, umap_random_state, llm_provider, llm_model, label_concurrency, bertopic_version, updated_at}`.
- `bertopic_model/` — `topic_model.save(MODEL_DIR.tmp, serialization="safetensors", save_ctfidf=True, save_embedding_model=False)`; then `shutil.rmtree(MODEL_DIR, ignore_errors=True)` + `os.replace(MODEL_DIR.tmp, MODEL_DIR)`. Enables Phase 5 incremental `partial_fit`. Will grow to ~tens of MB at 10K; add to `.gitignore` alongside `Markdown/embeddings/` (already ignored per [.env.example](.env.example)).

### Worker flow (`cluster_all`)

1. `arr, ids, emb_meta = embeddings.load_existing()`. Raise `ValueError("no embeddings to cluster — run `uv run embed`")` when `len(ids) == 0`.
2. `min_size = _resolve_min_cluster_size(len(ids))`. Refuse when `len(ids) < 2 * min_size` with msg suggesting lower `CLUSTER_MIN_SIZE`.
3. `texts_by_id = _load_texts_by_id(ids)`; `texts = [texts_by_id.get(i, "") for i in ids]` — order matches `arr` rows.
4. Build pipeline. `topics_assignments, _probs = topic_model.fit_transform(documents=texts, embeddings=arr)`.
5. For each cluster id in `topic_model.get_topic_info()`:
   - `keywords = [w for w, _ in topic_model.get_topic(cluster_id)]` (top-10).
   - `rep_docs = topic_model.get_representative_docs(cluster_id)` → map text → id via inverse dict built once before fit. Outliers (`-1`) get an empty list.
6. `asyncio.run(_label_clusters(...))` → list of `TopicLabel | None`; per cluster build `TopicInfo` using label-or-fallback. `-1` gets the synthetic outlier `TopicInfo` (no LLM call).
7. `save_atomic(...)`. Log `n_clusters`, `n_outliers`, `min_size`, `llm_model`.

### LLM labelling prompt

System (reused every cluster):

```
You name a cluster of YouTube videos. Given top keywords and 4 representative title+summary excerpts, return:
- label: 3–6 words, kebab-case, lowercase ASCII (e.g. "rust-async-runtime")
- description: 3 sentences suitable as a wiki topic page intro, plain prose, no markdown

Return strictly a TopicLabel.
```

User template (per cluster):

```
KEYWORDS: {", ".join(keywords[:10])}

REPRESENTATIVE VIDEOS:
1. {rep_text_1[:_REP_TEXT_CHAR_BUDGET]}
2. ...
```

Cost at 10K corpus: ~100 clusters × ~3K tokens in / ~150 out × cheap model (`qwen3:8b` local free; `claude-haiku-4-5-20251001` via OpenRouter ≈ <$0.50).

### CLI

[pyproject.toml](pyproject.toml) `[project.scripts]` add:

```
cluster = "youtubebrain.clusters:main"
```

Pipeline order: `uv run ingest` → `uv run transcripts` → `uv run summaries` → `uv run embed` → `uv run cluster`.

### Env vars

- `PROVIDER`, `MODEL` — existing, via [provider.create_model](src/youtubebrain/provider.py).
- `CLUSTER_MIN_SIZE` — int; overrides `√n` heuristic.
- `LABEL_CONCURRENCY` — int, default 4. Bump for cloud providers (8–16). Keep low (1–2) for local Ollama.

### Re-cluster policy

Full retrain each `uv run cluster` — UMAP geometry deterministic via `_UMAP_RANDOM_STATE=42`, but HDBSCAN labels clusters by discovery order, so **cluster ids are NOT stable across runs**. LLM labels are non-deterministic too. Downstream consumers MUST key off `label` (human-readable, kebab-case), not `cluster_id`. Existing artifacts overwritten atomically; refuse to write if `embeddings/meta.json.model` differs from prior `clustering/meta.json.embedding_model` — old labels are no longer comparable.

## Dependencies

[pyproject.toml](pyproject.toml) `[project.dependencies]` add:

```
"bertopic>=0.16,<0.18",
"umap-learn>=0.5",
"hdbscan>=0.8",
```

Transitively pulls `numba`, `scikit-learn`. All heavy — lazy-imported inside build helpers so `pytest -n auto` stays offline & fast.

## Tests: `tests/test_clusters.py`

Stub everything BERTopic-shaped — never import the real library in the default test run. Mirror [tests/test_embeddings.py](tests/test_embeddings.py) `_patch_store` + `_StubEncoder` patterns. `monkeypatch.setattr` on `clusters._build_topic_model` and `clusters._build_label_agent`.

`# @lat:`-anchored, one per leaf in `lat.md/clusters.md`:

1. `save_atomic` + `load_existing` round-trip preserves assignments / topics / meta.
2. `len(assignments) == len(ids.json)` from a fixture embeddings store.
3. `meta.json` records `n_clusters`, `n_outliers`, `min_cluster_size`, `llm_model`, `bertopic_version`.
4. `CLUSTER_MIN_SIZE` env override beats the `√n` heuristic.
5. **`_resolve_min_cluster_size`** heuristic returns `floor` at small `n`, `round(√n)` above the floor (table-driven: 100→10, 517→23, 10000→100).
6. `cluster_all` with stubbed topic model + stubbed agent produces aligned assignments and one `TopicInfo` per cluster.
7. Refuses when `Markdown/embeddings/` empty → `ValueError("no embeddings")`.
8. Refuses when `len(ids) < 2 * min_cluster_size` with actionable message.
9. Outlier cluster `-1` gets synthetic `TopicInfo(label="outliers")`, no LLM call.
10. Agent exception on one cluster → `_fallback_label` applied; other clusters unaffected.
11. Refuses when `embeddings/meta.json.model` differs from existing `clustering/meta.json.embedding_model` (no overwrite).
12. Atomic write leaves no `*.tmp` siblings if a mid-write step raises.
13. `@pytest.mark.slow_clustering` smoke test: real BERTopic on 200 synthetic 384-d gaussian-blob vectors, asserts `n_clusters >= 2`. Skipped by default — add `slow_clustering` to `pytest.ini_options.addopts` filter and to `markers`.

## Docs: `lat.md/clusters.md`

New file. Frontmatter `lat: require-code-mention: true`. Section tree (each ≥1-sentence ≤250-char leading paragraph):

```
Clusters
  CLI entry
  Storage layout
  Pipeline components
  Min-cluster-size heuristic
  Representative-doc plumbing
  LLM labeller
  Re-cluster policy
  Env vars
  Tests
    (one leaf per pytest case 1–13 above)
```

Add `[[clusters]]` bullet to [lat.md/lat.md](lat.md/lat.md) under `[[embeddings]]`. Run `lat check` before reporting done.

## Files touched

- new: [src/youtubebrain/clusters.py](src/youtubebrain/clusters.py)
- new: [tests/test_clusters.py](tests/test_clusters.py)
- new: [lat.md/clusters.md](lat.md/clusters.md)
- edit: [pyproject.toml](pyproject.toml) — 3 deps, 1 script, 1 marker, addopts filter
- edit: [lat.md/lat.md](lat.md/lat.md) — `[[clusters]]` index entry
- edit: [.gitignore](.gitignore) — `Markdown/clustering/bertopic_model/`

## Verification

```bash
uv sync                                              # pulls bertopic / umap-learn / hdbscan
uv run ruff format . && uv run ruff check --fix .
uv run pyright .
uv run pytest -n auto                                # default filter excludes slow_*
uv run pytest -m slow_clustering tests/test_clusters.py
uv run cluster                                       # end-to-end on current Markdown/embeddings/
ls Markdown/clustering/                              # assignments.json topics.json meta.json bertopic_model/
jq 'length' Markdown/clustering/assignments.json     # == len(ids.json)
jq 'length' Markdown/clustering/topics.json          # heuristic-driven, incl. outliers
jq '.n_clusters, .n_outliers, .min_cluster_size' Markdown/clustering/meta.json
lat check
```

Tuning: if outlier share > 30% or cluster count outside the heuristic table band, bump or drop `CLUSTER_MIN_SIZE` and re-run. No code change needed.

## Confirmed choices

- Cluster ids not stable across runs — consumers key off `label`.
- Labelling reuses `PROVIDER`/`MODEL`; user sets cheap model at cluster time.
- BERTopic model persisted with `prediction_data=True` for future Phase 5 `partial_fit`.
- Outlier cluster `-1` included in `topics.json`, synthetic `TopicInfo`, no LLM call.
- LLM labelling decoupled from BERTopic fit (post-fit `asyncio.gather` + semaphore).
- `min_cluster_size` defaults to `max(10, round(√n))` — scales 517 → 10K+ without code change.
- Refuse-to-overwrite when `embedding_model` changed since last cluster run.

## Open questions

1. Pin the labelling model to one provider (cheap cloud) regardless of global `PROVIDER`/`MODEL`, or piggyback on whatever the user has set?
2. Bump default `_AGENT_CONCURRENCY` from 4 → 8 once corpus crosses ~5K (more clusters, more headroom in cloud rate limits)?
3. Persist the `texts_by_id` snapshot to `Markdown/clustering/documents.json` for label-only reruns (skip BERTopic fit), or recompute each run?
4. At 10K, expose `--sample N` flag on `uv run cluster` to subsample for fast schema iteration, or rely solely on the env var?
