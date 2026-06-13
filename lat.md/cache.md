---
lat:
  require-code-mention: true
---
# Cache

Defines a shared SQLite status-row cache abstraction used by worker stages to avoid duplicating schema/enqueue/read/retry logic.

## StatusCache API

[[src/youtubebrain/cache.py#StatusCache]] is a frozen dataclass with `db_path`, `table`, and declarative `extra_columns` so each worker can keep queryable stage-specific fields without copy-pasting SQL.

Its base schema is `video_id`, `status`, `text`, `error_message`, `attempts`, `fetched_at`, and `last_attempt`; workers add typed extras such as transcript metadata fields or summary `model`.

## Schema initialization

[[src/youtubebrain/cache.py#StatusCache#init_db]] creates parent directories, enables WAL, and creates the table plus a status index idempotently.

The method validates table/column identifiers and supports repeated calls so worker entrypoints can safely run `init_db()` every time.

## Enqueue

[[src/youtubebrain/cache.py#StatusCache#enqueue]] deduplicates incoming ids and inserts missing rows as `pending` via `INSERT OR IGNORE`.

Existing rows remain unchanged, which preserves resumability across reruns while still admitting new ids.

## Read API

[[src/youtubebrain/cache.py#StatusCache#load_ok]] returns `{video_id: text_or_none}` for requested ids and only exposes text for rows where `status='ok'`.

Missing databases and non-ok rows both map to `None`, giving callers a stable shape that directly feeds markdown placeholder rendering.

## Retry selection

[[src/youtubebrain/cache.py#StatusCache#pending_ids]] and [[src/youtubebrain/cache.py#StatusCache#next_retryable]] implement shared retry selection using status filters, attempt caps, and attempt-first random ordering.

Workers pass stage-specific status sets and max-attempt values while reusing one selection implementation.

## Write API

[[src/youtubebrain/cache.py#StatusCache#record_result]] writes resolved outcomes (`status`, `text`, `error_message`, extras), bumps attempts, and stamps both `fetched_at` and `last_attempt`.

[[src/youtubebrain/cache.py#StatusCache#record_attempt]] records failed attempts without setting `fetched_at`, preserving blocked-row semantics.

## Progress counts

[[src/youtubebrain/cache.py#StatusCache#counts]] returns `(ok_count, total_count)` for progress logging in worker loops.

The aggregate is intentionally tiny and read-only so loops can log completion percentages after each committed row.

## Tests

Unit coverage in `tests/test_cache.py` validates table creation, enqueue/read behavior, retry selection, and write semantics for both result and attempt updates.

### Init idempotent enables WAL

Calling `init_db` multiple times keeps table/index creation idempotent and leaves journal mode configured to WAL.

### Enqueue dedupes ids

`enqueue` inserts each unique id once as `pending`, even across repeated calls with duplicates.

### load_ok missing db returns None map

When the sqlite file does not exist, `load_ok` still returns a complete id-to-None mapping.

### load_ok filters non-ok rows

Rows with statuses other than `ok` are returned as `None` even if they contain text.

### load_ok chunks >500 ids

`load_ok` handles large id lists by chunking `IN` queries and preserving output entries for every requested id.

### record_result writes timestamps attempts extras

`record_result` increments attempts, stamps fetched/attempt timestamps, and persists declared extra columns.

### record_attempt no fetched_at

`record_attempt` increments attempts and last-attempt timestamps while leaving `fetched_at` unset.

### next_retryable honors attempt cap

`next_retryable` and `pending_ids` only return rows whose statuses match and whose attempts are below the configured cap.

### record_result rejects unknown extras

Extras naming columns not declared for the table raise `ValueError` and leave the row unchanged, so a worker typo cannot write to the wrong column.

### Rejects invalid identifiers

`init_db` raises `ValueError` when the table name or an extra-column name is not a valid SQL identifier, keeping unsafe names out of interpolated SQL.

### Empty inputs short-circuit

Empty id lists and empty status tuples return empty results (`{}`, `[]`, `None`) without inserting or selecting any rows.
