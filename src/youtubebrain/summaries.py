"""Generate per-video summaries via a configurable LLM provider with resumable SQLite storage."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path

from pydantic_ai import Agent

from youtubebrain import logger
from youtubebrain.descriptions import DESCRIPTIONS_CACHE_PATH
from youtubebrain.provider import create_model
from youtubebrain.transcripts import TRANSCRIPTS_DB_PATH, load_transcripts

SUMMARIES_DB_PATH = Path("Markdown/.cache/summaries.sqlite")

_TRANSCRIPT_CHAR_LIMIT = 12000
_DEFAULT_MODEL = "qwen3:32b"
_MAX_ATTEMPTS = 5

_MODEL_ENV = "MODEL"

# @lat: [[summaries#System prompt]]
SYSTEM_PROMPT = """\
You produce a concise multi-paragraph summary of a YouTube video from the inputs below.

Ignore sponsorships, Patreon pitches, channel-membership pitches, merch-store mentions, hashtags, \
and sponsor read-outs inside transcripts. Focus on the substantive content of the video.

Keep the summary focused and readable (roughly two to four short paragraphs).\
"""


def _load_descriptions_cache(path: Path = DESCRIPTIONS_CACHE_PATH) -> dict[str, str | None]:
    """Read the descriptions JSON cache; return an empty dict if absent."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# @lat: [[summaries#Transcript truncation]]
def _truncate(text: str, limit: int = _TRANSCRIPT_CHAR_LIMIT) -> tuple[str, bool]:
    """Return text unchanged if under limit, else truncate with a marker suffix."""
    if len(text) <= limit:
        return text, False
    return f"{text[:limit]}\n…[transcript truncated]", True


# @lat: [[summaries#Agent build]]
def _build_agent() -> Agent[None, str]:
    """Build a pydantic-ai Agent using the provider selected via PROVIDER env var."""
    model = create_model()
    return Agent(model, output_type=str, system_prompt=SYSTEM_PROMPT, retries=3)


def _build_user_prompt(
    title: str,
    description: str | None,
    transcript: str | None,
) -> str:
    """Format title, description, and transcript for the LLM user message."""
    desc_block = description if description else "(none)"
    if transcript:
        transcript_block, _ = _truncate(transcript)
    else:
        transcript_block = "(none)"
    return f"TITLE:\n{title}\n\nDESCRIPTION:\n{desc_block}\n\nTRANSCRIPT:\n{transcript_block}"


# @lat: [[summaries#Skipped when no content]]
async def summarize_one(
    video_id: str,
    title: str,
    description: str | None,
    transcript: str | None,
    agent: Agent[None, str],
) -> tuple[str, str | None, str | None]:
    """Summarize one video; return (status, text, error_message)."""
    _ = video_id  # reserved for logging at call sites
    if description is None and transcript is None:
        return ("skipped", None, "no content")
    try:
        user_prompt = _build_user_prompt(title, description, transcript)
        result = await agent.run(user_prompt=user_prompt)
        return ("ok", result.output, None)
    except Exception as e:  # noqa: BLE001
        return ("error", None, str(e))


# @lat: [[summaries#SQLite schema]]
def init_db(db_path: Path = SUMMARIES_DB_PATH) -> None:
    """Create the summaries table and indexes if missing; enable WAL."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS summaries (
                video_id      TEXT PRIMARY KEY,
                status        TEXT NOT NULL,
                text          TEXT,
                model         TEXT,
                error_message TEXT,
                attempts      INTEGER NOT NULL DEFAULT 0,
                fetched_at    TIMESTAMP,
                last_attempt  TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_summaries_status ON summaries(status);
            """
        )
        con.commit()
    finally:
        con.close()


# @lat: [[summaries#Enqueue]]
def enqueue(video_ids: list[str], db_path: Path = SUMMARIES_DB_PATH) -> None:
    """Insert video IDs as pending rows; existing primary keys are left unchanged."""
    init_db(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.executemany(
            "INSERT OR IGNORE INTO summaries (video_id, status) VALUES (?, 'pending')",
            [(vid,) for vid in dict.fromkeys(video_ids)],
        )
        con.commit()
    finally:
        con.close()


# @lat: [[summaries#Read API]]
def load_summaries(video_ids: list[str], db_path: Path = SUMMARIES_DB_PATH) -> dict[str, str | None]:
    """Return summary text for each id; None when missing or status is not ok."""
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
                f"SELECT video_id, text FROM summaries WHERE video_id IN ({qmarks}) AND status = 'ok'",
                chunk,
            ).fetchall()
            for vid, text in rows:
                out[str(vid)] = text
        return out
    finally:
        con.close()


# @lat: [[summaries#Fetch loop]]
async def fetch_summaries(db_path: Path = SUMMARIES_DB_PATH) -> None:
    """Process pending/error rows until none remain or attempts cap is reached."""
    init_db(db_path)
    agent = _build_agent()
    model_name = os.environ.get(_MODEL_ENV, _DEFAULT_MODEL)

    from youtubebrain.ingest import WATCH_HISTORY_PATH, _video_id, load_watch_history  # noqa: PLC0415

    videos = load_watch_history(WATCH_HISTORY_PATH)
    titles: dict[str, str] = {}
    for video in videos:
        if video.title_url is None:
            continue
        vid = _video_id(video.title_url)
        titles[vid] = video.title.removeprefix("Watched ")

    descriptions = _load_descriptions_cache()
    all_ids = list(titles.keys())
    transcripts = load_transcripts(all_ids, TRANSCRIPTS_DB_PATH)

    con = sqlite3.connect(db_path)
    try:
        while True:
            # @lat: [[summaries#Re-summarize policy]]
            row = con.execute(
                """
                SELECT video_id FROM summaries
                WHERE status IN ('pending', 'error')
                  AND attempts < ?
                ORDER BY attempts ASC, RANDOM() LIMIT 1
                """,
                (_MAX_ATTEMPTS,),
            ).fetchone()
            if row is None:
                break
            video_id = str(row[0])
            title = titles.get(video_id, video_id)
            description = descriptions.get(video_id)
            transcript = transcripts.get(video_id)
            status, text, err = await summarize_one(
                video_id,
                title,
                description,
                transcript,
                agent,
            )
            con.execute(
                """
                UPDATE summaries SET
                    status=?,
                    text=?,
                    model=?,
                    error_message=?,
                    attempts=attempts+1,
                    fetched_at=CURRENT_TIMESTAMP,
                    last_attempt=CURRENT_TIMESTAMP
                WHERE video_id=?
                """,
                (status, text, model_name if status == "ok" else None, err, video_id),
            )
            con.commit()
            counts = con.execute(
                "SELECT COALESCE(SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END), 0), COUNT(*) FROM summaries",
            ).fetchone()
            n_ok, n_total = int(counts[0]), int(counts[1])
            pct = 100.0 * n_ok / n_total if n_total else 0.0
            logger.info(f"Summary {video_id}: {status} ({n_ok}/{n_total} summarized, {pct:.1f}%)")
    finally:
        con.close()


async def _main_async() -> None:
    from youtubebrain.ingest import WATCH_HISTORY_PATH, _video_id, load_watch_history  # noqa: PLC0415

    logger.info("Starting summaries fetcher.")
    videos = load_watch_history(WATCH_HISTORY_PATH)
    ids = [_video_id(v.title_url) for v in videos if v.title_url is not None]
    init_db()
    enqueue(ids)
    logger.info(f"Enqueued {len(ids)} video ids; starting fetch loop.")
    await fetch_summaries()
    logger.info("Summaries fetcher finished.")


# @lat: [[summaries#CLI entry]]
def main() -> None:
    """Load Takeout IDs, enqueue pending rows, then run the resumable fetch loop."""
    asyncio.run(_main_async())
