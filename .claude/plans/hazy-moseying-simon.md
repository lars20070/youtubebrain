# Consolidate the youtubebrain pipeline

## Context

`lat.md/overview.md` documents a 6-step run order across 5 separate `uv run` CLIs (`ingest → transcripts → summaries → ingest (again!) → embed → cluster`). The double-ingest is a wart: `ingest` must re-run to fold cached transcripts + summaries into `Markdown/raw/<id>.md` (the only mechanism that propagates them). Underneath:

- **3 storage formats** for per-video state: JSON (`descriptions.json`), SQLite (`transcripts.sqlite`, `summaries.sqlite`), markdown frontmatter (`raw/<id>.md`). Plus numpy/JSON for embeddings and JSON+pickle for cluster artefacts.
- **~110 LOC dup** between [src/youtubebrain/transcripts.py](src/youtubebrain/transcripts.py) and [src/youtubebrain/summaries.py](src/youtubebrain/summaries.py): `init_db` (~28 LOC), `enqueue` (12 LOC exact copy), `load_*` (24 LOC exact copy except table name).
- **3 independent atomic-write helpers** ([descriptions.py:34-39](src/youtubebrain/descriptions.py#L34-L39), [embeddings.py:150-165](src/youtubebrain/embeddings.py#L150-L165), [clusters.py:272-306](src/youtubebrain/clusters.py#L272-L306)).
- **`load_dotenv` called 11+ times**; env-var reads scattered across 5 modules; no central config schema.
- **No orchestrator** — user must remember the 6-step order and the double-ingest.
- **`__init__.py` only exports `logger`** — small public API, refactor-friendly.
- **Tests bind tightly to SQLite schema**: 21 direct row assertions in [tests/test_transcripts.py](tests/test_transcripts.py), 8 in [tests/test_summaries.py](tests/test_summaries.py); ingest/embed/cluster tests use higher-level interfaces.
- **116 `@lat:` bindings** across 7 test files anchor lat.md sections to test code; any schema rename ripples through both.
- **Zero external callers** outside the package.

Goal: reduce cognitive load (fewer surfaces, fewer formats, no double-ingest) and reduce duplication, without throwing away the resumable overnight-run guarantees the project depends on.

## Two routes

### Plan A — Database-centric (`videos.sqlite` is the source of truth)

One SQLite DB at `state/videos.sqlite` (or similar) with one row per video id and columns spanning all stages:

```
videos(
  id PK,
  title, channel_id, channel_name, watch_time, title_url,
  description, description_fetched_at,
  transcript_status, transcript_text, transcript_lang, transcript_source, transcript_raw_json,
  summary_status, summary_text, summary_model,
  embedding_status, embedded_at,
  cluster_id, cluster_label,
  attempts_*, last_attempt_*, errors_*
)
```

- `Markdown/raw/<id>.md` becomes a **rendered view** produced by a new `render` subcommand at the end of the pipeline. Not a source of truth — never read back.
- Embeddings stay as numpy (right format for vectors); cluster artefacts stay as JSON + `bertopic_model/`. Both keyed off ids from the DB.
- One CLI `youtubebrain` with subcommands: `ingest`, `fetch-descriptions`, `fetch-transcripts`, `fetch-summaries`, `embed`, `cluster`, `render`, `pipeline`.
- `pipeline` runs the canonical sequence end-to-end; double-ingest is gone (render is one step, runs last).

### Plan B — CLI-centric (single entry point, unify helpers, keep stores)

Keep the file layout, but flatten the user-facing surface:

- One CLI `youtubebrain` (Click or `argparse`) with subcommands matching today's tools, **plus** a new `pipeline` subcommand that runs the 6-step sequence internally — user only ever runs `uv run youtubebrain pipeline`.
- Extract `src/youtubebrain/storage.py` (or `cache.py`) holding parameterised `init_db(db_path, schema)`, `enqueue(...)`, `load_text(...)` — collapses the ~110 LOC dup between `transcripts.py` and `summaries.py`.
- Extract `src/youtubebrain/atomic.py` with one `atomic_write_text` + `atomic_write_bytes` helper, replacing the 3 hand-rolled versions.
- Extract `src/youtubebrain/config.py` (pydantic-settings model) for all env vars: `API_KEY_YOUTUBE`, `PROVIDER`, `MODEL`, `EMBEDDING_MODEL`, `CLUSTER_MIN_SIZE`, `TRANSCRIPTS_SLEEP_MIN`/`MAX`, `LABEL_CONCURRENCY`. Single `load_dotenv()` at CLI entry.
- Keep raw markdown as both source-of-truth-for-embed AND ingest output; keep the second ingest pass — but hide it behind `pipeline` so the user never types it twice.

## Pros and cons

| Dimension                          | Plan A (DB-first)                                              | Plan B (CLI-first)                                           |
| ---------------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------ |
| Eliminates double-ingest           | ✅ Render is the last step, runs once                          | ⚠️ Hidden behind `pipeline`, still exists internally          |
| Single source of truth             | ✅ One SQLite file                                             | ❌ Still 3 formats per-video + 2 derived stores              |
| LOC eliminated                     | ✅ Largest — collapses transcripts/summaries schemas + caches   | ✅ Real — atomic helpers, env reads, db helpers              |
| Test-rewrite cost                  | ❌ High — 29 direct SQL asserts + ~33 `@lat:` bindings to retarget | ✅ Low — mostly additive; CLI flag tests + helper unit tests |
| lat.md churn                       | ❌ Heavy — `transcripts`, `summaries`, `descriptions`, `ingest` schema sections rewritten; new `videos` section | ✅ Light — additive `[[cli]]` and `[[storage]]` sections      |
| Backward-compat of existing caches | ❌ None — one-shot migration script needed                     | ✅ Caches keep their format; users keep their data            |
| Risk of regression                 | ❌ High — touches every fetcher, render, and cluster path      | ✅ Low — changes are mostly extractions + a new entry point  |
| User-facing simplicity             | ✅ One command, one DB file to inspect                         | ✅ One command, but several files behind it                  |
| Inspectability of partial state    | ✅ `sqlite3 videos.sqlite` shows everything                    | ⚠️ Spread across JSON + 2 sqlite + markdown                  |
| Time estimate                      | ❌ ~2 weeks                                                    | ✅ ~3 days                                                   |
| Locks in current model             | ✅ No — actively normalises                                    | ⚠️ Yes — multi-store stays                                   |
| Path to the other plan later       | N/A                                                            | ✅ Plan A can still be done incrementally on top of B        |

## Recommendation: Plan B

Reasons, in order:
1. **Cost/benefit**: Plan B captures ~80% of the readability gain (single CLI, no double-ingest in the user's workflow, duplication gone) for ~20% of the work and risk.
2. **Test surface**: Plan A invalidates 29 direct SQL assertions and ~33 `@lat:` bindings; Plan B's churn is mostly additive (new helpers, new CLI entry).
3. **Resumability**: The SQLite caches already give Plan B's overnight fetchers crash-safety; rebuilding that on a unified schema (Plan A) is busywork for no functional gain.
4. **Single-dev project**: There are zero external callers. The benefit of a normalised `videos.sqlite` is mostly aesthetic until the project grows.
5. **B → A is an open door**: After the helpers are extracted and the CLI is unified, migrating to a single DB is mostly a schema design + render-step swap. The opposite direction (A → B if A turns out to be over-engineered) is much harder.

## Plan B execution outline

Files to add:
- `src/youtubebrain/cli.py` — Click root command with subcommands `ingest`, `transcripts`, `summaries`, `embed`, `cluster`, `pipeline`. Wraps existing `main()` functions.
- `src/youtubebrain/config.py` — `pydantic_settings.BaseSettings` model, single `settings = Settings()` instance. Single `load_dotenv()` at module import.
- `src/youtubebrain/storage.py` — `init_db(path, table, schema, index_col)`, `enqueue(path, table, ids)`, `load_text(path, table, ids)`. Reused by `transcripts.py` and `summaries.py`.
- `src/youtubebrain/atomic.py` — `atomic_write_text(path, text)`, `atomic_write_bytes(path, data)`. Replaces 3 hand-rolled versions.

Files to modify:
- [pyproject.toml](pyproject.toml) — replace the 5 entries under `[project.scripts]` with a single `youtubebrain = "youtubebrain.cli:main"`. Keep the old entries as deprecation shims for 1 release if desired (or drop — no external callers).
- [src/youtubebrain/transcripts.py](src/youtubebrain/transcripts.py) — replace `init_db`/`enqueue`/`load_transcripts` bodies with calls into `storage.py`.
- [src/youtubebrain/summaries.py](src/youtubebrain/summaries.py) — same.
- [src/youtubebrain/descriptions.py](src/youtubebrain/descriptions.py), [embeddings.py](src/youtubebrain/embeddings.py), [clusters.py](src/youtubebrain/clusters.py) — atomic-write call sites swap to `atomic.atomic_write_*`.
- Every module's env-var read points at `config.settings.*`.
- `cli.py:pipeline` runs: `ingest → transcripts → summaries → ingest → embed → cluster`. The double-ingest stays in code, disappears from the user surface.

lat.md updates (the post-refactor work):
- New section `lat.md/cli.md` documenting the subcommand surface and `pipeline` orchestration.
- New section `lat.md/storage.md` documenting the shared `init_db`/`enqueue`/`load_text` contract.
- New section `lat.md/atomic.md` documenting the shared write helper.
- New section `lat.md/config.md` documenting the pydantic-settings model.
- [overview.md#Run order](lat.md/overview.md): replace the 6-step list with `uv run youtubebrain pipeline` + an "internal sequence" sub-bullet for contributors.
- [clusters.md#CLI entry](lat.md/clusters.md), [transcripts.md#CLI entry](lat.md/transcripts.md), [summaries.md#CLI entry](lat.md/summaries.md), [ingest.md#…](lat.md/ingest.md), [embeddings.md#CLI entry](lat.md/embeddings.md): each rewritten to point at `youtubebrain <subcommand>`.
- Append a `# @lat:` from each new subcommand handler in `cli.py` to the corresponding `lat.md/<x>.md#CLI entry` section.

## PR strategy

Single PR — all of Plan B lands together (`atomic.py`, `storage.py`, `config.py`, `cli.py`, callsite swaps, `[project.scripts]` change, lat.md retargets). Trade-off: harder to bisect a regression, but the refactor is internally cohesive and the lat.md retargets only make sense once the new modules exist.

## Verification

1. `uv run ruff format . && uv run ruff check --fix . && uv run pyright .` — clean.
2. `uv run pytest -n auto -m "not paid and not ollama and not slow_embedding and not slow_clustering"` — all 121+ existing tests pass.
3. New unit tests:
   - `tests/test_cli.py` — invokes `youtubebrain --help` and each subcommand `--help` via Click's `CliRunner`; checks `pipeline` calls each stage in order via monkeypatched stubs.
   - `tests/test_storage.py` — exercises `init_db`/`enqueue`/`load_text` with a synthetic table, asserting parity with the pre-refactor transcripts and summaries assertions.
   - `tests/test_atomic.py` — atomic write semantics; mid-write crash leaves no `.tmp` siblings; existing file untouched.
   - `tests/test_config.py` — env-var precedence (env > .env > default), validation errors on bad numeric.
4. End-to-end smoke (requires real Takeout export, manual): `uv run youtubebrain pipeline` against a 5–10 video fixture export; verify `Markdown/raw/<id>.md` has Summary + Transcript sections populated and `Markdown/wiki/topics/<slug>/<slug>.md` exists.
5. `lat check` — passes after all lat.md sections are added + retargeted.

## Unresolved questions

- Click vs `argparse` for the CLI? Click gives `--help` cleanly and `CliRunner` for tests; `argparse` adds zero deps. Lean Click.
- Keep the old `[project.scripts]` entries as shims, or remove? Zero external callers → safe to remove. Confirm before deletion.
- Should `pipeline` accept `--skip-transcripts` / `--skip-summaries` for partial reruns? Probably yes; cheap to add.
- Does `config.py` use `pydantic-settings` (new dep) or a hand-rolled dataclass + `os.environ`? Lean pydantic-settings — already imply pydantic v2 is in use.
