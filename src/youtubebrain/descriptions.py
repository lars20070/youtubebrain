"""Fetch YouTube video descriptions via the YouTube Data API v3 into SQLite."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx

from youtubebrain import config, logger, takeout
from youtubebrain.cache import StatusCache

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"
_BATCH_SIZE = 50
_MAX_ATTEMPTS = 5
_API_KEY_ENV = "API_KEY_YOUTUBE"
_HTTP_TIMEOUT = 30.0


# @lat: [[descriptions#API key requirement]]
def _get_api_key() -> str:
    """Read the YouTube Data API key from the environment, loading .env on the way."""
    config.load_env()
    key = os.environ.get(_API_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"Missing {_API_KEY_ENV} environment variable. "
            f"Get a key from https://console.cloud.google.com/ "
            f"(enable 'YouTube Data API v3') and add it to .env."
        )
    return key


def _chunks(seq: list[str], n: int = _BATCH_SIZE) -> Iterator[list[str]]:
    """Yield successive n-sized slices from seq."""
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


# @lat: [[descriptions#API client]]
def _fetch_batch(client: httpx.Client, ids: list[str], api_key: str) -> dict[str, str]:
    """Fetch descriptions for up to 50 video IDs; return {id: description} for items the API returned."""
    response = client.get(
        YOUTUBE_API_URL,
        params={"part": "snippet", "id": ",".join(ids), "key": api_key, "maxResults": _BATCH_SIZE},
    )
    response.raise_for_status()
    items = response.json().get("items", [])
    return {item["id"]: item["snippet"].get("description", "") for item in items}


# @lat: [[descriptions#SQLite schema]]
def init_db(db_path: Path | None = None) -> None:
    """Create the descriptions table and indexes if missing; enable WAL."""
    _cache(db_path).init_db()


# @lat: [[descriptions#Enqueue]]
def enqueue(video_ids: list[str], db_path: Path | None = None) -> None:
    """Insert video IDs as pending rows; existing primary keys are left unchanged."""
    _cache(db_path).enqueue(video_ids)


# @lat: [[descriptions#Read API]]
def load_descriptions(video_ids: list[str], db_path: Path | None = None) -> dict[str, str | None]:
    """Return description text for each id; None when missing or status is not ok."""
    return _cache(db_path).load_ok(video_ids)


# @lat: [[descriptions#Fetch loop]]
def fetch_descriptions(db_path: Path | None = None) -> None:
    """Process pending/error rows in 50-id batches until none remain or attempts cap is reached."""
    status_cache = _cache(db_path)
    status_cache.init_db()
    con = status_cache.connect()
    try:
        pending_ids = status_cache.pending_ids(con, max_attempts=_MAX_ATTEMPTS)
        if not pending_ids:
            logger.info("Descriptions: no pending rows; nothing to fetch.")
            return

        api_key = _get_api_key()
        batches = list(_chunks(pending_ids))
        logger.info(f"Fetching {len(pending_ids)} descriptions in {len(batches)} batch(es).")
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            for i, batch in enumerate(batches, start=1):
                try:
                    fetched = _fetch_batch(client, batch, api_key)
                except httpx.HTTPError as exc:
                    # @lat: [[descriptions#API failures]]
                    for vid in batch:
                        status_cache.record_attempt(con, vid, "error", str(exc))
                    logger.warning(f"Batch {i}/{len(batches)} failed ({len(batch)} ids, will retry next run): {exc!r}")
                    continue

                ok_count = 0
                missing_count = 0
                for vid in batch:
                    description = fetched.get(vid)
                    if description is None:
                        # @lat: [[descriptions#Missing videos]]
                        status_cache.record_result(con, vid, "missing", text=None, error_message=None)
                        missing_count += 1
                        continue
                    status_cache.record_result(con, vid, "ok", text=description, error_message=None)
                    ok_count += 1
                logger.info(f"Batch {i}/{len(batches)}: ok={ok_count}, missing={missing_count}.")

        n_ok, n_total = status_cache.counts(con)
        n_missing = int(con.execute("SELECT COUNT(*) FROM descriptions WHERE status = 'missing'").fetchone()[0])
        n_retryable_error = int(
            con.execute(
                "SELECT COUNT(*) FROM descriptions WHERE status = 'error' AND attempts < ?",
                (_MAX_ATTEMPTS,),
            ).fetchone()[0],
        )
        logger.info(
            f"Descriptions ready: ok={n_ok}, missing={n_missing}, retryable_errors={n_retryable_error}, total={n_total}.",
        )
    finally:
        con.close()


def _remove_legacy_json_cache() -> None:
    legacy_path = config.DESCRIPTIONS_CACHE_PATH
    legacy_tmp = legacy_path.with_suffix(f"{legacy_path.suffix}.tmp")
    removed: list[str] = []
    for path in (legacy_path, legacy_tmp):
        if path.exists():
            path.unlink()
            removed.append(str(path))
    if removed:
        logger.info(f"Removed legacy descriptions cache file(s): {', '.join(removed)}")


# @lat: [[descriptions#CLI entry]]
def main() -> None:
    """Load Takeout IDs, enqueue pending rows, then run the resumable descriptions fetch loop."""
    logger.info("Starting descriptions fetcher.")
    _remove_legacy_json_cache()
    ids = takeout.load_video_ids()
    init_db()
    enqueue(ids)
    logger.info(f"Enqueued {len(ids)} video ids; starting fetch loop.")
    fetch_descriptions()
    logger.info("Descriptions fetcher finished.")


def _cache(db_path: Path | None = None) -> StatusCache:
    resolved_path = config.DESCRIPTIONS_DB_PATH if db_path is None else db_path
    return StatusCache(resolved_path, "descriptions")
