"""Unit tests for the Takeout watch-history ingest loader."""

import json
from pathlib import Path

import pytest
from pydantic import HttpUrl, TypeAdapter, ValidationError

from youtubebrain import ingest
from youtubebrain.ingest import (
    MARKDOWN_RAW_DIR,
    WATCH_HISTORY_PATH,
    _render_markdown,
    _video_id,
    load_watch_history,
    main,
    write_markdown,
)
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


def _build_video(**overrides: object) -> WatchedVideo:
    record = {**_VALID_RECORD, **overrides}
    return TypeAdapter(WatchedVideo).validate_python(record)


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


# @lat: [[ingest#Tests#Filters unresolved titles]]
def test_filters_unresolved_titles(tmp_path: Path) -> None:
    """Records whose title is a URL placeholder are silently dropped."""
    path = _write_history(tmp_path, [_VALID_RECORD, _UNRESOLVED_RECORD])
    videos = load_watch_history(path)
    assert len(videos) == 1
    assert videos[0].title == "Watched Test Video"


# @lat: [[ingest#Tests#Filters non-watch entries]]
def test_filters_non_watch_entries(tmp_path: Path) -> None:
    """Records whose title does not start with 'Watched ' are silently dropped."""
    path = _write_history(tmp_path, [_VALID_RECORD, _NON_WATCH_RECORD])
    videos = load_watch_history(path)
    assert len(videos) == 1
    assert videos[0].title == "Watched Test Video"


# @lat: [[ingest#Tests#Default path constant]]
def test_default_path_constant() -> None:
    """The default path points at the Takeout watch-history.json location."""
    assert Path("Takeout/YouTube and YouTube Music/history/watch-history.json") == WATCH_HISTORY_PATH


# @lat: [[ingest#Tests#Video ID extraction]]
def test_video_id_extracts_from_url() -> None:
    """The 'v' query parameter is returned as the video ID."""
    url = _http_url("https://www.youtube.com/watch?v=JWWDqbcQoXA")
    assert _video_id(url) == "JWWDqbcQoXA"


# @lat: [[ingest#Tests#Video ID raises without v param]]
def test_video_id_raises_without_v_param() -> None:
    """URLs lacking a 'v' query parameter raise ValueError."""
    url = _http_url("https://www.youtube.com/post/UgkxMzjzCEjX7KL27rz")
    with pytest.raises(ValueError, match="'v' query parameter"):
        _video_id(url)


# @lat: [[ingest#Tests#Render markdown fields]]
def test_render_markdown_contains_all_fields() -> None:
    """Rendered markdown includes title, titleUrl, channel name+url and ISO time."""
    video = _build_video()
    body = _render_markdown(video)
    assert "## Video" in body
    assert "- Title: Watched Test Video" in body
    assert "https://www.youtube.com/watch?v=abc123" in body
    assert "Test Channel" in body
    assert "https://www.youtube.com/channel/UCxxx" in body
    assert "2026-05-07T08:39:47.023000+00:00" in body


# @lat: [[ingest#Tests#Render multiple channels]]
def test_render_markdown_lists_multiple_channels() -> None:
    """A record with multiple subtitles renders each as its own bullet."""
    video = _build_video(
        subtitles=[
            {"name": "Channel One", "url": "https://www.youtube.com/channel/UC1"},
            {"name": "Channel Two", "url": "https://www.youtube.com/channel/UC2"},
        ],
    )
    body = _render_markdown(video)
    assert "- [Channel One](https://www.youtube.com/channel/UC1)" in body
    assert "- [Channel Two](https://www.youtube.com/channel/UC2)" in body


# @lat: [[ingest#Tests#Render empty subtitles]]
def test_render_markdown_empty_subtitles() -> None:
    """An empty subtitles list renders a (none) placeholder under Channels."""
    video = _build_video(subtitles=[])
    body = _render_markdown(video)
    assert "## Channels" in body
    assert "_(none)_" in body


# @lat: [[ingest#Tests#Write markdown creates file]]
def test_write_markdown_creates_named_file(tmp_path: Path) -> None:
    """write_markdown writes a file named <video_id>.md in out_dir."""
    video = _build_video(titleUrl="https://www.youtube.com/watch?v=JWWDqbcQoXA")
    path = write_markdown(video, tmp_path)
    assert path == tmp_path / "JWWDqbcQoXA.md"
    assert path.exists()
    assert "- Title: Watched Test Video" in path.read_text()


# @lat: [[ingest#Tests#Write markdown overwrites]]
def test_write_markdown_overwrites_existing(tmp_path: Path) -> None:
    """A second call replaces the file contents (idempotent re-ingest)."""
    video = _build_video()
    path = write_markdown(video, tmp_path)
    path.write_text("stale")
    write_markdown(video, tmp_path)
    assert "stale" not in path.read_text()
    assert "- Title: Watched Test Video" in path.read_text()


# @lat: [[ingest#Tests#Write markdown requires title URL]]
def test_write_markdown_raises_without_title_url(tmp_path: Path) -> None:
    """write_markdown raises ValueError when the record has no titleUrl."""
    record = {k: v for k, v in _VALID_RECORD.items() if k != "titleUrl"}
    video = TypeAdapter(WatchedVideo).validate_python(record)
    with pytest.raises(ValueError, match="title_url"):
        write_markdown(video, tmp_path)


# @lat: [[ingest#Tests#main writes files]]
def test_main_writes_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() writes one markdown file per kept video and prints nothing to stdout."""
    record_b = {
        **_VALID_RECORD,
        "title": "Watched Second Video",
        "titleUrl": "https://www.youtube.com/watch?v=JWWDqbcQoXA",
    }
    history = _write_history(tmp_path, [_VALID_RECORD, record_b])
    out_dir = tmp_path / "out"
    monkeypatch.setattr(ingest, "WATCH_HISTORY_PATH", history)
    monkeypatch.setattr(ingest, "MARKDOWN_RAW_DIR", out_dir)
    main()
    assert capsys.readouterr().out == ""
    assert (out_dir / "abc123.md").exists()
    assert (out_dir / "JWWDqbcQoXA.md").exists()


# @lat: [[ingest#Tests#main skips unresolved]]
def test_main_skips_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() does not write a file for unresolved (URL-placeholder) records."""
    history = _write_history(tmp_path, [_VALID_RECORD, _UNRESOLVED_RECORD])
    out_dir = tmp_path / "out"
    monkeypatch.setattr(ingest, "WATCH_HISTORY_PATH", history)
    monkeypatch.setattr(ingest, "MARKDOWN_RAW_DIR", out_dir)
    main()
    files = sorted(p.name for p in out_dir.iterdir())
    assert files == ["abc123.md"]


# @lat: [[ingest#Tests#main skips non-watch]]
def test_main_skips_non_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() does not write a file for non-watch (community-post) activity."""
    history = _write_history(tmp_path, [_VALID_RECORD, _NON_WATCH_RECORD])
    out_dir = tmp_path / "out"
    monkeypatch.setattr(ingest, "WATCH_HISTORY_PATH", history)
    monkeypatch.setattr(ingest, "MARKDOWN_RAW_DIR", out_dir)
    main()
    files = sorted(p.name for p in out_dir.iterdir())
    assert files == ["abc123.md"]


# @lat: [[ingest#Tests#Default output directory]]
def test_default_output_directory() -> None:
    """MARKDOWN_RAW_DIR points at the repo-root-relative raw markdown folder."""
    assert Path("Markdown/raw") == MARKDOWN_RAW_DIR
