"""Unit tests for the SQLite-backed summary fetcher and read API."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from youtubebrain.summaries import (
    _TRANSCRIPT_CHAR_LIMIT,
    _build_agent,
    _truncate,
    enqueue,
    fetch_summaries,
    init_db,
    load_summaries,
    summarize_one,
)


# @lat: [[summaries#Tests#Schema and enqueue idempotent]]
def test_init_db_creates_schema(tmp_path: Path) -> None:
    """init_db creates the summaries table and status index."""
    db = tmp_path / "s.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "summaries" in tables
        indexes = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_summaries_status" in indexes
    finally:
        con.close()


# @lat: [[summaries#Tests#Enqueue inserts pending]]
def test_enqueue_inserts_pending(tmp_path: Path) -> None:
    """enqueue inserts pending rows once per id; duplicates are ignored."""
    db = tmp_path / "s.sqlite"
    init_db(db)
    enqueue(["a", "b"], db)
    enqueue(["a", "b", "b"], db)
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT video_id, status FROM summaries ORDER BY video_id").fetchall()
        assert rows == [("a", "pending"), ("b", "pending")]
    finally:
        con.close()


# @lat: [[summaries#Tests#load_summaries None for non-ok]]
def test_load_summaries_returns_ok_only(tmp_path: Path) -> None:
    """Rows that are not status ok surface as None to the reader."""
    db = tmp_path / "s.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO summaries (video_id, status, text) VALUES ('x', 'pending', 'hidden')",
        )
        con.execute(
            "INSERT INTO summaries (video_id, status, text) VALUES ('y', 'error', 'err text')",
        )
        con.commit()
    finally:
        con.close()
    assert load_summaries(["x", "y"], db) == {"x": None, "y": None}


# @lat: [[summaries#Tests#load_summaries returns text for ok]]
def test_load_summaries_text_for_ok(tmp_path: Path) -> None:
    """Status ok rows return their plain text field."""
    db = tmp_path / "s.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO summaries (video_id, status, text, model) VALUES ('x', 'ok', 'summary body', 'qwen3:32b')",
        )
        con.commit()
    finally:
        con.close()
    assert load_summaries(["x"], db) == {"x": "summary body"}


class _FakeRunResult:
    def __init__(self, output: str) -> None:
        self.output = output


class _StubAgent:
    def __init__(self, output: str = "ok body", *, raise_error: Exception | None = None) -> None:
        self.output = output
        self.raise_error = raise_error
        self.calls: list[str] = []

    async def run(self, user_prompt: str = "", **_kw: object) -> _FakeRunResult:
        self.calls.append(user_prompt)
        if self.raise_error is not None:
            raise self.raise_error
        return _FakeRunResult(self.output)


# @lat: [[summaries#Tests#Skipped when no content]]
@pytest.mark.asyncio
async def test_summarize_one_skipped_when_no_content() -> None:
    """Both description and transcript None yields skipped with no content error."""
    status, text, err = await summarize_one("vid", "Title", None, None, _StubAgent())  # type: ignore[arg-type]
    assert status == "skipped"
    assert text is None
    assert err == "no content"


# @lat: [[summaries#Tests#summarize_one ok with stub agent]]
@pytest.mark.asyncio
async def test_summarize_one_ok_with_stub_agent() -> None:
    """A successful agent run returns ok status and output text."""
    stub = _StubAgent("ok body")
    status, text, err = await summarize_one("vid", "Title", "desc", "transcript", stub)  # type: ignore[arg-type]
    assert status == "ok"
    assert text == "ok body"
    assert err is None
    assert "TITLE:" in stub.calls[0]
    assert "desc" in stub.calls[0]


# @lat: [[summaries#Tests#summarize_one error on exception]]
@pytest.mark.asyncio
async def test_summarize_one_error_on_exception() -> None:
    """Agent exceptions map to error status with the message preserved."""
    stub = _StubAgent(raise_error=RuntimeError("boom"))
    status, text, err = await summarize_one("vid", "Title", "d", None, stub)  # type: ignore[arg-type]
    assert status == "error"
    assert text is None
    assert err == "boom"


# @lat: [[summaries#Tests#Transcript truncation marks suffix]]
def test_truncate_marks_truncation() -> None:
    """Text longer than the char limit is truncated with a marker suffix."""
    long_text = "x" * (_TRANSCRIPT_CHAR_LIMIT + 100)
    truncated, was_truncated = _truncate(long_text)
    assert was_truncated is True
    assert truncated.endswith("…[transcript truncated]")
    assert len(truncated) > _TRANSCRIPT_CHAR_LIMIT


# @lat: [[summaries#Tests#Default model when env unset]]
def test_build_agent_uses_create_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """_build_agent passes the model returned by create_model to Agent."""
    sentinel = object()
    monkeypatch.setattr("youtubebrain.summaries.create_model", lambda: sentinel)
    recorded: list[object] = []

    def fake_agent(model: object, **_kwargs: object) -> object:
        recorded.append(model)
        return object()

    monkeypatch.setattr("youtubebrain.summaries.Agent", fake_agent)
    _build_agent()
    assert recorded == [sentinel]


# @lat: [[summaries#Tests#MODEL env override]]
@pytest.mark.asyncio
async def test_fetch_summaries_honors_model_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """MODEL env var is recorded in the model column of ok rows."""
    monkeypatch.setenv("MODEL", "foo:bar")
    db = tmp_path / "s.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT INTO summaries (video_id, status) VALUES ('vid1', 'pending')")
        con.commit()
    finally:
        con.close()

    class _Video:
        title = "Watched Hello"
        title_url = "https://www.youtube.com/watch?v=vid1"

    monkeypatch.setattr("youtubebrain.summaries.takeout.load_watch_history", lambda _p: [_Video()])
    monkeypatch.setattr("youtubebrain.summaries.takeout.video_id", lambda _u: "vid1")
    monkeypatch.setattr("youtubebrain.summaries._load_descriptions_cache", lambda: {"vid1": "desc"})
    monkeypatch.setattr("youtubebrain.summaries.load_transcripts", lambda _ids, _db=None: {"vid1": "transcript"})
    monkeypatch.setattr("youtubebrain.summaries._build_agent", lambda: _StubAgent("out"))

    await fetch_summaries(db)

    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT model FROM summaries WHERE video_id='vid1'").fetchone()
        assert row[0] == "foo:bar"
    finally:
        con.close()


# @lat: [[summaries#Tests#Fetch loop persists ok]]
@pytest.mark.asyncio
async def test_fetch_summaries_persists_ok(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """fetch_summaries writes ok rows with text and model from a stub agent."""
    monkeypatch.delenv("MODEL", raising=False)
    db = tmp_path / "s.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT INTO summaries (video_id, status) VALUES ('vid1', 'pending')")
        con.commit()
    finally:
        con.close()

    class _Video:
        title = "Watched Hello"
        title_url = "https://www.youtube.com/watch?v=vid1"

    monkeypatch.setattr("youtubebrain.summaries.takeout.load_watch_history", lambda _p: [_Video()])
    monkeypatch.setattr("youtubebrain.summaries.takeout.video_id", lambda _u: "vid1")
    monkeypatch.setattr("youtubebrain.summaries._load_descriptions_cache", lambda: {"vid1": "desc"})
    monkeypatch.setattr("youtubebrain.summaries.load_transcripts", lambda _ids, _db=None: {"vid1": "transcript"})
    monkeypatch.setattr("youtubebrain.summaries._build_agent", lambda: _StubAgent("out"))

    await fetch_summaries(db)

    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT status, text, model, attempts FROM summaries WHERE video_id='vid1'").fetchone()
        assert row[0] == "ok"
        assert row[1] == "out"
        assert row[2] == "qwen3:32b"
        assert row[3] == 1
    finally:
        con.close()


# @lat: [[summaries#Tests#Fetch loop skipped without inputs]]
@pytest.mark.asyncio
async def test_fetch_summaries_skipped_when_no_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When description and transcript are both missing, the row becomes skipped without calling the agent."""
    db = tmp_path / "s.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT INTO summaries (video_id, status) VALUES ('vid1', 'pending')")
        con.commit()
    finally:
        con.close()

    class _Video:
        title = "Watched Hello"
        title_url = "https://www.youtube.com/watch?v=vid1"

    stub = _StubAgent("should not run")
    monkeypatch.setattr("youtubebrain.summaries.takeout.load_watch_history", lambda _p: [_Video()])
    monkeypatch.setattr("youtubebrain.summaries.takeout.video_id", lambda _u: "vid1")
    monkeypatch.setattr("youtubebrain.summaries._load_descriptions_cache", lambda: {})
    monkeypatch.setattr("youtubebrain.summaries.load_transcripts", lambda _ids, _db=None: {"vid1": None})
    monkeypatch.setattr("youtubebrain.summaries._build_agent", lambda: stub)

    await fetch_summaries(db)

    assert stub.calls == []
    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT status, error_message FROM summaries WHERE video_id='vid1'").fetchone()
        assert row[0] == "skipped"
        assert row[1] == "no content"
    finally:
        con.close()
