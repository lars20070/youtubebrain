"""Fetch YouTube transcripts with resumable SQLite storage and a multi-step fallback chain."""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Literal

import requests
from pytubefix import YouTube
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    IpBlocked,
    NoTranscriptFound,
    PoTokenRequired,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
)

from youtubebrain import config, logger

LANGS: tuple[str, ...] = ("en", "en-US", "en-GB", "a.en")

UA_POOL: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
)

BACKOFFS: tuple[int, ...] = (300, 900, 2700, 7200)
_MAX_ATTEMPTS = 5
_SESSION_RECYCLE_EVERY = 300
_CONSECUTIVE_BLOCKS_ABORT = len(BACKOFFS)
_DEFAULT_SLEEP_MIN = 3.0
_DEFAULT_SLEEP_MAX = 7.0
_SLEEP_MIN_ENV = "TRANSCRIPTS_SLEEP_MIN"
_SLEEP_MAX_ENV = "TRANSCRIPTS_SLEEP_MAX"


class BlockedError(Exception):
    """Raised when YouTube signals an IP-level block so the outer loop can back off."""


def _sleep(seconds: float) -> None:
    """Sleep helper so tests can monkeypatch pacing."""
    time.sleep(seconds)


# @lat: [[transcripts#Pacing configuration]]
def _inter_video_sleep_window() -> tuple[float, float]:
    """Return (min, max) seconds for inter-video pacing from env or defaults."""
    config.load_env()
    raw_min = os.environ.get(_SLEEP_MIN_ENV, str(_DEFAULT_SLEEP_MIN))
    raw_max = os.environ.get(_SLEEP_MAX_ENV, str(_DEFAULT_SLEEP_MAX))
    try:
        sleep_min = float(raw_min)
        sleep_max = float(raw_max)
    except ValueError:
        logger.warning(
            f"Invalid {_SLEEP_MIN_ENV}={raw_min!r} or {_SLEEP_MAX_ENV}={raw_max!r}; using defaults ({_DEFAULT_SLEEP_MIN}, {_DEFAULT_SLEEP_MAX}).",
        )
        return (_DEFAULT_SLEEP_MIN, _DEFAULT_SLEEP_MAX)
    if sleep_min < 0 or sleep_max < sleep_min:
        logger.warning(
            f"Invalid sleep window ({sleep_min}, {sleep_max}); "
            f"require min >= 0 and max >= min; using defaults ({_DEFAULT_SLEEP_MIN}, {_DEFAULT_SLEEP_MAX}).",
        )
        return (_DEFAULT_SLEEP_MIN, _DEFAULT_SLEEP_MAX)
    return (sleep_min, sleep_max)


def _build_yta_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = random.choice(UA_POOL)
    s.headers["Accept-Language"] = "en-US,en;q=0.9"
    return s


@dataclass
class _YtaSessionState:
    """Rotates the requests Session periodically to avoid extreme keepalive patterns."""

    session: requests.Session | None = None
    calls_on_session: int = 0

    def api(self) -> YouTubeTranscriptApi:
        if self.session is None or self.calls_on_session >= _SESSION_RECYCLE_EVERY:
            self.session = _build_yta_session()
            self.calls_on_session = 0
        self.calls_on_session += 1
        return YouTubeTranscriptApi(http_client=self.session)


@dataclass(frozen=True)
class _ResolvedOk:
    language: str
    is_generated: bool
    text: str
    raw_json: str
    source: Literal["yta", "yt-dlp", "pytubefix"]


def _snippets_to_text(raw: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for row in raw:
        t = row.get("text")
        if isinstance(t, str) and t.strip():
            parts.append(t.strip())
    return " ".join(parts)


def _json3_file_to_text(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    chunks: list[str] = []
    for ev in data.get("events", []):
        for seg in ev.get("segs") or []:
            u = seg.get("utf8")
            if isinstance(u, str):
                chunks.append(u)
    return "".join(chunks).strip()


def _xml_captions_to_plain(xml: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", xml)
    return " ".join(unescape(no_tags).split())


def _try_yta(  # noqa: PLR0911
    video_id: str,
    api: YouTubeTranscriptApi,
) -> _ResolvedOk | tuple[Literal["fallback"], Literal["age", "pot"]] | tuple[Literal["terminal"], str, str | None]:
    """Return ok payload, ('fallback', reason), or ('terminal', status, error_message)."""
    try:
        ft = api.fetch(video_id, languages=LANGS)
        raw = ft.to_raw_data()
        return _ResolvedOk(
            language=ft.language_code,
            is_generated=ft.is_generated,
            text=_snippets_to_text(raw),
            raw_json=json.dumps(raw),
            source="yta",
        )
    except NoTranscriptFound:
        try:
            tl = api.list(video_id).find_transcript(LANGS).translate("en").fetch()
            raw = tl.to_raw_data()
            return _ResolvedOk(
                language="en-translated",
                is_generated=True,
                text=_snippets_to_text(raw),
                raw_json=json.dumps(raw),
                source="yta",
            )
        except (RequestBlocked, IpBlocked) as e:
            raise BlockedError(str(e)) from e
        except Exception as e:  # noqa: BLE001
            return ("terminal", "no_captions", str(e))
    except TranscriptsDisabled as e:
        return ("terminal", "no_captions", str(e))
    except VideoUnavailable as e:
        return ("terminal", "unavailable", str(e))
    except AgeRestricted:
        return ("fallback", "age")
    except PoTokenRequired:
        return ("fallback", "pot")
    except (RequestBlocked, IpBlocked) as e:
        raise BlockedError(str(e)) from e
    except CouldNotRetrieveTranscript as e:
        return ("terminal", "error", str(e))


def _which_ytdlp() -> str | None:
    return shutil.which("yt-dlp")


def _try_ytdlp(video_id: str) -> _ResolvedOk | Literal["fallback"] | tuple[str, str | None] | Literal["blocked"]:  # noqa: PLR0911
    exe = _which_ytdlp()
    if not exe:
        return ("error", "yt-dlp executable not found on PATH")
    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory() as tmp:
        out_tmpl = str(Path(tmp) / "%(id)s.%(ext)s")
        cmd = [
            exe,
            "--skip-download",
            "--write-auto-subs",
            "--write-subs",
            "--sub-langs",
            "en,en-US,en-GB",
            "--sub-format",
            "json3/best",
            "--sleep-requests",
            "1.5",
            "--sleep-subtitles",
            "3",
            "--extractor-args",
            "youtube:player_client=tv,mweb",
            "-o",
            out_tmpl,
            url,
        ]
        try:
            proc = subprocess.run(  # noqa: S603 — argv from template + trusted url
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            logger.error(f"yt-dlp timed out after 180 seconds for video {video_id}")
            return ("error", "yt-dlp timed out after 180 seconds")
        combined = (proc.stdout or "") + (proc.stderr or "")
        if "HTTP Error 429" in combined or "Too Many Requests" in combined:
            return "blocked"
        if proc.returncode != 0:
            return ("error", combined.strip()[:2000] or f"yt-dlp exit {proc.returncode}")
        json3_files = list(Path(tmp).glob("*.json3"))
        if not json3_files:
            return "fallback"
        try:
            text = _json3_file_to_text(json3_files[0])
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError) as e:
            logger.warning(f"yt-dlp produced unparseable JSON3 for {video_id} ({json3_files[0].name}): {e!r}; falling back to pytubefix")
            return "fallback"
        if not text:
            return "fallback"
        raw_like: list[dict[str, object]] = [{"text": text, "start": 0.0, "duration": 0.0}]
        return _ResolvedOk(
            language="en",
            is_generated=True,
            text=text,
            raw_json=json.dumps(raw_like),
            source="yt-dlp",
        )


def _try_pytubefix(video_id: str) -> _ResolvedOk | tuple[str, str | None]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        yt = YouTube(url, use_po_token=True)
        cap = None
        for key in ("a.en", "en"):
            if key in yt.captions:
                cap = yt.captions[key]
                break
        if cap is None:
            return ("error", "pytubefix: no English caption track")
        xml = cap.xml_captions
        text = _xml_captions_to_plain(xml)
        if not text:
            return ("error", "pytubefix: empty caption text")
        raw_like = [{"text": text, "start": 0.0, "duration": 0.0}]
        code = getattr(cap, "code", None)
        return _ResolvedOk(
            language=str(code or "en"),
            is_generated=True,
            text=text,
            raw_json=json.dumps(raw_like),
            source="pytubefix",
        )
    except Exception as e:  # noqa: BLE001
        return ("error", str(e))


def _resolve_with_fallbacks(  # noqa: PLR0911
    video_id: str,
    yta_state: _YtaSessionState,
) -> tuple[str, str | None, str | None, str | None, int | None, str | None, str | None]:
    """Return (status, language, text, raw_json, is_generated, error_message, source)."""
    api = yta_state.api()
    first = _try_yta(video_id, api)
    if isinstance(first, _ResolvedOk):
        return (
            "ok",
            first.language,
            first.text,
            first.raw_json,
            1 if first.is_generated else 0,
            None,
            first.source,
        )
    if first[0] == "terminal":
        _, status, err = first
        return (status, None, None, None, None, err, None)
    _, fb_reason = first
    second = _try_ytdlp(video_id)
    if second == "blocked":
        raise BlockedError("yt-dlp blocked (429 / Too Many Requests)")
    if isinstance(second, _ResolvedOk):
        return (
            "ok",
            second.language,
            second.text,
            second.raw_json,
            1 if second.is_generated else 0,
            None,
            second.source,
        )
    if second == "fallback":
        third = _try_pytubefix(video_id)
        if isinstance(third, _ResolvedOk):
            return (
                "ok",
                third.language,
                third.text,
                third.raw_json,
                1 if third.is_generated else 0,
                None,
                third.source,
            )
        status, err = third
        if fb_reason == "age":
            return ("age_restricted", None, None, None, None, err, None)
        return (status, None, None, None, None, err, None)
    status, err = second
    if fb_reason == "age" and status == "error":
        return ("age_restricted", None, None, None, None, err, None)
    return (status, None, None, None, None, err, None)


# @lat: [[transcripts#Fallback chain]]
def resolve_transcript(
    video_id: str,
    yta_state: _YtaSessionState | None = None,
) -> tuple[str, str | None, str | None, str | None, int | None, str | None, str | None]:
    """Resolve one video using the same fallback chain as the fetch loop (for tests)."""
    return _resolve_with_fallbacks(video_id, yta_state or _YtaSessionState())


# @lat: [[transcripts#SQLite schema]]
def init_db(db_path: Path | None = None) -> None:
    """Create the transcripts table and indexes if missing; enable WAL."""
    db_path = config.TRANSCRIPTS_DB_PATH if db_path is None else db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS transcripts (
                video_id      TEXT PRIMARY KEY,
                status        TEXT NOT NULL,
                language      TEXT,
                is_generated  INTEGER,
                text          TEXT,
                raw_json      TEXT,
                source        TEXT,
                error_message TEXT,
                attempts      INTEGER NOT NULL DEFAULT 0,
                fetched_at    TIMESTAMP,
                last_attempt  TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_transcripts_status ON transcripts(status);
            """
        )
        con.commit()
    finally:
        con.close()


# @lat: [[transcripts#Enqueue]]
def enqueue(video_ids: list[str], db_path: Path | None = None) -> None:
    """Insert video IDs as pending rows; existing primary keys are left unchanged."""
    db_path = config.TRANSCRIPTS_DB_PATH if db_path is None else db_path
    init_db(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.executemany(
            "INSERT OR IGNORE INTO transcripts (video_id, status) VALUES (?, 'pending')",
            [(vid,) for vid in dict.fromkeys(video_ids)],
        )
        con.commit()
    finally:
        con.close()


# @lat: [[transcripts#Read API]]
def load_transcripts(video_ids: list[str], db_path: Path | None = None) -> dict[str, str | None]:
    """Return plain transcript text for each id; None when missing or status is not ok."""
    db_path = config.TRANSCRIPTS_DB_PATH if db_path is None else db_path
    unique = list(dict.fromkeys(video_ids))
    if not unique:
        return {}
    if not db_path.exists():
        return dict.fromkeys(unique, None)
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        out: dict[str, str | None] = dict.fromkeys(unique, None)
        for chunk_start in range(0, len(unique), 500):
            chunk = unique[chunk_start : chunk_start + 500]
            qmarks = ",".join("?" * len(chunk))
            rows = cur.execute(
                f"SELECT video_id, text FROM transcripts WHERE video_id IN ({qmarks}) AND status = 'ok'",
                chunk,
            ).fetchall()
            for vid, text in rows:
                out[str(vid)] = text
        return out
    finally:
        con.close()


# @lat: [[transcripts#Fetch loop]]
def fetch_transcripts(db_path: Path | None = None) -> None:
    """Process pending/blocked/error rows with pacing until none remain or consecutive blocks abort."""
    db_path = config.TRANSCRIPTS_DB_PATH if db_path is None else db_path
    init_db(db_path)
    sleep_min, sleep_max = _inter_video_sleep_window()
    yta_state = _YtaSessionState()
    consecutive_blocks = 0
    ok_since_long_pause = 0
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        while True:
            row = con.execute(
                """
                SELECT video_id FROM transcripts
                WHERE status IN ('pending','error')
                  AND attempts < ?
                ORDER BY attempts ASC, RANDOM() LIMIT 1
                """,
                (_MAX_ATTEMPTS,),
            ).fetchone()
            if row is None:
                break
            video_id = str(row["video_id"])
            try:
                status, lang, text, raw_json, is_gen, err, source = _resolve_with_fallbacks(video_id, yta_state)
                consecutive_blocks = 0
                con.execute(
                    """
                    UPDATE transcripts SET
                        status=?,
                        language=?,
                        text=?,
                        raw_json=?,
                        is_generated=?,
                        error_message=?,
                        source=?,
                        attempts=attempts+1,
                        fetched_at=CURRENT_TIMESTAMP,
                        last_attempt=CURRENT_TIMESTAMP
                    WHERE video_id=?
                    """,
                    (status, lang, text, raw_json, is_gen, err, source, video_id),
                )
                con.commit()
                counts = con.execute(
                    "SELECT COALESCE(SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END), 0), COUNT(*) FROM transcripts",
                ).fetchone()
                n_ok, n_total = int(counts[0]), int(counts[1])
                pct = 100.0 * n_ok / n_total if n_total else 0.0
                logger.info(f"Transcript {video_id}: {status} ({n_ok}/{n_total} transcribed, {pct:.1f}%)")
                if status == "ok":
                    ok_since_long_pause += 1
                    if ok_since_long_pause > 0 and ok_since_long_pause % 500 == 0:
                        _sleep(random.uniform(60, 120))
                _sleep(random.uniform(sleep_min, sleep_max))
            except BlockedError as e:
                consecutive_blocks += 1
                con.execute(
                    """
                    UPDATE transcripts SET
                        status='blocked',
                        error_message=?,
                        attempts=attempts+1,
                        last_attempt=CURRENT_TIMESTAMP
                    WHERE video_id=?
                    """,
                    (str(e), video_id),
                )
                con.commit()
                logger.warning(f"Transcript blocked on {video_id}: {e!r}")
                if consecutive_blocks >= _CONSECUTIVE_BLOCKS_ABORT:
                    logger.error("Too many consecutive blocks; stopping fetch loop.")
                    break
                _sleep(float(BACKOFFS[consecutive_blocks - 1]))
    finally:
        con.close()


# @lat: [[transcripts#CLI entry]]
def main() -> None:
    """Load Takeout IDs, enqueue pending rows, then run the resumable fetch loop."""
    from youtubebrain.ingest import _video_id, load_watch_history  # noqa: PLC0415

    logger.info("Starting transcripts fetcher.")
    videos = load_watch_history(config.WATCH_HISTORY_PATH)
    ids = [_video_id(v.title_url) for v in videos if v.title_url is not None]
    init_db()
    enqueue(ids)
    logger.info(f"Enqueued {len(ids)} video ids; starting fetch loop.")
    fetch_transcripts()
    logger.info("Transcripts fetcher finished.")
