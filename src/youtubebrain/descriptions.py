"""Fetch YouTube video descriptions via the YouTube Data API v3 with on-disk caching."""

import json
import os
from collections.abc import Iterable
from pathlib import Path

import httpx
from dotenv import load_dotenv

from youtubebrain import logger

DESCRIPTIONS_CACHE_PATH = Path("Markdown/.cache/descriptions.json")
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"
_BATCH_SIZE = 50
_API_KEY_ENV = "API_KEY_YOUTUBE"
_HTTP_TIMEOUT = 30.0


# @lat: [[descriptions#Cache]]
def _load_cache(path: Path) -> dict[str, str | None]:
    """Read the JSON cache from disk, returning an empty dict if absent."""
    if not path.exists():
        return {}
    return json.loads(path.read_text())


# @lat: [[descriptions#Cache]]
def _save_cache(path: Path, cache: dict[str, str | None]) -> None:
    """Persist the cache to disk, ensuring the parent directory exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True))


# @lat: [[descriptions#API key requirement]]
def _get_api_key() -> str:
    """Read the YouTube Data API key from the environment, loading .env on the way."""
    load_dotenv()
    key = os.environ.get(_API_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"Missing {_API_KEY_ENV} environment variable. "
            f"Get a key from https://console.cloud.google.com/ "
            f"(enable 'YouTube Data API v3') and add it to .env."
        )
    return key


def _chunks(seq: list[str], n: int = _BATCH_SIZE) -> Iterable[list[str]]:
    """Yield successive n-sized slices from seq."""
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


# @lat: [[descriptions#API client]]
async def _fetch_batch(client: httpx.AsyncClient, ids: list[str], api_key: str) -> dict[str, str]:
    """Fetch descriptions for up to 50 video IDs; return {id: description} for items the API returned."""
    response = await client.get(
        YOUTUBE_API_URL,
        params={"part": "snippet", "id": ",".join(ids), "key": api_key, "maxResults": _BATCH_SIZE},
    )
    response.raise_for_status()
    items = response.json().get("items", [])
    return {item["id"]: item["snippet"].get("description", "") for item in items}


# @lat: [[descriptions#API client]]
async def fetch_descriptions(
    video_ids: list[str],
    cache_path: Path = DESCRIPTIONS_CACHE_PATH,
) -> dict[str, str | None]:
    """Return {video_id: description_or_None} for every input ID, using the cache to skip prior fetches."""
    cache = _load_cache(cache_path)
    unique_ids = list(dict.fromkeys(video_ids))
    missing = [vid for vid in unique_ids if vid not in cache]
    cached_count = len(unique_ids) - len(missing)
    logger.info(f"Descriptions: {cached_count} cached, {len(missing)} to fetch.")

    failed_count = 0
    if missing:
        api_key = _get_api_key()
        batches = list(_chunks(missing))
        logger.info(f"Fetching {len(missing)} descriptions in {len(batches)} batch(es).")
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            for i, batch in enumerate(batches, start=1):
                try:
                    fetched = await _fetch_batch(client, batch, api_key)
                except httpx.HTTPError as e:
                    # @lat: [[descriptions#API failures]]
                    failed_count += len(batch)
                    logger.warning(f"Batch {i}/{len(batches)} failed ({len(batch)} ids, will retry next run): {e!r}")
                    continue
                for vid in batch:
                    # @lat: [[descriptions#Missing videos]]
                    cache[vid] = fetched.get(vid)
                _save_cache(cache_path, cache)
                logger.info(f"Batch {i}/{len(batches)}: fetched {len(fetched)}/{len(batch)}.")

    result = {vid: cache.get(vid) for vid in unique_ids}
    available = sum(1 for v in result.values() if v is not None)
    logger.info(
        f"Descriptions ready: {available} available, {len(result) - available} unavailable ({failed_count} failed, will retry next run).",
    )
    return result
