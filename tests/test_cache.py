"""Unit tests for the shared SQLite status-row cache helper."""

from pathlib import Path

import pytest

from youtubebrain.cache import StatusCache


def _cache(
    tmp_path: Path,
    *,
    table: str = "status_rows",
    extra_columns: tuple[str, ...] = (),
) -> StatusCache:
    return StatusCache(tmp_path / "status.sqlite", table, extra_columns)


# @lat: [[cache#Tests#Init idempotent enables WAL]]
def test_init_db_idempotent_enables_wal(tmp_path: Path) -> None:
    """init_db can be called repeatedly and leaves table/index/WAL mode configured."""
    status_cache = _cache(tmp_path, extra_columns=("model TEXT",))
    status_cache.init_db()
    status_cache.init_db()

    con = status_cache.connect()
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        indexes = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        journal_mode = str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        con.close()

    assert "status_rows" in tables
    assert "idx_status_rows_status" in indexes
    assert journal_mode == "wal"


# @lat: [[cache#Tests#Enqueue dedupes ids]]
def test_enqueue_dedupes_video_ids(tmp_path: Path) -> None:
    """enqueue inserts one pending row per unique id and leaves existing keys untouched."""
    status_cache = _cache(tmp_path)
    status_cache.enqueue(["a", "b"])
    status_cache.enqueue(["a", "b", "b"])

    con = status_cache.connect()
    try:
        rows = con.execute("SELECT video_id, status FROM status_rows ORDER BY video_id").fetchall()
    finally:
        con.close()

    assert rows == [("a", "pending"), ("b", "pending")]


# @lat: [[cache#Tests#load_ok missing db returns None map]]
def test_load_ok_missing_db_returns_all_none(tmp_path: Path) -> None:
    """load_ok returns a full None map when the sqlite file does not exist."""
    status_cache = _cache(tmp_path)
    assert status_cache.load_ok(["a", "b", "a"]) == {"a": None, "b": None}
    assert not status_cache.db_path.exists()


# @lat: [[cache#Tests#load_ok filters non-ok rows]]
def test_load_ok_returns_text_only_for_ok_rows(tmp_path: Path) -> None:
    """Rows not in status ok map to None even if text is present in the table."""
    status_cache = _cache(tmp_path)
    status_cache.init_db()
    con = status_cache.connect()
    try:
        con.executemany(
            "INSERT INTO status_rows (video_id, status, text) VALUES (?, ?, ?)",
            [
                ("ok1", "ok", "hello"),
                ("err1", "error", "hidden"),
                ("pending1", "pending", "hidden"),
            ],
        )
        con.commit()
    finally:
        con.close()

    assert status_cache.load_ok(["ok1", "err1", "pending1", "missing"]) == {
        "ok1": "hello",
        "err1": None,
        "pending1": None,
        "missing": None,
    }


# @lat: [[cache#Tests#load_ok chunks >500 ids]]
def test_load_ok_chunks_large_id_lists(tmp_path: Path) -> None:
    """load_ok handles >500 ids by chunking queries and preserving all output keys."""
    status_cache = _cache(tmp_path)
    video_ids = [f"id{i:04d}" for i in range(1200)]
    status_cache.enqueue(video_ids)

    con = status_cache.connect()
    try:
        con.execute("UPDATE status_rows SET status='ok', text='text-0050' WHERE video_id='id0050'")
        con.execute("UPDATE status_rows SET status='ok', text='text-1100' WHERE video_id='id1100'")
        con.commit()
    finally:
        con.close()

    out = status_cache.load_ok(video_ids)
    assert len(out) == 1200
    assert out["id0050"] == "text-0050"
    assert out["id1100"] == "text-1100"
    assert out["id0000"] is None


# @lat: [[cache#Tests#record_result writes timestamps attempts extras]]
def test_record_result_updates_core_and_extra_columns(tmp_path: Path) -> None:
    """record_result bumps attempts, stamps fetched/last attempt, and writes declared extras."""
    status_cache = _cache(tmp_path, extra_columns=("language TEXT", "is_generated INTEGER"))
    status_cache.enqueue(["v1"])
    con = status_cache.connect()
    try:
        status_cache.record_result(
            con,
            "v1",
            "ok",
            text="hello",
            error_message=None,
            extra={"language": "en", "is_generated": 1},
        )
        row = con.execute(
            """
            SELECT status, text, error_message, attempts, fetched_at, last_attempt, language, is_generated
            FROM status_rows
            WHERE video_id='v1'
            """,
        ).fetchone()
    finally:
        con.close()

    assert row[0] == "ok"
    assert row[1] == "hello"
    assert row[2] is None
    assert row[3] == 1
    assert row[4] is not None
    assert row[5] is not None
    assert row[6] == "en"
    assert row[7] == 1


# @lat: [[cache#Tests#record_attempt no fetched_at]]
def test_record_attempt_does_not_touch_fetched_at(tmp_path: Path) -> None:
    """record_attempt increments attempts and last_attempt but leaves fetched_at unset."""
    status_cache = _cache(tmp_path)
    status_cache.enqueue(["v1"])
    con = status_cache.connect()
    try:
        status_cache.record_attempt(con, "v1", "blocked", "ip blocked")
        row = con.execute(
            "SELECT status, error_message, attempts, fetched_at, last_attempt FROM status_rows WHERE video_id='v1'",
        ).fetchone()
    finally:
        con.close()

    assert row[0] == "blocked"
    assert row[1] == "ip blocked"
    assert row[2] == 1
    assert row[3] is None
    assert row[4] is not None


# @lat: [[cache#Tests#record_result rejects unknown extras]]
def test_record_result_rejects_unknown_extra_columns(tmp_path: Path) -> None:
    """record_result raises ValueError for extras not declared for the table and leaves the row unchanged."""
    status_cache = _cache(tmp_path, extra_columns=("model TEXT",))
    status_cache.enqueue(["v1"])
    con = status_cache.connect()
    try:
        with pytest.raises(ValueError, match="Unknown extra columns"):
            status_cache.record_result(con, "v1", "ok", text="hello", extra={"language": "en"})
        row = con.execute("SELECT status, attempts FROM status_rows WHERE video_id='v1'").fetchone()
    finally:
        con.close()

    assert row == ("pending", 0)


# @lat: [[cache#Tests#Rejects invalid identifiers]]
def test_init_db_rejects_invalid_identifiers(tmp_path: Path) -> None:
    """init_db raises ValueError when the table or an extra-column name is not a valid SQL identifier."""
    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        _cache(tmp_path, table="bad-table").init_db()
    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        _cache(tmp_path, extra_columns=("bad-name TEXT",)).init_db()


# @lat: [[cache#Tests#Empty inputs short-circuit]]
def test_empty_inputs_short_circuit(tmp_path: Path) -> None:
    """Empty id lists and empty status tuples return empty results without inserting or selecting rows."""
    status_cache = _cache(tmp_path)
    status_cache.enqueue([])
    assert status_cache.load_ok([]) == {}

    con = status_cache.connect()
    try:
        assert status_cache.pending_ids(con, statuses=()) == []
        assert status_cache.next_retryable(con, statuses=()) is None
        n_rows = con.execute("SELECT COUNT(*) FROM status_rows").fetchone()[0]
    finally:
        con.close()

    assert n_rows == 0


# @lat: [[cache#Tests#next_retryable honors attempt cap]]
def test_next_retryable_respects_statuses_and_attempt_cap(tmp_path: Path) -> None:
    """next_retryable and pending_ids only return matching statuses with attempts below the cap."""
    status_cache = _cache(tmp_path)
    status_cache.init_db()
    con = status_cache.connect()
    try:
        con.executemany(
            "INSERT INTO status_rows (video_id, status, attempts) VALUES (?, ?, ?)",
            [
                ("a", "pending", 0),
                ("b", "error", 4),
                ("c", "error", 5),
                ("d", "ok", 0),
            ],
        )
        con.commit()

        assert status_cache.next_retryable(con, statuses=("pending", "error"), max_attempts=5) in {"a", "b"}
        assert status_cache.next_retryable(con, statuses=("error",), max_attempts=5) == "b"
        assert status_cache.next_retryable(con, statuses=("error",), max_attempts=4) is None
        assert set(status_cache.pending_ids(con, statuses=("pending", "error"), max_attempts=5)) == {"a", "b"}
    finally:
        con.close()
