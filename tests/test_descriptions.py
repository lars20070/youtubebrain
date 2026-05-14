"""Unit tests for the YouTube Data API description fetcher and cache."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from youtubebrain.descriptions import YOUTUBE_API_URL, fetch_descriptions


def _api_response(items: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(200, json={"items": items})


def _video_item(vid: str, description: str) -> dict[str, object]:
    return {"id": vid, "snippet": {"description": description}}


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY_YOUTUBE", "test-key")


# @lat: [[descriptions#Tests#Uses cache first]]
@pytest.mark.asyncio
async def test_fetch_descriptions_uses_cache_first(tmp_path: Path) -> None:
    """Pre-seeded cache entries are returned without any HTTP call."""
    cache_path = tmp_path / "descriptions.json"
    cache_path.write_text(json.dumps({"abc": "hello"}))
    with respx.mock(assert_all_called=False) as router:
        route = router.get(YOUTUBE_API_URL)
        result = await fetch_descriptions(["abc"], cache_path)
    assert result == {"abc": "hello"}
    assert not route.called


# @lat: [[descriptions#Tests#Batches in fifties]]
@pytest.mark.asyncio
async def test_fetch_descriptions_batches_in_50s(tmp_path: Path) -> None:
    """75 uncached IDs are fetched in exactly two batches."""
    cache_path = tmp_path / "descriptions.json"
    ids = [f"id{i:03d}" for i in range(75)]
    with respx.mock() as router:
        route = router.get(YOUTUBE_API_URL).mock(
            side_effect=lambda request: _api_response(
                [_video_item(vid, f"desc-{vid}") for vid in request.url.params["id"].split(",")],
            ),
        )
        result = await fetch_descriptions(ids, cache_path)
    assert route.call_count == 2
    assert len(result) == 75
    assert result["id000"] == "desc-id000"
    assert result["id074"] == "desc-id074"


# @lat: [[descriptions#Tests#Stores None for missing]]
@pytest.mark.asyncio
async def test_fetch_descriptions_stores_none_for_missing(tmp_path: Path) -> None:
    """IDs the API does not return are cached as None and surfaced as None to the caller."""
    cache_path = tmp_path / "descriptions.json"
    with respx.mock() as router:
        router.get(YOUTUBE_API_URL).mock(return_value=_api_response([_video_item("present", "yes")]))
        result = await fetch_descriptions(["present", "gone1", "gone2"], cache_path)
    assert result == {"present": "yes", "gone1": None, "gone2": None}
    cache = json.loads(cache_path.read_text())
    assert cache == {"present": "yes", "gone1": None, "gone2": None}


# @lat: [[descriptions#Tests#Persists cache per batch]]
@pytest.mark.asyncio
async def test_fetch_descriptions_writes_cache_after_each_batch(tmp_path: Path) -> None:
    """The cache file contains the first batch even if a later batch fails."""
    cache_path = tmp_path / "descriptions.json"
    ids = [f"id{i:03d}" for i in range(60)]
    responses = [
        _api_response([_video_item(vid, f"desc-{vid}") for vid in ids[:50]]),
        httpx.Response(500, json={"error": "boom"}),
    ]
    with respx.mock() as router:
        router.get(YOUTUBE_API_URL).mock(side_effect=responses)
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_descriptions(ids, cache_path)
    cache = json.loads(cache_path.read_text())
    assert cache["id000"] == "desc-id000"
    assert cache["id049"] == "desc-id049"
    assert "id050" not in cache


# @lat: [[descriptions#Tests#Raises without API key]]
@pytest.mark.asyncio
async def test_fetch_descriptions_raises_without_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch_descriptions raises RuntimeError if API_KEY_YOUTUBE is unset."""
    monkeypatch.delenv("API_KEY_YOUTUBE", raising=False)
    monkeypatch.setattr("youtubebrain.descriptions.load_dotenv", lambda: None)
    cache_path = tmp_path / "descriptions.json"
    with pytest.raises(RuntimeError, match="API_KEY_YOUTUBE"):
        await fetch_descriptions(["abc"], cache_path)


# @lat: [[descriptions#Tests#Deduplicates input ids]]
@pytest.mark.asyncio
async def test_fetch_descriptions_deduplicates_input(tmp_path: Path) -> None:
    """Duplicate input IDs result in a single fetched entry and a single result key."""
    cache_path = tmp_path / "descriptions.json"
    with respx.mock() as router:
        route = router.get(YOUTUBE_API_URL).mock(return_value=_api_response([_video_item("abc", "x")]))
        result = await fetch_descriptions(["abc", "abc", "abc"], cache_path)
    assert route.call_count == 1
    assert result == {"abc": "x"}
