# Add `## Summary` section via local Ollama LLM

## Context

Per-video markdown files at [Markdown/raw/](Markdown/raw/) currently have `## Description` + `## Transcript`. Add a third section `## Summary` synthesising title + description + transcript via a local Ollama model called from Pydantic AI. Summary must be cached so re-ingests don't re-run the LLM. Ads/boilerplate ignored by prompt instruction (no regex strip).

## Design decisions (locked)

- **Position**: `## Summary` between frontmatter and `## Description`.
- **Cache**: SQLite `Markdown/.cache/summaries.sqlite`, mirroring [transcripts.py](src/youtubebrain/transcripts.py).
- **Ad handling**: system prompt tells LLM to disregard sponsorships/Patreon/memberships/merch/hashtags. No regex.
- **Model**: `SUMMARY_MODEL` env var, default `qwen3:32b` if unset in `.env`.
- **LLM stack**: `pydantic-ai-slim[openai]` → `OllamaModel` + `OllamaProvider(base_url=...)`, base URL from `OLLAMA_BASE_URL` env (default `http://localhost:11434/v1`).
- **Trigger**: new CLI `uv run summaries`. `ingest` reads cache only — never calls LLM.

## Files to create

### `src/youtubebrain/summaries.py` (new)

Mirror [transcripts.py](src/youtubebrain/transcripts.py) structure:

- `SUMMARIES_DB_PATH = Path("Markdown/.cache/summaries.sqlite")`.
- `init_db(path)` — schema:
  ```
  CREATE TABLE summaries (
    video_id      TEXT PRIMARY KEY,
    status        TEXT NOT NULL,   -- 'pending' | 'ok' | 'error' | 'skipped'
    text          TEXT,
    model         TEXT,
    error_message TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    fetched_at    TIMESTAMP,
    last_attempt  TIMESTAMP
  );
  CREATE INDEX idx_summaries_status ON summaries(status);
  ```
- `enqueue(video_ids, db_path)` — INSERT OR IGNORE `'pending'` rows.
- `load_summaries(video_ids, db_path) -> dict[str, str | None]` — read API used by ingest. Returns text only when `status='ok'`.
- `_build_agent() -> Agent[None, str]` — reads `SUMMARY_MODEL` (default `"qwen3:32b"` if unset) + `OLLAMA_BASE_URL` (default `"http://localhost:11434/v1"`). Builds `OllamaModel(model_name, provider=OllamaProvider(base_url=...))` and `Agent(model, output_type=str, system_prompt=SYSTEM_PROMPT, retries=3)`.
- `SYSTEM_PROMPT` — instructs: produce a concise multi-paragraph summary of a YouTube video. Inputs given as `TITLE:`, `DESCRIPTION:`, `TRANSCRIPT:`. Ignore sponsorships, Patreon, channel-membership pitches, merch-store mentions, hashtags, and sponsor read-outs inside transcripts.
- `summarize_one(video_id, title, description, transcript, agent) -> tuple[str, str | None, str | None]` — returns `(status, text, error)`. If both description and transcript are None → `('skipped', None, 'no content')`. Otherwise `await agent.run(user_prompt=...)` and return `('ok', result.output, None)`. On exception → `('error', None, str(e))`.
- `fetch_summaries(db_path)` — async loop over pending/error rows, reads title/description/transcript from sibling modules ([ingest#load_watch_history](src/youtubebrain/ingest.py), [descriptions](src/youtubebrain/descriptions.py), [transcripts#load_transcripts](src/youtubebrain/transcripts.py)), calls `summarize_one`, UPDATEs row + commits per video. Log progress (`n_ok/n_total`).
- `main()` — `asyncio.run(_main_async())`. Loads watch history → enqueues ids → runs fetch loop. Add `[project.scripts] summaries = "youtubebrain.summaries:main"` in [pyproject.toml](pyproject.toml).

### `tests/test_summaries.py` (new)

Patterns from [tests/test_ingest.py](tests/test_ingest.py) + [tests/test_transcripts.py](tests/test_transcripts.py):

- `test_init_db_creates_schema` — table + index exist.
- `test_enqueue_inserts_pending` — duplicate ids ignored.
- `test_load_summaries_returns_ok_only` — `error`/`pending` rows return `None`.
- `test_summarize_one_skipped_when_no_content` — both inputs None → `('skipped', ...)`.
- `test_summarize_one_ok_with_stub_agent` — monkeypatch `agent.run` to return a fake result; verify status/text.
- `test_summarize_one_error_on_exception` — agent raises → `('error', None, msg)`.
- `test_build_agent_uses_default_model` — `SUMMARY_MODEL` unset → agent built with `"qwen3:32b"`.
- `test_build_agent_honors_env_override` — `SUMMARY_MODEL=foo:bar` → agent uses that model id.
- Mark any test that would hit real Ollama with `@pytest.mark.ollama`.

## Files to modify

### `src/youtubebrain/ingest.py`

- [_render_markdown](src/youtubebrain/ingest.py#L55) signature: add `summary: str | None = None` parameter.
- Insert into `body_lines` **before** `## Description`:
  ```
  "## Summary",
  "",
  summary if summary else "_(unavailable)_",
  "",
  ```
- [write_markdown](src/youtubebrain/ingest.py#L94): same new param, forward to `_render_markdown`.
- [main](src/youtubebrain/ingest.py#L119): import `load_summaries` from `youtubebrain.summaries`, call after `load_transcripts`, pass `summaries.get(vid)` into `write_markdown`.

### `pyproject.toml`

- Add dep `"pydantic-ai-slim[openai]>=1.0"`.
- Add `[project.scripts]` entry: `summaries = "youtubebrain.summaries:main"`.

### `lat.md/summaries.md` (new)

Top-level page mirroring [lat.md/transcripts.md](lat.md/transcripts.md) layout. Frontmatter `lat: require-code-mention: true`. Sections (each w/ leading paragraph ≤250 chars + `@lat:` ref in code):

- `CLI entry`, `SQLite schema`, `Enqueue`, `Read API`, `Fetch loop`, `Agent build`, `System prompt`, `Skipped when no content`, `Tests` (one subsection per test above).

### `lat.md/ingest.md`

- Update [Markdown writer](lat.md/ingest.md#L57) section: add `## Summary` to the file-layout block; mention summary comes from `[[summaries#Read API]]`. Add test specs `Render summary section` + `Render unavailable summary` under Tests.

### `lat.md/lat.md`

Add link `[[summaries]]` alongside existing `[[ingest]]`, `[[descriptions]]`, `[[transcripts]]`.

### `.env.example`

Add commented placeholders showing defaults:
```
# SUMMARY_MODEL=qwen3:32b
# OLLAMA_BASE_URL=http://localhost:11434/v1
```

## Operational flow after change

```
uv run transcripts   # populates transcripts.sqlite
uv run summaries     # NEW — populates summaries.sqlite via Ollama
uv run ingest        # reads all 3 caches, writes Markdown/raw/*.md
```

`ingest` never blocks on Ollama. Videos without a cached summary render `_(unavailable)_`.

## Verification

1. `ollama pull qwen3:32b` (or set `SUMMARY_MODEL` in `.env` to a model already pulled).
2. `uv run summaries` — confirm summaries.sqlite created, rows progress pending→ok, log shows `n_ok/n_total` and the model id in use.
3. `uv run ingest` — open `Markdown/raw/CQaa4SfFFck.md`, verify section order frontmatter → Summary → Description → Transcript; verify Summary text does not parrot the Patreon/merch/hashtag lines from the description.
4. Set `SUMMARY_MODEL=qwen3:8b` in `.env`, rerun `uv run summaries` — log must show the override is honored.
5. Pre-commit gates: `uv run ruff format . && uv run ruff check --fix . && uv run pyright . && uv run pytest -n auto`.
6. `lat check` — all wiki links + code refs resolve.

## Unresolved questions

- Summary length cap (e.g. ≤500 words) — leave to prompt or enforce post-hoc?
- Long transcripts can exceed model context. Truncate at N chars before sending, or rely on Ollama's `num_ctx`?
- Re-summarize when transcript later becomes available? (Currently: once `ok`, never retried.)
