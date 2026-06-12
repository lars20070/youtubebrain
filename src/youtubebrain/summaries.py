"""Generate per-video summaries via a configurable LLM provider with resumable SQLite storage."""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING

from pydantic_ai import Agent

from youtubebrain import config, logger, takeout
from youtubebrain.cache import StatusCache
from youtubebrain.provider import create_model
from youtubebrain.transcripts import load_transcripts

if TYPE_CHECKING:
    from pathlib import Path

_TRANSCRIPT_CHAR_LIMIT = 12000
_DEFAULT_MODEL = "qwen3:32b"
_MAX_ATTEMPTS = 5

_MODEL_ENV = "MODEL"
_SUMMARIES_EXTRA_COLUMNS = ("model TEXT",)

# @lat: [[summaries#System prompt]]
SYSTEM_PROMPT = """\
You produce a concise multi-paragraph summary of a YouTube video from the inputs below.

Ignore sponsorships, Patreon pitches, channel-membership pitches, merch-store mentions, hashtags, \
and sponsor read-outs inside transcripts. Focus on the substantive content of the video.

Keep the summary focused and readable (roughly two to four short paragraphs).\
"""


def _load_descriptions_cache(path: Path | None = None) -> dict[str, str | None]:
    """Read the descriptions JSON cache; return an empty dict if absent."""
    path = config.DESCRIPTIONS_CACHE_PATH if path is None else path
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
def init_db(db_path: Path | None = None) -> None:
    """Create the summaries table and indexes if missing; enable WAL."""
    _cache(db_path).init_db()


# @lat: [[summaries#Enqueue]]
def enqueue(video_ids: list[str], db_path: Path | None = None) -> None:
    """Insert video IDs as pending rows; existing primary keys are left unchanged."""
    _cache(db_path).enqueue(video_ids)


# @lat: [[summaries#Read API]]
def load_summaries(video_ids: list[str], db_path: Path | None = None) -> dict[str, str | None]:
    """Return summary text for each id; None when missing or status is not ok."""
    return _cache(db_path).load_ok(video_ids)


# @lat: [[summaries#Fetch loop]]
async def fetch_summaries(db_path: Path | None = None) -> None:
    """Process pending/error rows until none remain or attempts cap is reached."""
    status_cache = _cache(db_path)
    status_cache.init_db()
    agent = _build_agent()
    model_name = os.environ.get(_MODEL_ENV, _DEFAULT_MODEL)

    videos = takeout.load_watch_history(config.WATCH_HISTORY_PATH)
    titles: dict[str, str] = {}
    for video in videos:
        if video.title_url is None:
            continue
        vid = takeout.video_id(video.title_url)
        titles[vid] = video.title.removeprefix("Watched ")

    descriptions = _load_descriptions_cache()
    all_ids = list(titles.keys())
    transcripts = load_transcripts(all_ids)

    con = status_cache.connect()
    try:
        while True:
            # @lat: [[summaries#Re-summarize policy]]
            video_id = status_cache.next_retryable(con, max_attempts=_MAX_ATTEMPTS)
            if video_id is None:
                break
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
            status_cache.record_result(
                con,
                video_id,
                status,
                text=text,
                error_message=err,
                extra={"model": model_name if status == "ok" else None},
            )
            n_ok, n_total = status_cache.counts(con)
            pct = 100.0 * n_ok / n_total if n_total else 0.0
            logger.info(f"Summary {video_id}: {status} ({n_ok}/{n_total} summarized, {pct:.1f}%)")
    finally:
        con.close()


async def _main_async() -> None:
    logger.info("Starting summaries fetcher.")
    ids = takeout.load_video_ids()
    init_db()
    enqueue(ids)
    logger.info(f"Enqueued {len(ids)} video ids; starting fetch loop.")
    await fetch_summaries()
    logger.info("Summaries fetcher finished.")


# @lat: [[summaries#CLI entry]]
def main() -> None:
    """Load Takeout IDs, enqueue pending rows, then run the resumable fetch loop."""
    asyncio.run(_main_async())


def _cache(db_path: Path | None = None) -> StatusCache:
    resolved_path = config.SUMMARIES_DB_PATH if db_path is None else db_path
    return StatusCache(resolved_path, "summaries", _SUMMARIES_EXTRA_COLUMNS)
