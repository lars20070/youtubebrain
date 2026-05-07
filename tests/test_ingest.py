"""Unit tests for the Takeout watch-history ingest loader."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from youtubebrain import ingest
from youtubebrain.ingest import WATCH_HISTORY_PATH, load_watch_history, main
from youtubebrain.models import WatchedVideo

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


def _write_history(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    path = tmp_path / "watch-history.json"
    path.write_text(json.dumps(records))
    return path


# @lat: [[ingest#Tests#Parses valid record]]
def test_parses_valid_record(tmp_path: Path) -> None:
    """A minimal valid JSON array round-trips into list[WatchedVideo]."""
    path = _write_history(tmp_path, [_VALID_RECORD])
    videos = load_watch_history(path)
    assert len(videos) == 1
    assert isinstance(videos[0], WatchedVideo)
    assert videos[0].title == "Watched Test Video"
    assert videos[0].activity_controls == ["YouTube watch history"]
    assert str(videos[0].title_url) == "https://www.youtube.com/watch?v=abc123"


# @lat: [[ingest#Tests#Handles empty array]]
def test_handles_empty_array(tmp_path: Path) -> None:
    """An empty JSON array yields an empty list."""
    path = _write_history(tmp_path, [])
    assert load_watch_history(path) == []


# @lat: [[ingest#Tests#Handles optional fields]]
def test_handles_optional_fields(tmp_path: Path) -> None:
    """Records without titleUrl/subtitles/description still parse."""
    record = {k: v for k, v in _VALID_RECORD.items() if k not in {"titleUrl", "subtitles"}}
    path = _write_history(tmp_path, [record])
    videos = load_watch_history(path)
    assert videos[0].title_url is None
    assert videos[0].subtitles == []
    assert videos[0].description is None


# @lat: [[ingest#Tests#Rejects unknown field]]
def test_rejects_unknown_field(tmp_path: Path) -> None:
    """Schema drift raises ValidationError due to extra=forbid."""
    record = {**_VALID_RECORD, "unexpectedField": "boom"}
    path = _write_history(tmp_path, [record])
    with pytest.raises(ValidationError):
        load_watch_history(path)


# @lat: [[ingest#Tests#main prints titles]]
def test_main_prints_titles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() prints every video title to stdout, one per line."""
    record_b = {**_VALID_RECORD, "title": "Second Video"}
    path = _write_history(tmp_path, [_VALID_RECORD, record_b])
    monkeypatch.setattr(ingest, "WATCH_HISTORY_PATH", path)
    main()
    out = capsys.readouterr().out
    assert out.splitlines() == ["Watched Test Video", "Second Video"]


# @lat: [[ingest#Tests#Filters unresolved titles]]
def test_filters_unresolved_titles(tmp_path: Path) -> None:
    """Records whose title is a URL placeholder are silently dropped."""
    path = _write_history(tmp_path, [_VALID_RECORD, _UNRESOLVED_RECORD])
    videos = load_watch_history(path)
    assert len(videos) == 1
    assert videos[0].title == "Watched Test Video"


# @lat: [[ingest#Tests#main skips unresolved titles]]
def test_main_skips_unresolved_titles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() does not print URL-shaped placeholder titles."""
    path = _write_history(tmp_path, [_VALID_RECORD, _UNRESOLVED_RECORD])
    monkeypatch.setattr(ingest, "WATCH_HISTORY_PATH", path)
    main()
    out = capsys.readouterr().out
    assert out.splitlines() == ["Watched Test Video"]


# @lat: [[ingest#Tests#Default path constant]]
def test_default_path_constant() -> None:
    """The default path points at the Takeout watch-history.json location."""
    assert Path("Takeout/YouTube and YouTube Music/history/watch-history.json") == WATCH_HISTORY_PATH
