"""Unit tests for Takeout parsing, filtering, and id extraction helpers."""

import json
from pathlib import Path

import pytest
from pydantic import HttpUrl, TypeAdapter, ValidationError

from youtubebrain import config
from youtubebrain.models import WatchedVideo
from youtubebrain.takeout import channel_id, load_video_ids, load_watch_history, video_id

_VALID_RECORD: dict[str, object] = {
    "header": "YouTube",
    "title": "Watched Test Video",
    "titleUrl": "https://www.youtube.com/watch?v=abc123",
    "subtitles": [{"name": "Test Channel", "url": "https://www.youtube.com/channel/UCxxx"}],
    "time": "2026-05-07T08:39:47.023Z",
    "products": ["YouTube"],
    "activityControls": ["YouTube watch history"],
}

_UNRESOLVED_RECORD: dict[str, object] = {
    "header": "YouTube",
    "title": "Watched https://www.youtube.com/watch?v=B3ij-TSeXMs",
    "titleUrl": "https://www.youtube.com/watch?v=B3ij-TSeXMs",
    "time": "2025-10-11T10:33:36.215Z",
    "products": ["YouTube"],
    "activityControls": ["YouTube watch history"],
}

_NON_WATCH_RECORD: dict[str, object] = {
    "header": "YouTube",
    "title": "Viewed G'day all - just letting you know about today's episode delay.",
    "titleUrl": "https://www.youtube.com/post/UgkxMzjzCEjX7KL27rz",
    "time": "2025-10-11T09:00:00.000Z",
    "products": ["YouTube"],
    "activityControls": ["YouTube watch history"],
}

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


def _http_url(value: str) -> HttpUrl:
    return _HTTP_URL_ADAPTER.validate_python(value)


def _write_history(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    path = tmp_path / "watch-history.json"
    path.write_text(json.dumps(records))
    return path


# @lat: [[takeout#Tests#Parses valid record]]
def test_parses_valid_record(tmp_path: Path) -> None:
    """A minimal valid JSON array round-trips into list[WatchedVideo]."""
    path = _write_history(tmp_path, [_VALID_RECORD])
    videos = load_watch_history(path)
    assert len(videos) == 1
    assert isinstance(videos[0], WatchedVideo)
    assert videos[0].title == "Watched Test Video"
    assert videos[0].activity_controls == ["YouTube watch history"]
    assert str(videos[0].title_url) == "https://www.youtube.com/watch?v=abc123"


# @lat: [[takeout#Tests#Handles empty array]]
def test_handles_empty_array(tmp_path: Path) -> None:
    """An empty JSON array yields an empty list."""
    path = _write_history(tmp_path, [])
    assert load_watch_history(path) == []


# @lat: [[takeout#Tests#Handles optional fields]]
def test_handles_optional_fields(tmp_path: Path) -> None:
    """Records without titleUrl/subtitles/description still parse."""
    record = {k: v for k, v in _VALID_RECORD.items() if k not in {"titleUrl", "subtitles"}}
    path = _write_history(tmp_path, [record])
    videos = load_watch_history(path)
    assert videos[0].title_url is None
    assert videos[0].subtitles == []
    assert videos[0].description is None


# @lat: [[takeout#Tests#Rejects unknown field]]
def test_rejects_unknown_field(tmp_path: Path) -> None:
    """Schema drift raises ValidationError due to extra=forbid."""
    record = {**_VALID_RECORD, "unexpectedField": "boom"}
    path = _write_history(tmp_path, [record])
    with pytest.raises(ValidationError):
        load_watch_history(path)


# @lat: [[takeout#Tests#Filters unresolved titles]]
def test_filters_unresolved_titles(tmp_path: Path) -> None:
    """Records whose title is a URL placeholder are silently dropped."""
    path = _write_history(tmp_path, [_VALID_RECORD, _UNRESOLVED_RECORD])
    videos = load_watch_history(path)
    assert len(videos) == 1
    assert videos[0].title == "Watched Test Video"


# @lat: [[takeout#Tests#Filters non-watch entries]]
def test_filters_non_watch_entries(tmp_path: Path) -> None:
    """Records whose title does not start with 'Watched ' are silently dropped."""
    path = _write_history(tmp_path, [_VALID_RECORD, _NON_WATCH_RECORD])
    videos = load_watch_history(path)
    assert len(videos) == 1
    assert videos[0].title == "Watched Test Video"


# @lat: [[takeout#Tests#Load video ids filters records]]
def test_load_video_ids_filters_records(tmp_path: Path) -> None:
    """Only kept records with a title_url contribute an id; filtered and URL-less records are skipped."""
    no_url_record = {k: v for k, v in _VALID_RECORD.items() if k != "titleUrl"}
    path = _write_history(tmp_path, [_VALID_RECORD, no_url_record, _UNRESOLVED_RECORD, _NON_WATCH_RECORD])
    assert load_video_ids(path) == ["abc123"]


# @lat: [[takeout#Tests#Load video ids default path]]
def test_load_video_ids_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling load_video_ids without a path reads config.WATCH_HISTORY_PATH."""
    history = _write_history(tmp_path, [_VALID_RECORD])
    monkeypatch.setattr(config, "WATCH_HISTORY_PATH", history)
    assert load_video_ids() == ["abc123"]


# @lat: [[takeout#Tests#Default path constant]]
def test_default_path_constant() -> None:
    """The default path points at the Takeout watch-history.json location."""
    assert Path("Takeout/YouTube and YouTube Music/history/watch-history.json") == config.WATCH_HISTORY_PATH


# @lat: [[takeout#Tests#Video ID extraction]]
def test_video_id_extracts_from_url() -> None:
    """The 'v' query parameter is returned as the video ID."""
    url = _http_url("https://www.youtube.com/watch?v=JWWDqbcQoXA")
    assert video_id(url) == "JWWDqbcQoXA"


# @lat: [[takeout#Tests#Video ID raises without v param]]
def test_video_id_raises_without_v_param() -> None:
    """URLs lacking a 'v' query parameter raise ValueError."""
    url = _http_url("https://www.youtube.com/post/UgkxMzjzCEjX7KL27rz")
    with pytest.raises(ValueError, match="'v' query parameter"):
        video_id(url)


# @lat: [[takeout#Tests#Channel ID extraction]]
def test_channel_id_extracts_from_url() -> None:
    """The last path segment after /channel/ is returned as the channel ID."""
    url = _http_url("https://www.youtube.com/channel/UCvPXiKxH-eH9xq-80vpgmKQ")
    assert channel_id(url) == "UCvPXiKxH-eH9xq-80vpgmKQ"


# @lat: [[takeout#Tests#Channel ID raises on bad URL]]
def test_channel_id_raises_on_bad_url() -> None:
    """URLs that are not /channel/<id> raise ValueError."""
    url = _http_url("https://www.youtube.com/@EpicHistory")
    with pytest.raises(ValueError, match="/channel/<id>"):
        channel_id(url)
