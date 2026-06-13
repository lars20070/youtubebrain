"""Unit tests for markdown rendering, parsing, and compile entrypoint wiring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from pydantic import TypeAdapter

from youtubebrain import config, markdown
from youtubebrain.markdown import (
    compose_text,
    main,
    parse_raw_markdown,
    read_frontmatter,
    render_markdown,
    write_markdown,
)
from youtubebrain.models import WatchedVideo

if TYPE_CHECKING:
    from collections.abc import Callable

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

_MD_TEMPLATE = """\
---
id: {id}
url: https://www.youtube.com/watch?v={id}
title: {title}
channels: []
watch_time: '2026-05-06T14:50:16.546000+00:00'
---

## Summary

{summary}

## Description

{description}

## Transcript

{transcript}
"""


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


def _stub_load_descriptions(
    descriptions: dict[str, str | None],
) -> Callable[[list[str], Path | None], dict[str, str | None]]:
    """Build a stub for markdown.load_descriptions that returns a fixed mapping."""

    def _stub(video_ids: list[str], _db_path: Path | None = None) -> dict[str, str | None]:
        return {vid: descriptions.get(vid) for vid in video_ids}

    return _stub


def _stub_load_none(video_ids: list[str], _db_path: Path | None = None) -> dict[str, str | None]:
    """Return no cached text (equivalent to empty cache rows)."""
    return dict.fromkeys(dict.fromkeys(video_ids), None)


def _write_md(path: Path, **fields: str) -> Path:
    defaults = {
        "id": "vidA",
        "title": "Some Title",
        "summary": markdown.UNAVAILABLE_MARKER,
        "description": markdown.UNAVAILABLE_MARKER,
        "transcript": markdown.UNAVAILABLE_MARKER,
    }
    defaults.update(fields)
    path.write_text(_MD_TEMPLATE.format(**defaults), encoding="utf-8")
    return path


# @lat: [[markdown#Tests#Render markdown frontmatter]]
def test_render_markdown_frontmatter() -> None:
    """Rendered markdown frontmatter includes id, url, title, channels with id, and watch_time."""
    video = _build_video()
    body = render_markdown(video, description="A short clip about something.")
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


# @lat: [[markdown#Tests#Render summary section]]
def test_render_markdown_summary_section() -> None:
    """Rendered markdown includes Summary before Description and Transcript with supplied body text."""
    video = _build_video()
    body = render_markdown(video, description="d", transcript="t", summary="summary text")
    summary_pos = body.index("## Summary")
    desc_pos = body.index("## Description")
    transcript_pos = body.index("## Transcript")
    assert summary_pos < desc_pos < transcript_pos
    assert "summary text" in body.split("## Summary")[1].split("## Description")[0]


# @lat: [[markdown#Tests#Render unavailable summary]]
def test_render_markdown_summary_unavailable() -> None:
    """A None summary renders the unavailable marker under the Summary heading."""
    video = _build_video()
    body = render_markdown(video, description="d", transcript="t", summary=None)
    assert "## Summary" in body
    assert markdown.UNAVAILABLE_MARKER in body.split("## Summary")[1].split("## Description")[0]


# @lat: [[markdown#Tests#Render unavailable description]]
def test_render_markdown_unavailable_description() -> None:
    """A None description renders the unavailable marker under the Description heading."""
    video = _build_video()
    body = render_markdown(video, description=None)
    assert "## Description" in body
    assert markdown.UNAVAILABLE_MARKER in body
    assert "## Transcript" in body


# @lat: [[markdown#Tests#Render multiple channels]]
def test_render_markdown_lists_multiple_channels() -> None:
    """A record with multiple subtitles renders each channel as a YAML list entry."""
    video = _build_video(
        subtitles=[
            {"name": "Channel One", "url": "https://www.youtube.com/channel/UC1"},
            {"name": "Channel Two", "url": "https://www.youtube.com/channel/UC2"},
        ],
    )
    meta = _parse_frontmatter(render_markdown(video))
    channels = meta["channels"]
    assert isinstance(channels, list)
    assert len(channels) == 2
    assert channels[0]["name"] == "Channel One"
    assert channels[0]["id"] == "UC1"
    assert channels[1]["name"] == "Channel Two"
    assert channels[1]["id"] == "UC2"


# @lat: [[markdown#Tests#Render empty subtitles]]
def test_render_markdown_empty_subtitles() -> None:
    """An empty subtitles list renders channels: [] in the frontmatter."""
    video = _build_video(subtitles=[])
    meta = _parse_frontmatter(render_markdown(video))
    assert meta["channels"] == []


# @lat: [[markdown#Tests#Render strips watched prefix]]
def test_render_markdown_strips_watched_prefix() -> None:
    """The leading 'Watched ' from the Takeout title is dropped in frontmatter."""
    video = _build_video(title="Watched Some Cool Video")
    meta = _parse_frontmatter(render_markdown(video))
    assert meta["title"] == "Some Cool Video"
    assert meta["title"] != "Watched Some Cool Video"


# @lat: [[markdown#Tests#Render markdown Transcript section]]
def test_render_markdown_transcript_section() -> None:
    """The markdown template includes a Transcript heading and body text."""
    video = _build_video(title="Watched Hello", titleUrl="https://www.youtube.com/watch?v=abc")
    body = render_markdown(video, description="d", transcript="line one")
    assert "## Transcript" in body
    assert "line one" in body
    assert "## Description" in body


# @lat: [[markdown#Tests#Render transcript unavailable placeholder]]
def test_render_markdown_transcript_unavailable() -> None:
    """A None transcript renders the unavailable marker under Transcript."""
    video = _build_video(title="Watched Hello", titleUrl="https://www.youtube.com/watch?v=abc")
    body = render_markdown(video, transcript=None)
    assert "## Transcript" in body
    assert markdown.UNAVAILABLE_MARKER in body.split("## Transcript")[1]


# @lat: [[markdown#Tests#Write markdown creates file]]
def test_write_markdown_creates_named_file(tmp_path: Path) -> None:
    """write_markdown writes a file named <video_id>.md in out_dir including supplied text."""
    video = _build_video(titleUrl="https://www.youtube.com/watch?v=JWWDqbcQoXA")
    path = write_markdown(video, tmp_path, description="hello world")
    assert path == tmp_path / "JWWDqbcQoXA.md"
    assert path.exists()
    content = path.read_text()
    assert _parse_frontmatter(content)["title"] == "Test Video"
    assert "hello world" in content
    assert "## Transcript" in content


# @lat: [[markdown#Tests#Write markdown overwrites]]
def test_write_markdown_overwrites_existing(tmp_path: Path) -> None:
    """A second call replaces the file contents (idempotent re-compile)."""
    video = _build_video()
    path = write_markdown(video, tmp_path)
    path.write_text("stale")
    write_markdown(video, tmp_path)
    assert "stale" not in path.read_text()
    assert _parse_frontmatter(path.read_text())["title"] == "Test Video"


# @lat: [[markdown#Tests#Write markdown requires title URL]]
def test_write_markdown_raises_without_title_url(tmp_path: Path) -> None:
    """write_markdown raises ValueError when the record has no titleUrl."""
    record = {k: v for k, v in _VALID_RECORD.items() if k != "titleUrl"}
    video = TypeAdapter(WatchedVideo).validate_python(record)
    with pytest.raises(ValueError, match="title_url"):
        write_markdown(video, tmp_path)


# @lat: [[markdown#Tests#main writes files]]
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
        markdown,
        "load_descriptions",
        _stub_load_descriptions({"abc123": "first desc", "JWWDqbcQoXA": "second desc"}),
    )
    monkeypatch.setattr(markdown, "load_transcripts", _stub_load_none)
    monkeypatch.setattr(markdown, "load_summaries", _stub_load_none)
    main()
    assert capsys.readouterr().out == ""
    assert (out_dir / "abc123.md").exists()
    assert (out_dir / "JWWDqbcQoXA.md").exists()
    assert "first desc" in (out_dir / "abc123.md").read_text()
    assert "second desc" in (out_dir / "JWWDqbcQoXA.md").read_text()


# @lat: [[markdown#Tests#main skips unresolved]]
def test_main_skips_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() does not write a file for unresolved (URL-placeholder) records."""
    history = _write_history(tmp_path, [_VALID_RECORD, _UNRESOLVED_RECORD])
    out_dir = tmp_path / "out"
    monkeypatch.setattr(config, "WATCH_HISTORY_PATH", history)
    monkeypatch.setattr(config, "MARKDOWN_RAW_DIR", out_dir)
    monkeypatch.setattr(markdown, "load_descriptions", _stub_load_descriptions({"abc123": "d"}))
    monkeypatch.setattr(markdown, "load_transcripts", _stub_load_none)
    monkeypatch.setattr(markdown, "load_summaries", _stub_load_none)
    main()
    files = sorted(p.name for p in out_dir.iterdir())
    assert files == ["abc123.md"]


# @lat: [[markdown#Tests#main skips non-watch]]
def test_main_skips_non_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() does not write a file for non-watch (community-post) activity."""
    history = _write_history(tmp_path, [_VALID_RECORD, _NON_WATCH_RECORD])
    out_dir = tmp_path / "out"
    monkeypatch.setattr(config, "WATCH_HISTORY_PATH", history)
    monkeypatch.setattr(config, "MARKDOWN_RAW_DIR", out_dir)
    monkeypatch.setattr(markdown, "load_descriptions", _stub_load_descriptions({"abc123": "d"}))
    monkeypatch.setattr(markdown, "load_transcripts", _stub_load_none)
    monkeypatch.setattr(markdown, "load_summaries", _stub_load_none)
    main()
    files = sorted(p.name for p in out_dir.iterdir())
    assert files == ["abc123.md"]


# @lat: [[markdown#Tests#main folds transcripts]]
def test_main_folds_transcripts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main passes cached transcript text through to written markdown files."""
    history = _write_history(tmp_path, [_VALID_RECORD])
    out_dir = tmp_path / "out"
    monkeypatch.setattr(config, "WATCH_HISTORY_PATH", history)
    monkeypatch.setattr(config, "MARKDOWN_RAW_DIR", out_dir)

    def fake_descriptions(video_ids: list[str], _db_path: Path | None = None) -> dict[str, str | None]:
        return dict.fromkeys(video_ids, "d")

    def fake_transcripts(video_ids: list[str], _db_path: Path | None = None) -> dict[str, str | None]:
        return dict.fromkeys(video_ids, "transcript body")

    def fake_summaries(video_ids: list[str], _db_path: Path | None = None) -> dict[str, str | None]:
        return dict.fromkeys(video_ids, "summary body")

    monkeypatch.setattr(markdown, "load_descriptions", fake_descriptions)
    monkeypatch.setattr(markdown, "load_transcripts", fake_transcripts)
    monkeypatch.setattr(markdown, "load_summaries", fake_summaries)
    main()
    text = (out_dir / "abc123.md").read_text()
    assert "transcript body" in text
    assert "## Transcript" in text
    assert "summary body" in text
    assert "## Summary" in text


# @lat: [[markdown#Tests#Default output directory]]
def test_default_output_directory() -> None:
    """config.MARKDOWN_RAW_DIR points at the repo-root-relative raw markdown folder."""
    assert Path("Markdown/raw") == config.MARKDOWN_RAW_DIR


# @lat: [[markdown#Tests#Frontmatter parsing]]
def test_parse_raw_markdown_extracts_fields(tmp_path: Path) -> None:
    """parse_raw_markdown returns id, title, and section bodies from a typical raw file."""
    path = _write_md(
        tmp_path / "vid.md",
        id="vid1",
        title="Why doormen matter",
        summary="A short summary body.",
        description="Promo description body.",
    )
    vid, title, summary, description = parse_raw_markdown(path)
    assert vid == "vid1"
    assert title == "Why doormen matter"
    assert summary == "A short summary body."
    assert description == "Promo description body."


# @lat: [[markdown#Tests#Unavailable placeholder recognized as missing]]
def test_unavailable_placeholder_treated_as_missing(tmp_path: Path) -> None:
    """Section bodies equal to the unavailable marker parse to None, not text."""
    path = _write_md(
        tmp_path / "vid.md",
        id="vid1",
        title="T",
        summary=markdown.UNAVAILABLE_MARKER,
        description="real desc",
    )
    _vid, _title, summary, description = parse_raw_markdown(path)
    assert summary is None
    assert description == "real desc"


# @lat: [[markdown#Tests#Frontmatter missing fence rejected]]
def test_read_frontmatter_rejects_missing_fence(tmp_path: Path) -> None:
    """A file that does not start with the frontmatter fence raises ValueError."""
    path = tmp_path / "vid.md"
    path.write_text("# no frontmatter here\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing frontmatter fence"):
        read_frontmatter(path)


# @lat: [[markdown#Tests#Frontmatter unclosed fence rejected]]
def test_read_frontmatter_rejects_unclosed_fence(tmp_path: Path) -> None:
    """An opening fence without a closing fence raises ValueError."""
    path = tmp_path / "vid.md"
    path.write_text("---\nid: vid1\ntitle: T\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unclosed frontmatter"):
        read_frontmatter(path)


# @lat: [[markdown#Tests#Frontmatter malformed yaml rejected]]
def test_read_frontmatter_rejects_malformed_yaml(tmp_path: Path) -> None:
    """Invalid YAML between the fences raises ValueError wrapping the parser error."""
    path = tmp_path / "vid.md"
    path.write_text("---\na: [1, 2\n---\n\nbody\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed frontmatter"):
        read_frontmatter(path)


# @lat: [[markdown#Tests#Frontmatter non-mapping rejected]]
def test_read_frontmatter_rejects_non_mapping(tmp_path: Path) -> None:
    """A top-level YAML value that is not a mapping raises ValueError."""
    path = tmp_path / "vid.md"
    path.write_text("---\n- a\n- b\n---\n\nbody\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected mapping"):
        read_frontmatter(path)


# @lat: [[markdown#Tests#Frontmatter empty returns empty mapping]]
def test_read_frontmatter_empty_returns_empty_dict(tmp_path: Path) -> None:
    """Two adjacent fences parse to an empty dict and the remaining body."""
    path = tmp_path / "vid.md"
    path.write_text("---\n---\n\nbody text\n", encoding="utf-8")
    fm, body = read_frontmatter(path)
    assert fm == {}
    assert "body text" in body


# @lat: [[markdown#Tests#Parse requires string id and title]]
def test_parse_raw_markdown_requires_string_id_and_title(tmp_path: Path) -> None:
    """Frontmatter without string id and title values raises ValueError."""
    path = tmp_path / "vid.md"
    path.write_text("---\nid: 123\ntitle: T\n---\n\n## Summary\n\nx\n", encoding="utf-8")
    with pytest.raises(ValueError, match="string `id` and `title`"):
        parse_raw_markdown(path)


# @lat: [[markdown#Tests#Compose prefers summary]]
def test_compose_prefers_summary() -> None:
    """When both summary and description are present, summary is used."""
    text = compose_text("Title", "summary body", "description body")
    assert text == "Title\n\nsummary body"


# @lat: [[markdown#Tests#Compose falls back to description]]
def test_compose_falls_back_to_description() -> None:
    """Missing summary falls back to description."""
    text = compose_text("Title", None, "description body")
    assert text == "Title\n\ndescription body"


# @lat: [[markdown#Tests#Compose skipped when both missing]]
def test_compose_returns_none_when_both_missing() -> None:
    """Both fields missing returns None (caller should skip)."""
    assert compose_text("Title", None, None) is None
