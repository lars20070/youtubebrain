---
lat:
  require-code-mention: true
---
# Summaries

Generates per-video summaries via a configurable LLM provider (Ollama by default; see [[provider]]), stores them in SQLite for resumable runs, and exposes plain text for [[ingest#Markdown writer]] via [[summaries#Read API]].

The pipeline mirrors [[transcripts]]: a slow fetcher persists durable state, while [[src/youtubebrain/ingest.py#main]] stays fast by only reading the cache when writing `Markdown/raw/<video_id>.md`. Summaries synthesise title, description, and transcript; sponsorship and merch boilerplate are ignored by prompt instruction, not regex stripping.

```mermaid
flowchart TD
    Takeout[Takeout_watch_history_json]
    Takeout -->|summaries_main| SM[uv_run_summaries]
    Takeout -->|ingest_main| Ingest[uv_run_ingest]
    Desc[(descriptions_json)]
    TxDB[(transcripts_sqlite)]
    SM -->|enqueue| SmDB[(summaries_sqlite)]
    Desc --> SM
    TxDB --> SM
    SM -->|fetch_summaries| LLM[Provider_via_pydantic_ai]
    LLM --> SmDB
    Ingest -->|load_summaries| SmDB
    Ingest --> MD[Markdown_raw]
```

## CLI entry

[[src/youtubebrain/summaries.py#main]] is the `uv run summaries` entry point: Takeout IDs, enqueue, then the async fetch loop.

`_main_async` pulls ids from [[src/youtubebrain/takeout.py#load_video_ids]], while [[src/youtubebrain/summaries.py#fetch_summaries]] uses [[src/youtubebrain/takeout.py#load_watch_history]] and [[src/youtubebrain/takeout.py#video_id]] to build title context for each row.

## SQLite schema

[[src/youtubebrain/summaries.py#init_db]] delegates to [[src/youtubebrain/cache.py#StatusCache#init_db]] via a `StatusCache` configured for the `summaries` table.

Shared base columns come from [[cache#StatusCache API]]: `text` holds the summary body, and `error_message`, `fetched_at`, `last_attempt`, and `attempts` support debugging and retry caps. The summary-specific extra column `model` records which model id produced an `ok` row. `status` drives resumability: `pending` (queued), `ok` (summary ready for markdown), `skipped` (no description or transcript to summarize), and `error` (LLM or transport failure; retried until `attempts` reaches the cap).

## Enqueue

[[src/youtubebrain/summaries.py#enqueue]] delegates to [[src/youtubebrain/cache.py#StatusCache#enqueue]] for dedupe + pending-row insertion.

Existing primary keys are left unchanged, so re-running after a partial night only adds new ids from an updated Takeout export without clobbering completed rows.

## Read API

[[src/youtubebrain/summaries.py#load_summaries]] is a wrapper over [[src/youtubebrain/cache.py#StatusCache#load_ok]].

If the database file is missing, every requested id maps to `None`. Otherwise only `status='ok'` rows return text so ingest can render `_(unavailable)_` for every other case.

## Fetch loop

[[src/youtubebrain/summaries.py#fetch_summaries]] runs the summarization worker: one video at a time, async LLM calls via pydantic-ai.

Row selection uses [[src/youtubebrain/cache.py#StatusCache#next_retryable]] with statuses `pending/error` and attempt cap 5. Rows with `status='ok'` or `status='skipped'` never match again. On each iteration the loop loads titles from [[takeout#Loader]], descriptions from `Markdown/.cache/descriptions.json`, and transcripts via [[transcripts#Read API]], then calls [[src/youtubebrain/summaries.py#summarize_one]]. Successful rows are persisted through [[src/youtubebrain/cache.py#StatusCache#record_result]], and progress logging uses [[src/youtubebrain/cache.py#StatusCache#counts]].

## Agent build

[[src/youtubebrain/summaries.py#_build_agent]] constructs a pydantic-ai `Agent` whose model is built by [[provider#Model factory]].

The factory dispatches on the `PROVIDER` env var (default `ollama`) and returns either an `OpenAIChatModel` or a pydantic-ai shorthand string. The agent is wrapped with `output_type=str`, the system prompt, and `retries=3`. The `model` column in the SQLite table records the value of the `MODEL` env var (default `qwen3:32b`) for each `ok` row.

## System prompt

The constant `SYSTEM_PROMPT` instructs the model to produce a concise multi-paragraph summary from `TITLE:`, `DESCRIPTION:`, and `TRANSCRIPT:` blocks in the user message.

It explicitly tells the model to disregard sponsorships, Patreon pitches, channel-membership pitches, merch-store mentions, hashtags, and sponsor read-outs inside transcripts, and to keep the summary to roughly two to four short paragraphs.

## Transcript truncation

[[src/youtubebrain/summaries.py#_truncate]] caps transcript input at `_TRANSCRIPT_CHAR_LIMIT` (12000 characters) before building the user prompt.

Longer transcripts are cut with a trailing `…[transcript truncated]` marker so prompts stay within Ollama's default `num_ctx` without requiring per-model context tuning.

## Skipped when no content

[[src/youtubebrain/summaries.py#summarize_one]] returns `('skipped', None, 'no content')` when both description and transcript are `None`.

Skipped rows are persisted and not retried by the fetch loop selection query.

## Re-summarize policy

Once a row reaches `status='ok'`, it is never selected again. The fetch loop only processes `pending` and `error` rows.

There is no input-hash or transcript-arrival re-summarize path: if a transcript appears later, delete or reset the row manually to regenerate.

## Tests

Pytest coverage for SQLite helpers, agent wiring, summarize_one branches, and fetch-loop persistence; each leaf below maps to one `# @lat:` comment in `tests/test_summaries.py`.

### Schema and enqueue idempotent

Verifies `init_db` creates the table and `idx_summaries_status` index.

### Enqueue inserts pending

Double `enqueue` leaves one pending row per unique id.

### load_summaries None for non-ok

Pending and error rows map to `None` for readers.

### load_summaries returns text for ok

Status `ok` returns the stored plain `text` field.

### Skipped when no content

Both description and transcript `None` yields `skipped` with message `no content`.

### summarize_one ok with stub agent

A stub `agent.run` returning output produces `ok` status and text.

### summarize_one error on exception

Agent exceptions map to `error` status with the exception string preserved.

### Transcript truncation marks suffix

Input over `_TRANSCRIPT_CHAR_LIMIT` includes the truncation marker suffix.

### Default model when env unset

`_build_agent` passes the model returned by [[provider#Model factory]] verbatim to the `Agent` constructor.

### MODEL env override

`MODEL=foo:bar` is what `fetch_summaries` writes into the `model` column of `ok` rows.

### Fetch loop persists ok

A mocked fetch run writes `ok` text and records the model id with `attempts=1`.

### Fetch loop skipped without inputs

When description and transcript are both missing, the row becomes `skipped` without calling the agent.
