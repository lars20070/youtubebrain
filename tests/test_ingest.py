"""Unit tests for ingest markdown rendering and entrypoint wiring."""

import json
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter

from youtubebrain import config, ingest
from youtubebrain.ingest import (
    _render_markdown,
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


def _write_history(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    path = tmp_path / "watch-history.json"
    path.write_text(json.dumps(records))
    return path


def _build_video(**overrides: object) -> WatchedVideo:
    record = {**_VALID_RECORD, **overrides}
    return TypeAdapter(WatchedVideo).validate_python(record)


def _parse_frontmatter(body: str) -> dict[str, object]:
    """Return the YAML frontmatter dict from a rendered markdown body."""
    if not body.startswith("---\n"):
        msg = "body does not start with frontmatter delimiter"
        raise ValueError(msg)
    end = body.index("\n---\n", 4)
    return yaml.safe_load(body[4:end])


def _stub_fetch_descriptions(descriptions: dict[str, str | None]):  # noqa: ANN202
    """Build an async stub for ingest.fetch_descriptions that returns a fixed mapping."""

    async def _stub(video_ids: list[str], cache_path: Path | None = None) -> dict[str, str | None]:  # noqa: ARG001
        return {vid: descriptions.get(vid) for vid in video_ids}

    return _stub


def _stub_load_transcripts_none(video_ids: list[str], db_path: Path | None = None) -> dict[str, str | None]:  # noqa: ARG001
    """Return no transcript text (read from DB would be empty in tests)."""
    return dict.fromkeys(dict.fromkeys(video_ids), None)


def _stub_load_summaries_none(video_ids: list[str], db_path: Path | None = None) -> dict[str, str | None]:  # noqa: ARG001
    """Return no summary text (read from DB would be empty in tests)."""
    return dict.fromkeys(dict.fromkeys(video_ids), None)


# @lat: [[ingest#Tests#Render markdown frontmatter]]
def test_render_markdown_frontmatter() -> None:
    """Rendered markdown frontmatter includes id, url, title, channels with id, and watch_time."""
    video = _build_video()
    body = _render_markdown(video, description="A short clip about something.")
    meta = _parse_frontmatter(body)
    assert meta["id"] == "abc123"
    assert meta["url"] == "https://www.youtube.com/watch?v=abc123"
    assert meta["title"] == "Test Video"
    assert meta["watch_time"] == "2026-05-07T08:39:47.023000+00:00"
    channels = meta["channels"]
    assert isinstance(channels, list)
    assert len(channels) == 1
    assert channels[0] == {
        "name": "Test Channel",
        "id": "UCxxx",
        "url": "https://www.youtube.com/channel/UCxxx",
    }
    assert "## Summary" in body
    assert "## Description" in body
    assert "A short clip about something." in body
    assert "## Transcript" in body


# @lat: [[ingest#Tests#Render summary section]]
def test_render_markdown_summary_section() -> None:
    """Rendered markdown includes Summary before Description and Transcript with supplied body text."""
    video = _build_video()
    body = _render_markdown(video, description="d", transcript="t", summary="summary text")
    summary_pos = body.index("## Summary")
    desc_pos = body.index("## Description")
    transcript_pos = body.index("## Transcript")
    assert summary_pos < desc_pos < transcript_pos
    assert "summary text" in body.split("## Summary")[1].split("## Description")[0]


# @lat: [[ingest#Tests#Render unavailable summary]]
def test_render_markdown_summary_unavailable() -> None:
    """A None summary renders the _(unavailable)_ placeholder under the Summary heading."""
    video = _build_video()
    body = _render_markdown(video, description="d", transcript="t", summary=None)
    assert "## Summary" in body
    assert "_(unavailable)_" in body.split("## Summary")[1].split("## Description")[0]


# @lat: [[ingest#Tests#Render unavailable description]]
def test_render_markdown_unavailable_description() -> None:
    """A None description renders the _(unavailable)_ placeholder under the Description heading."""
    video = _build_video()
    body = _render_markdown(video, description=None)
    assert "## Description" in body
    assert "_(unavailable)_" in body
    assert "## Transcript" in body


# @lat: [[ingest#Tests#Render multiple channels]]
def test_render_markdown_lists_multiple_channels() -> None:
    """A record with multiple subtitles renders each channel as a YAML list entry."""
    video = _build_video(
        subtitles=[
            {"name": "Channel One", "url": "https://www.youtube.com/channel/UC1"},
            {"name": "Channel Two", "url": "https://www.youtube.com/channel/UC2"},
        ],
    )
    meta = _parse_frontmatter(_render_markdown(video))
    channels = meta["channels"]
    assert isinstance(channels, list)
    assert len(channels) == 2
    assert channels[0]["name"] == "Channel One"
    assert channels[0]["id"] == "UC1"
    assert channels[1]["name"] == "Channel Two"
    assert channels[1]["id"] == "UC2"


# @lat: [[ingest#Tests#Render empty subtitles]]
def test_render_markdown_empty_subtitles() -> None:
    """An empty subtitles list renders channels: [] in the frontmatter."""
    video = _build_video(subtitles=[])
    meta = _parse_frontmatter(_render_markdown(video))
    assert meta["channels"] == []


# @lat: [[ingest#Tests#Render strips watched prefix]]
def test_render_markdown_strips_watched_prefix() -> None:
    """The leading 'Watched ' from the Takeout title is dropped in the title frontmatter key."""
    video = _build_video(title="Watched Some Cool Video")
    meta = _parse_frontmatter(_render_markdown(video))
    assert meta["title"] == "Some Cool Video"
    assert meta["title"] != "Watched Some Cool Video"


# @lat: [[ingest#Tests#Write markdown creates file]]
def test_write_markdown_creates_named_file(tmp_path: Path) -> None:
    """write_markdown writes a file named <video_id>.md in out_dir including any description."""
    video = _build_video(titleUrl="https://www.youtube.com/watch?v=JWWDqbcQoXA")
    path = write_markdown(video, tmp_path, description="hello world")
    assert path == tmp_path / "JWWDqbcQoXA.md"
    assert path.exists()
    content = path.read_text()
    assert _parse_frontmatter(content)["title"] == "Test Video"
    assert "hello world" in content
    assert "## Transcript" in content


# @lat: [[ingest#Tests#Write markdown overwrites]]
def test_write_markdown_overwrites_existing(tmp_path: Path) -> None:
    """A second call replaces the file contents (idempotent re-ingest)."""
    video = _build_video()
    path = write_markdown(video, tmp_path)
    path.write_text("stale")
    write_markdown(video, tmp_path)
    assert "stale" not in path.read_text()
    assert _parse_frontmatter(path.read_text())["title"] == "Test Video"


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
    monkeypatch.setattr(config, "WATCH_HISTORY_PATH", history)
    monkeypatch.setattr(config, "MARKDOWN_RAW_DIR", out_dir)
    monkeypatch.setattr(
        ingest,
        "fetch_descriptions",
        _stub_fetch_descriptions({"abc123": "first desc", "JWWDqbcQoXA": "second desc"}),
    )
    monkeypatch.setattr(ingest, "load_transcripts", _stub_load_transcripts_none)
    monkeypatch.setattr(ingest, "load_summaries", _stub_load_summaries_none)
    main()
    assert capsys.readouterr().out == ""
    assert (out_dir / "abc123.md").exists()
    assert (out_dir / "JWWDqbcQoXA.md").exists()
    assert "first desc" in (out_dir / "abc123.md").read_text()
    assert "second desc" in (out_dir / "JWWDqbcQoXA.md").read_text()


# @lat: [[ingest#Tests#main skips unresolved]]
def test_main_skips_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() does not write a file for unresolved (URL-placeholder) records."""
    history = _write_history(tmp_path, [_VALID_RECORD, _UNRESOLVED_RECORD])
    out_dir = tmp_path / "out"
    monkeypatch.setattr(config, "WATCH_HISTORY_PATH", history)
    monkeypatch.setattr(config, "MARKDOWN_RAW_DIR", out_dir)
    monkeypatch.setattr(ingest, "fetch_descriptions", _stub_fetch_descriptions({"abc123": "d"}))
    monkeypatch.setattr(ingest, "load_transcripts", _stub_load_transcripts_none)
    monkeypatch.setattr(ingest, "load_summaries", _stub_load_summaries_none)
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
    monkeypatch.setattr(config, "WATCH_HISTORY_PATH", history)
    monkeypatch.setattr(config, "MARKDOWN_RAW_DIR", out_dir)
    monkeypatch.setattr(ingest, "fetch_descriptions", _stub_fetch_descriptions({"abc123": "d"}))
    monkeypatch.setattr(ingest, "load_transcripts", _stub_load_transcripts_none)
    monkeypatch.setattr(ingest, "load_summaries", _stub_load_summaries_none)
    main()
    files = sorted(p.name for p in out_dir.iterdir())
    assert files == ["abc123.md"]


# @lat: [[ingest#Tests#Default output directory]]
def test_default_output_directory() -> None:
    """config.MARKDOWN_RAW_DIR points at the repo-root-relative raw markdown folder."""
    assert Path("Markdown/raw") == config.MARKDOWN_RAW_DIR
