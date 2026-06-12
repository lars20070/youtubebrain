# Refactor: single-run ingest + holistic pipeline cleanup

## Context

`uv run ingest` currently runs twice: pass 1 fetches descriptions (YouTube API → `Markdown/.cache/descriptions.json`, needed by `summaries`) and writes `Markdown/raw/<id>.md` with `_(unavailable)_` placeholders; pass 2 (after `transcripts`/`summaries` workers fill their SQLite caches) folds real text into the markdown. Cause: `ingest.main()` ([ingest.py:126](src/youtubebrain/ingest.py#L126)) mixes three jobs — description fetching, Takeout parsing, markdown compile. Goal: each stage runs once, clear DAG, less tech debt. Backward compat irrelevant.

**User-approved decisions:**
1. New dedicated `uv run descriptions` stage (step 1). Pipeline: `descriptions ∥ transcripts → summaries → markdown → embed → cluster → ./compile-wiki.sh → ./index-wiki.sh`.
2. Rename `ingest` → `markdown` (pure offline compile, no API key, no LLM).
3. Unify all 3 caches on SQLite via shared `cache.py` (`descriptions.sqlite` replaces JSON; no migration — re-fetch once).

**Verified facts:** `markdown.py` module name safe (PyPI `markdown` pkg not in venv; submodule can't shadow top-level anyway). Existing transcripts/summaries sqlite files stay readable (same column names; `CREATE TABLE IF NOT EXISTS` no-ops). `tavily-python` + `pypandoc` unused. CI bug: `pytest -m "not slow"` ([build.yaml:55](.github/workflows/build.yaml#L55)) overrides addopts marker filter → selects paid/ollama tests. `.claude/CLAUDE.md` + `pyproject description` describe a different project (web-research template).

## Target layout

```
src/youtubebrain/
  config.py        NEW  all path constants + load_env() (single dotenv call site)
  takeout.py       NEW  load_watch_history, video_ids, video_id, channel_id, filters (from ingest.py:16-52,117-123)
  cache.py         NEW  StatusCache: shared SQLite status-row cache
  descriptions.py  REWRITTEN  sqlite worker + main()
  transcripts.py   ported onto cache.py + takeout.py
  summaries.py     ported onto cache.py + takeout.py + descriptions.load_descriptions
  markdown.py      NEW  render+parse+compile main() (writer from ingest.py:56-113, parser from embeddings.py:32-141)
  embeddings.py    parser moves out; encoder/store/embed loop stays
  clusters.py      imports parse helpers from markdown.py, paths from config.py
  models.py / provider.py / logger.py  unchanged (provider: dotenv via config.load_env)
  ingest.py        DELETED
```

Acyclic imports: workers → takeout+cache+config; markdown → workers' `load_*` read APIs. Kills late imports at [transcripts.py:500](src/youtubebrain/transcripts.py#L500), [summaries.py:234](src/youtubebrain/summaries.py#L234).

### cache.py design

Per-worker table, shared helpers, declarative extra columns (NOT generic JSON blob — transcripts has queryable `language/is_generated/raw_json/source`, summaries has `model`).

```python
@dataclass(frozen=True)
class StatusCache:
    db_path: Path; table: str; extra_columns: tuple[str, ...] = ()
    def init_db(self) -> None                    # mkdir, WAL, CREATE TABLE IF NOT EXISTS + status index
    def connect(self) -> sqlite3.Connection
    def enqueue(self, video_ids) -> None         # dedupe + INSERT OR IGNORE 'pending'
    def load_ok(self, video_ids) -> dict[str, str | None]   # missing-file→all None; 500-id IN chunks
    def pending_ids(self, con, *, statuses=("pending","error"), max_attempts=5) -> list[str]
    def next_retryable(self, con, ...) -> str | None         # ORDER BY attempts ASC, RANDOM()
    def record_result(self, con, vid, status, *, text=None, error_message=None, extra=None) -> None
    def record_attempt(self, con, vid, status, error_message) -> None   # no fetched_at (blocked-row semantics)
    def counts(self, con) -> tuple[int, int]
```

Base columns: `video_id PK, status, text, error_message, attempts, fetched_at, last_attempt`. Workers keep thin public wrappers (`init_db(db_path=None)`, `enqueue(ids, db_path=None)`, `load_transcripts/...`) so tests change minimally. Statuses: transcripts unchanged; summaries unchanged; descriptions new = `pending/ok/missing/error` (`missing` = API-confirmed absent, terminal; `error` = HTTP failure, retried while attempts < 5).

### descriptions.py

Sync `httpx.Client` (batches already sequential — async buys nothing, drops asyncio plumbing). `main()`: unlink stale `Markdown/.cache/descriptions.json` (+ `.json.tmp`) if present, then `ids = takeout.load_video_ids(); init_db(); enqueue(ids); fetch_descriptions()`. Worker: pending_ids → 50-chunks → `_fetch_batch` (unchanged, [descriptions.py:70](src/youtubebrain/descriptions.py#L70)) → `ok`/`missing` per id; `httpx.HTTPError` → `error` rows, continue. No pending rows → no API key needed. Delete `_load_cache`/`_save_cache`/old async `fetch_descriptions(ids, path)`.

### markdown.py

Section names + `_(unavailable)_` marker as constants; `_SECTION_SPLIT_RE` built from them (writer/parser can't drift). `render_markdown`/`write_markdown` (de-underscored), `read_frontmatter`, `parse_raw_markdown`, `compose_text`, `iter_raw_files`. `main()`: load history, ids, three `load_*` dicts, write all files. Placeholders kept for non-ok rows. Output byte-identical to today (checksum-verifiable).

## Steps (tree green after each: ruff format/check, pyright, pytest -n auto, lat check)

**0. Standalone cleanup** — pyproject: drop `tavily-python`(L16) + `pypandoc`(L19), fix stale `description`(L4), bump `requires-python` to `">=3.12,<3.13"`(L7), drop `src/youtubebrain/cli.py` from coverage omit (L95); `uv lock && uv sync`. build.yaml: delete tavily .env step (L27-30), python matrix `3.11`→`3.12`(L14), L55 → `uv run pytest -v -n auto` (addopts filter applies). Rewrite `.claude/CLAUDE.md` to brief accurate pipeline description (keep dev-commands + rules pointers).

**1. config.py + single dotenv** — create config.py (paths above; `EMBEDDINGS_META_JSON_PATH` vs `CLUSTERING_META_JSON_PATH` disambiguation). Replace all `load_dotenv()` calls (descriptions:52, transcripts:68, embeddings:63, provider:49, clusters:91/109/123) with `config.load_env()`. Path constants → call-time `config.X` lookups (fn defaults become `Path | None = None`) so tests patch one module. conftest `_block_dotenv` shrinks to single `monkeypatch.setattr(config, "load_dotenv", noop)`. Update test patch sites (`test_clusters._patch_stores`:58-82, test_ingest:328/353/371, test_transcripts:450, test_embeddings). lat: re-point `ingest#Default output directory` ref etc.

**2. takeout.py** — create; transcripts/summaries mains use `takeout.load_video_ids()` (kills 3× duplication + cycles). Split `tests/test_ingest.py` → `tests/test_takeout.py` (loader/filter/id tests). test_summaries patch targets → `takeout.load_watch_history`/`takeout.video_id`. lat: new `takeout.md` (sections lifted from ingest.md), prune "lazy import" prose in transcripts.md/summaries.md.

**3. cache.py + port transcripts & summaries** — delete duplicated init_db/enqueue/load (transcripts.py:348-415, summaries.py:94-158); rewrite worker loops on cache API (resolver chain, pacing, blocked backoff untouched). New `tests/test_cache.py` (init idempotent/WAL, enqueue dedupe, load_ok non-ok→None + missing-file + >500 chunking, record_result timestamps/attempts/extra, record_attempt no fetched_at, next_retryable caps). lat: new `cache.md`; re-point transcripts.md/summaries.md schema/enqueue/read-API refs.

**4. descriptions stage** — rewrite descriptions.py; add `descriptions = "youtubebrain.descriptions:main"` script. summaries: `_load_descriptions_cache` (L37-41) → `descriptions.load_descriptions`. ingest.main (interim): `load_descriptions(ids)` instead of `asyncio.run(fetch_descriptions(...))`. Rewrite `tests/test_descriptions.py` (respx, sync; batching, missing→`missing`, per-batch persistence, error rows retryable, key-only-when-pending, main wiring, read API). lat: rewrite descriptions.md (CLI entry, SQLite cache, statuses, Tests).

**5. markdown.py** — create; embeddings.py imports `parse_raw_markdown`/`compose_text`/`iter_raw_files` from it; clusters.py re-points (L221-229, 482-489, 547, 596, 621-623 + `_FRONTMATTER_FENCE`:49). New `tests/test_markdown.py` absorbing render/write/main tests (test_ingest:190-384), 3 "Ingest…" tests (test_transcripts:413-470), parser/compose tests (test_embeddings:86-130); main tests stub three `load_*` APIs. lat: new `markdown.md` (writer + parsing-rules + CLI entry + merged Tests); re-point embeddings.md/clusters.md refs.

**6. kill ingest** — delete `src/youtubebrain/ingest.py`, `tests/test_ingest.py`, `lat.md/ingest.md`; sweep `[[ingest…]]` links (overview.md, transcripts.md, summaries.md, embeddings.md, descriptions.md, lat.md/lat.md). Final scripts:
```toml
descriptions / transcripts / summaries / markdown / embed / cluster
```

**7. docs rewrite** — `lat.md/overview.md`: new run order (1 descriptions ∥ 2 transcripts → 3 summaries → 4 markdown → 5 embed → 6 cluster → 7 compile-wiki → 8 index-wiki), prerequisites table (markdown row: hard = watch-history.json; soft = 3 sqlite caches), mermaid without dotted second-pass edges, stage details per tool. `README.md:26-48`: new command block, drop "ingest is run twice" prose. Check wiki.md "six uv run tools" wording. `.env.example`: regroup vars by stage with the new stage names (`API_KEY_YOUTUBE` → "step 1, uv run descriptions"; `TRANSCRIPTS_SLEEP_*` → step 2; `PROVIDER`/`MODEL` + provider keys → steps 3+6; `EMBEDDING_MODEL` → step 5; `CLUSTER_*`/`LABEL_CONCURRENCY` → step 6); no new vars. `lat check` must pass incl. require-code-mention files (takeout, markdown, cache, descriptions, transcripts, summaries, embeddings, clusters, provider).

### @lat test-spec moves

| Old | New |
|---|---|
| `ingest#Tests#{parse/filter/id-extraction specs}` | `takeout#Tests#…` |
| `ingest#Tests#{render/write/main specs}` | `markdown#Tests#…` |
| `transcripts#Tests#{Ingest markdown…, Ingest transcript unavailable…, Ingest main folds…}` | `markdown#Tests#…` |
| `embeddings#Tests#{Frontmatter parsing, Unavailable…, Compose…×3}` | `markdown#Tests#…` |
| `descriptions#Tests#*` | rewritten sqlite-era set |
| — | new `cache#Tests#*` |

## Verification

```bash
uv lock && uv sync
uv run ruff format . && uv run ruff check --fix .
uv run pyright .
uv run pytest -n auto        # offline per addopts
lat check
```

Offline smoke (no key/LLM/network; local caches already populated):
```bash
shasum Markdown/raw/*.md | shasum   # before
uv run markdown                      # pure compile
shasum Markdown/raw/*.md | shasum   # Summary/Transcript identical; Description regresses to
                                     # _(unavailable)_ until `uv run descriptions` re-fetches (accepted)
uv run embed                         # "nothing to embed" if ids unchanged
```
Then with key: `uv run descriptions && uv run markdown`. Old `descriptions.json` auto-deleted by `descriptions.main()`.

## Resolved decisions (user-confirmed)

1. Bump to Python 3.12: `requires-python = ">=3.12,<3.13"` + CI matrix 3.12 (step 0).
2. `descriptions.main()` auto-deletes stale `descriptions.json` (step 4).
3. `.env.example` regrouped/annotated by new stage names (step 7).

No unresolved questions.
