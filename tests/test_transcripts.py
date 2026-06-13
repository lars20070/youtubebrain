"""Unit tests for the SQLite-backed transcript fetcher and read API."""

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from youtube_transcript_api._errors import NoTranscriptFound, RequestBlocked

from youtubebrain.transcripts import (
    _DEFAULT_SLEEP_MAX,
    _DEFAULT_SLEEP_MIN,
    BlockedError,
    _inter_video_sleep_window,
    _json3_file_to_text,
    _ResolvedOk,
    _try_yta,
    _try_ytdlp,
    enqueue,
    fetch_transcripts,
    init_db,
    load_transcripts,
    resolve_transcript,
)


# @lat: [[transcripts#Tests#Schema and enqueue idempotent]]
def test_init_enqueue_idempotent(tmp_path: Path) -> None:
    """init_db creates the table; enqueue inserts pending rows once per id."""
    db = tmp_path / "t.sqlite"
    init_db(db)
    enqueue(["a", "b"], db)
    enqueue(["a", "b", "b"], db)
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT video_id, status FROM transcripts ORDER BY video_id").fetchall()
        assert rows == [("a", "pending"), ("b", "pending")]
    finally:
        con.close()


# @lat: [[transcripts#Tests#load_transcripts None for non-ok]]
def test_load_transcripts_none_for_non_ok(tmp_path: Path) -> None:
    """Rows that are not status ok surface as None to the reader."""
    db = tmp_path / "t.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO transcripts (video_id, status, text) VALUES ('x', 'pending', 'hidden')",
        )
        con.commit()
    finally:
        con.close()
    assert load_transcripts(["x"], db) == {"x": None}


# @lat: [[transcripts#Tests#load_transcripts returns text for ok]]
def test_load_transcripts_returns_text_for_ok(tmp_path: Path) -> None:
    """Status ok rows return their plain text field."""
    db = tmp_path / "t.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO transcripts (video_id, status, text, language, is_generated, raw_json, source) "
            "VALUES ('x', 'ok', 'hello world', 'en', 0, '[]', 'yta')",
        )
        con.commit()
    finally:
        con.close()
    assert load_transcripts(["x"], db) == {"x": "hello world"}


# @lat: [[transcripts#Tests#Primary ok persists yta fields]]
def test_fetch_persists_ok_from_yta(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A successful yta resolution writes text, raw_json, and source yta."""
    db = tmp_path / "t.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT INTO transcripts (video_id, status) VALUES ('vid1', 'pending')")
        con.commit()
    finally:
        con.close()

    raw = [{"text": "one", "start": 0.0, "duration": 1.0}]

    def fake_resolve(vid: str, state: object) -> tuple:  # noqa: ANN401
        assert vid == "vid1"
        return ("ok", "en", "one", json.dumps(raw), 0, None, "yta")

    monkeypatch.setattr("youtubebrain.transcripts._resolve_with_fallbacks", fake_resolve)
    monkeypatch.setattr("youtubebrain.transcripts._sleep", lambda _: None)

    fetch_transcripts(db)

    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT status, language, text, source, raw_json FROM transcripts WHERE video_id='vid1'").fetchone()
        assert row[0] == "ok"
        assert row[1] == "en"
        assert row[2] == "one"
        assert row[3] == "yta"
        assert json.loads(row[4]) == raw
    finally:
        con.close()


# @lat: [[transcripts#Tests#TranscriptsDisabled terminal]]
def test_transcripts_disabled_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """TranscriptsDisabled maps to no_captions via the yta terminal path."""

    def fake_try_yta(vid: str, api: object) -> object:  # noqa: ARG001
        return ("terminal", "no_captions", "disabled")

    monkeypatch.setattr("youtubebrain.transcripts._try_yta", fake_try_yta)
    monkeypatch.setattr("youtubebrain.transcripts._try_ytdlp", lambda *_: ("error", "should not run"))
    monkeypatch.setattr("youtubebrain.transcripts._try_pytubefix", lambda *_: ("error", "should not run"))

    st, *_rest = resolve_transcript("v")
    assert st == "no_captions"


# @lat: [[transcripts#Tests#VideoUnavailable terminal]]
def test_video_unavailable_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Video unavailable maps to terminal status unavailable."""

    def fake_try_yta(vid: str, api: object) -> object:  # noqa: ARG001
        return ("terminal", "unavailable", "gone")

    monkeypatch.setattr("youtubebrain.transcripts._try_yta", fake_try_yta)
    st, *_ = resolve_transcript("x")
    assert st == "unavailable"


# @lat: [[transcripts#Tests#RequestBlocked backoff path]]
def test_request_blocked_updates_row_and_backoff(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """BlockedError marks the row blocked, increments attempts, and triggers backoff sleep."""
    db = tmp_path / "t.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT INTO transcripts (video_id, status) VALUES ('b1', 'pending')")
        con.commit()
    finally:
        con.close()

    def boom(*_a: object, **_k: object) -> None:
        raise BlockedError("nope")

    sleeps: list[float] = []
    monkeypatch.setattr("youtubebrain.transcripts._resolve_with_fallbacks", boom)
    monkeypatch.setattr("youtubebrain.transcripts._sleep", lambda s: sleeps.append(float(s)))

    fetch_transcripts(db)

    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT status, attempts FROM transcripts WHERE video_id='b1'").fetchone()
        assert row[0] == "blocked"
        assert row[1] == 1
    finally:
        con.close()
    assert sleeps and sleeps[0] == 300.0


# @lat: [[transcripts#Tests#Consecutive blocks abort]]
def test_consecutive_blocks_abort_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Four consecutive BlockedError responses stop the fetch loop without processing a fifth id."""
    db = tmp_path / "t.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        for i in range(5):
            con.execute("INSERT INTO transcripts (video_id, status) VALUES (?, 'pending')", (f"id{i}",))
        con.commit()
    finally:
        con.close()

    monkeypatch.setattr("youtubebrain.transcripts._resolve_with_fallbacks", lambda *_a, **_k: (_ for _ in ()).throw(BlockedError("x")))
    monkeypatch.setattr("youtubebrain.transcripts._sleep", lambda _s: None)

    fetch_transcripts(db)

    con = sqlite3.connect(db)
    try:
        blocked = con.execute("SELECT count(*) FROM transcripts WHERE status='blocked'").fetchone()[0]
        pending = con.execute("SELECT count(*) FROM transcripts WHERE status='pending'").fetchone()[0]
        assert blocked == 4
        assert pending == 1
    finally:
        con.close()


# @lat: [[transcripts#Tests#Translation path block raises BlockedError]]
def test_translation_path_block_raises_blocked_error() -> None:
    """A block raised by the translated-fetch path becomes BlockedError, not terminal no_captions."""

    class _BlockedTranscript:
        def translate(self, _lang: str) -> "_BlockedTranscript":
            return self

        def fetch(self) -> object:
            raise RequestBlocked("v")

    class _List:
        def find_transcript(self, _langs: object) -> _BlockedTranscript:
            return _BlockedTranscript()

    class _Api:
        def fetch(self, _video_id: str, languages: object = None) -> object:  # noqa: ARG002
            raise NoTranscriptFound("v", ["en"], None)

        def list(self, _video_id: str) -> _List:
            return _List()

    with pytest.raises(BlockedError):
        _try_yta("v", _Api())  # type: ignore[arg-type]


# @lat: [[transcripts#Tests#PoTokenRequired uses yt-dlp]]
def test_po_token_falls_through_to_ytdlp(monkeypatch: pytest.MonkeyPatch) -> None:
    """When yta returns fallback pot, yt-dlp success is returned."""
    calls: list[str] = []

    def fake_try_yta(vid: str, api: object) -> object:  # noqa: ARG001
        calls.append("yta")
        return ("fallback", "pot")

    def fake_ytdlp(vid: str) -> _ResolvedOk:
        calls.append("ytdlp")
        return _ResolvedOk(
            language="en",
            is_generated=True,
            text="from ytdlp",
            raw_json="[]",
            source="yt-dlp",
        )

    monkeypatch.setattr("youtubebrain.transcripts._try_yta", fake_try_yta)
    monkeypatch.setattr("youtubebrain.transcripts._try_ytdlp", fake_ytdlp)
    st, lang, text, *_ = resolve_transcript("abc")
    assert st == "ok"
    assert lang == "en"
    assert text == "from ytdlp"
    assert calls == ["yta", "ytdlp"]


# @lat: [[transcripts#Tests#yt-dlp JSON3 plain text]]
def test_json3_to_plain_text(tmp_path: Path) -> None:
    """JSON3 subtitle files are flattened into plain text."""
    p = tmp_path / "sub.json3"
    p.write_text(
        json.dumps({"events": [{"segs": [{"utf8": "Hello "}, {"utf8": "world"}]}]}),
        encoding="utf-8",
    )
    assert _json3_file_to_text(p) == "Hello world"


# @lat: [[transcripts#Tests#yt-dlp malformed JSON3 falls back]]
def test_ytdlp_malformed_json3_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A corrupt yt-dlp JSON3 file returns fallback instead of raising out of the fetch loop."""
    monkeypatch.setattr("youtubebrain.transcripts._which_ytdlp", lambda: "/usr/bin/yt-dlp")

    def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
        out_tmpl = cmd[cmd.index("-o") + 1]
        tmp_dir = Path(out_tmpl).parent
        (tmp_dir / "v.en.json3").write_text("{not json", encoding="utf-8")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("youtubebrain.transcripts.subprocess.run", fake_run)

    assert _try_ytdlp("v") == "fallback"


# @lat: [[transcripts#Tests#yt-dlp non-dict JSON3 falls back]]
def test_ytdlp_non_dict_json3_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON3 whose top level is a list (not dict) returns fallback rather than raising AttributeError."""
    monkeypatch.setattr("youtubebrain.transcripts._which_ytdlp", lambda: "/usr/bin/yt-dlp")

    def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
        out_tmpl = cmd[cmd.index("-o") + 1]
        tmp_dir = Path(out_tmpl).parent
        (tmp_dir / "v.en.json3").write_text("[1, 2, 3]", encoding="utf-8")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("youtubebrain.transcripts.subprocess.run", fake_run)

    assert _try_ytdlp("v") == "fallback"


# @lat: [[transcripts#Tests#pytubefix only after yt-dlp fallback]]
def test_pytubefix_after_ytdlp_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When yt-dlp returns fallback, pytubefix is invoked."""
    order: list[str] = []

    def fake_try_yta(vid: str, api: object) -> object:  # noqa: ARG001
        order.append("yta")
        return ("fallback", "pot")

    def fake_ytdlp(vid: str) -> str:
        order.append("ytdlp")
        return "fallback"

    def fake_pf(vid: str) -> _ResolvedOk:
        order.append("pytubefix")
        return _ResolvedOk(
            language="en",
            is_generated=True,
            text="pf ok",
            raw_json="[]",
            source="pytubefix",
        )

    monkeypatch.setattr("youtubebrain.transcripts._try_yta", fake_try_yta)
    monkeypatch.setattr("youtubebrain.transcripts._try_ytdlp", fake_ytdlp)
    monkeypatch.setattr("youtubebrain.transcripts._try_pytubefix", fake_pf)
    st, *_rest = resolve_transcript("z")
    assert st == "ok"
    assert order == ["yta", "ytdlp", "pytubefix"]


# @lat: [[transcripts#Tests#Sleep window uses env values]]
def test_sleep_window_uses_env_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Valid TRANSCRIPTS_SLEEP_MIN/MAX are used for inter-video pacing."""
    monkeypatch.setenv("TRANSCRIPTS_SLEEP_MIN", "1.0")
    monkeypatch.setenv("TRANSCRIPTS_SLEEP_MAX", "2.0")
    assert _inter_video_sleep_window() == (1.0, 2.0)

    db = tmp_path / "t.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT INTO transcripts (video_id, status) VALUES ('vid1', 'pending')")
        con.commit()
    finally:
        con.close()

    def fake_resolve(vid: str, state: object) -> tuple:  # noqa: ANN401, ARG001
        return ("ok", "en", "one", "[]", 0, None, "yta")

    sleeps: list[float] = []
    monkeypatch.setattr("youtubebrain.transcripts._resolve_with_fallbacks", fake_resolve)
    monkeypatch.setattr("youtubebrain.transcripts._sleep", lambda s: sleeps.append(float(s)))

    fetch_transcripts(db)

    assert len(sleeps) == 1
    assert 1.0 <= sleeps[0] <= 2.0


# @lat: [[transcripts#Tests#Sleep window falls back on invalid env]]
def test_sleep_window_falls_back_on_invalid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-numeric env values fall back to the default sleep window."""
    monkeypatch.setenv("TRANSCRIPTS_SLEEP_MIN", "abc")
    monkeypatch.setenv("TRANSCRIPTS_SLEEP_MAX", "xyz")
    assert _inter_video_sleep_window() == (_DEFAULT_SLEEP_MIN, _DEFAULT_SLEEP_MAX)


# @lat: [[transcripts#Tests#Sleep window falls back when min greater than max]]
def test_sleep_window_falls_back_when_min_gt_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """When min exceeds max, the helper returns the default sleep window."""
    monkeypatch.setenv("TRANSCRIPTS_SLEEP_MIN", "10")
    monkeypatch.setenv("TRANSCRIPTS_SLEEP_MAX", "5")
    assert _inter_video_sleep_window() == (_DEFAULT_SLEEP_MIN, _DEFAULT_SLEEP_MAX)


# @lat: [[transcripts#Tests#Attempts cap at five]]
def test_attempts_cap_stops_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Rows with attempts already at five are not selected again."""
    db = tmp_path / "t.sqlite"
    init_db(db)
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO transcripts (video_id, status, attempts) VALUES ('done', 'error', 5)",
        )
        con.commit()
    finally:
        con.close()

    called = False

    def mark_called(*_a: object, **_k: object) -> tuple:
        nonlocal called
        called = True
        return ("ok", "en", "t", "[]", 0, None, "yta")

    monkeypatch.setattr("youtubebrain.transcripts._resolve_with_fallbacks", mark_called)
    monkeypatch.setattr("youtubebrain.transcripts._sleep", lambda _s: None)

    fetch_transcripts(db)
    assert called is False
