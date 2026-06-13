"""Unit tests for the SQLite-backed descriptions stage."""

import sqlite3
from pathlib import Path

import httpx
import pytest
import respx

from youtubebrain.descriptions import YOUTUBE_API_URL, enqueue, fetch_descriptions, init_db, load_descriptions


def _api_response(items: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(200, json={"items": items})


def _video_item(vid: str, description: str) -> dict[str, object]:
    return {"id": vid, "snippet": {"description": description}}


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY_YOUTUBE", "test-key")


# @lat: [[descriptions#Tests#Read API missing db]]
def test_load_descriptions_missing_db_returns_none_map(tmp_path: Path) -> None:
    """load_descriptions returns a full None map when the sqlite file is missing."""
    db_path = tmp_path / "descriptions.sqlite"
    assert load_descriptions(["a", "b", "a"], db_path) == {"a": None, "b": None}


# @lat: [[descriptions#Tests#Read API ok only]]
def test_load_descriptions_returns_ok_only(tmp_path: Path) -> None:
    """load_descriptions exposes text only for rows where status is ok."""
    db_path = tmp_path / "descriptions.sqlite"
    init_db(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.executemany(
            "INSERT INTO descriptions (video_id, status, text) VALUES (?, ?, ?)",
            [
                ("ok1", "ok", "desc ok"),
                ("missing1", "missing", None),
                ("error1", "error", "hidden"),
            ],
        )
        con.commit()
    finally:
        con.close()

    assert load_descriptions(["ok1", "missing1", "error1"], db_path) == {
        "ok1": "desc ok",
        "missing1": None,
        "error1": None,
    }


# @lat: [[descriptions#Tests#Batches in fifties]]
def test_fetch_descriptions_batches_in_50s(tmp_path: Path) -> None:
    """75 queued IDs are fetched in exactly two HTTP batches."""
    db_path = tmp_path / "descriptions.sqlite"
    ids = [f"id{i:03d}" for i in range(75)]
    init_db(db_path)
    enqueue(ids, db_path)
    with respx.mock() as router:
        route = router.get(YOUTUBE_API_URL).mock(
            side_effect=lambda request: _api_response(
                [_video_item(vid, f"desc-{vid}") for vid in request.url.params["id"].split(",")],
            ),
        )
        fetch_descriptions(db_path)

    assert route.call_count == 2
    loaded = load_descriptions(ids, db_path)
    assert loaded["id000"] == "desc-id000"
    assert loaded["id074"] == "desc-id074"


# @lat: [[descriptions#Tests#Missing rows become missing status]]
def test_fetch_descriptions_marks_missing_rows(tmp_path: Path) -> None:
    """IDs absent from the API response are persisted as terminal missing rows."""
    db_path = tmp_path / "descriptions.sqlite"
    init_db(db_path)
    enqueue(["present", "gone1", "gone2"], db_path)
    with respx.mock() as router:
        router.get(YOUTUBE_API_URL).mock(return_value=_api_response([_video_item("present", "yes")]))
        fetch_descriptions(db_path)

    assert load_descriptions(["present", "gone1", "gone2"], db_path) == {"present": "yes", "gone1": None, "gone2": None}
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT video_id, status FROM descriptions WHERE video_id IN ('present','gone1','gone2') ORDER BY video_id",
        ).fetchall()
    finally:
        con.close()
    assert rows == [("gone1", "missing"), ("gone2", "missing"), ("present", "ok")]


# @lat: [[descriptions#Tests#Persists per batch]]
def test_fetch_descriptions_persists_per_batch(tmp_path: Path) -> None:
    """A failed later batch still leaves earlier successful rows committed."""
    db_path = tmp_path / "descriptions.sqlite"
    ids = [f"id{i:03d}" for i in range(60)]
    init_db(db_path)
    enqueue(ids, db_path)
    batches: list[list[str]] = []

    def _respond(request: httpx.Request) -> httpx.Response:
        # Batch membership is randomized by pending_ids, so echo the requested
        # ids back instead of assuming which ids land in the first batch.
        batch = request.url.params["id"].split(",")
        batches.append(batch)
        if len(batches) == 1:
            return _api_response([_video_item(vid, f"desc-{vid}") for vid in batch])
        return httpx.Response(500, json={"error": "boom"})

    with respx.mock() as router:
        router.get(YOUTUBE_API_URL).mock(side_effect=_respond)
        fetch_descriptions(db_path)

    assert len(batches) == 2
    ok_batch, failed_batch = batches
    assert len(ok_batch) == 50
    assert len(failed_batch) == 10
    loaded = load_descriptions(ids, db_path)
    for vid in ok_batch:
        assert loaded[vid] == f"desc-{vid}"
    for vid in failed_batch:
        assert loaded[vid] is None

    con = sqlite3.connect(db_path)
    try:
        n_ok = con.execute("SELECT COUNT(*) FROM descriptions WHERE status='ok'").fetchone()[0]
        n_error = con.execute(
            "SELECT COUNT(*) FROM descriptions WHERE status='error' AND attempts=1",
        ).fetchone()[0]
    finally:
        con.close()
    assert int(n_ok) == 50
    assert int(n_error) == 10


# @lat: [[descriptions#Tests#Error rows retryable]]
def test_fetch_descriptions_retries_error_rows(tmp_path: Path) -> None:
    """Rows marked error are retried on the next run while attempts remain below the cap."""
    db_path = tmp_path / "descriptions.sqlite"
    init_db(db_path)
    enqueue(["a", "b"], db_path)
    with respx.mock() as router:
        router.get(YOUTUBE_API_URL).mock(return_value=httpx.Response(500, json={"error": "boom"}))
        fetch_descriptions(db_path)

    with respx.mock() as router:
        router.get(YOUTUBE_API_URL).mock(
            return_value=_api_response([_video_item("a", "A"), _video_item("b", "B")]),
        )
        fetch_descriptions(db_path)

    assert load_descriptions(["a", "b"], db_path) == {"a": "A", "b": "B"}
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT video_id, status, attempts FROM descriptions ORDER BY video_id").fetchall()
    finally:
        con.close()
    assert rows == [("a", "ok", 2), ("b", "ok", 2)]


# @lat: [[descriptions#Tests#API key only when pending]]
def test_fetch_descriptions_requires_key_only_for_pending_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing API key raises only when retryable pending/error rows exist."""
    db_path = tmp_path / "descriptions.sqlite"
    init_db(db_path)
    enqueue(["a"], db_path)
    monkeypatch.delenv("API_KEY_YOUTUBE", raising=False)
    with pytest.raises(RuntimeError, match="API_KEY_YOUTUBE"):
        fetch_descriptions(db_path)

    con = sqlite3.connect(db_path)
    try:
        con.execute("UPDATE descriptions SET status='ok', text='A' WHERE video_id='a'")
        con.commit()
    finally:
        con.close()

    # No pending/error rows, so no API call and therefore no key required.
    fetch_descriptions(db_path)
