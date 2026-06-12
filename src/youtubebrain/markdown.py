"""Compile and parse raw markdown files for watched videos."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import yaml

from youtubebrain import config, logger, takeout
from youtubebrain.descriptions import load_descriptions
from youtubebrain.summaries import load_summaries
from youtubebrain.transcripts import load_transcripts

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from youtubebrain.models import WatchedVideo

FRONTMATTER_FENCE = "---"
UNAVAILABLE_MARKER = "_(unavailable)_"
SUMMARY_HEADING = "Summary"
DESCRIPTION_HEADING = "Description"
TRANSCRIPT_HEADING = "Transcript"

_SECTION_HEADINGS = (SUMMARY_HEADING, DESCRIPTION_HEADING, TRANSCRIPT_HEADING)
_SECTION_SPLIT_RE = re.compile(rf"(?m)^##\s+({'|'.join(re.escape(heading) for heading in _SECTION_HEADINGS)})\s*$")


def _normalize_section(raw: str | None) -> str | None:
    """Collapse empty bodies and the unavailable marker to None."""
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped or stripped == UNAVAILABLE_MARKER:
        return None
    return stripped


def _split_sections(body: str) -> dict[str, str]:
    """Split a markdown body into a dict keyed by `## <Name>` heading."""
    parts = _SECTION_SPLIT_RE.split(body)
    sections: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        sections[parts[i]] = parts[i + 1]
    return sections


# @lat: [[markdown#Markdown writer]]
def render_markdown(
    video: WatchedVideo,
    description: str | None = None,
    transcript: str | None = None,
    summary: str | None = None,
) -> str:
    """Render a WatchedVideo as markdown with YAML frontmatter and pipeline sections."""
    if video.title_url is None:
        raise ValueError(f"WatchedVideo has no title_url: {video.title!r}")
    frontmatter = {
        "id": takeout.video_id(video.title_url),
        "url": str(video.title_url),
        "title": video.title.removeprefix("Watched "),
        "channels": [{"name": s.name, "id": takeout.channel_id(s.url), "url": str(s.url)} for s in video.subtitles],
        "watch_time": video.time.isoformat(),
    }
    yaml_body = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip("\n")
    body_lines = [
        FRONTMATTER_FENCE,
        yaml_body,
        FRONTMATTER_FENCE,
        "",
        f"## {SUMMARY_HEADING}",
        "",
        summary if summary else UNAVAILABLE_MARKER,
        "",
        f"## {DESCRIPTION_HEADING}",
        "",
        description if description else UNAVAILABLE_MARKER,
        "",
        f"## {TRANSCRIPT_HEADING}",
        "",
        transcript if transcript else UNAVAILABLE_MARKER,
        "",
    ]
    return "\n".join(body_lines)


# @lat: [[markdown#Markdown writer]]
def write_markdown(
    video: WatchedVideo,
    out_dir: Path,
    description: str | None = None,
    transcript: str | None = None,
    summary: str | None = None,
) -> Path:
    """Write one markdown file named `<video_id>.md` to `out_dir`."""
    if video.title_url is None:
        raise ValueError(f"WatchedVideo has no title_url: {video.title!r}")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{takeout.video_id(video.title_url)}.md"
    path.write_text(render_markdown(video, description, transcript, summary))
    return path


# @lat: [[markdown#Parsing rules]]
def read_frontmatter(path: Path) -> tuple[dict, str]:
    """Read a markdown file; return (frontmatter_mapping, body_after_second_fence).

    Raises ValueError on a missing/unclosed fence, malformed YAML, or a non-mapping
    top-level value. Empty frontmatter is returned as {}.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_FENCE:
        raise ValueError(f"Missing frontmatter fence in {path}")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == FRONTMATTER_FENCE)
    except StopIteration as exc:
        raise ValueError(f"Unclosed frontmatter in {path}") from exc
    try:
        fm = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed frontmatter in {path}: {exc}") from exc
    if fm is None:
        fm = {}
    elif not isinstance(fm, dict):
        raise ValueError(f"Malformed frontmatter in {path}: expected mapping but got {type(fm).__name__}")
    body = "\n".join(lines[end + 1 :])
    return fm, body


# @lat: [[markdown#Parsing rules]]
def parse_raw_markdown(path: Path) -> tuple[str, str, str | None, str | None]:
    """Parse a raw markdown file; return (video_id, title, summary, description)."""
    fm, body = read_frontmatter(path)
    video_id = fm.get("id")
    title = fm.get("title")
    if not isinstance(video_id, str) or not isinstance(title, str):
        raise ValueError(f"Frontmatter must include string `id` and `title` in {path}")
    sections = _split_sections(body)
    summary = _normalize_section(sections.get(SUMMARY_HEADING))
    description = _normalize_section(sections.get(DESCRIPTION_HEADING))
    return video_id, title, summary, description


# @lat: [[markdown#Text composition]]
def compose_text(title: str, summary: str | None, description: str | None) -> str | None:
    """Return title + summary; fall back to title + description; else None."""
    if summary:
        return f"{title}\n\n{summary}"
    if description:
        return f"{title}\n\n{description}"
    return None


# @lat: [[markdown#Raw file iteration]]
def iter_raw_files(raw_dir: Path | None = None) -> Iterator[Path]:
    """Yield every `<video_id>.md` file under raw_dir in deterministic order."""
    raw_dir = config.MARKDOWN_RAW_DIR if raw_dir is None else raw_dir
    if not raw_dir.exists():
        return
    yield from sorted(raw_dir.glob("*.md"))


# @lat: [[markdown#CLI entry]]
def main() -> None:
    """Compile raw markdown files from Takeout metadata and cached stage outputs."""
    logger.info("Starting markdown compiler.")
    videos = takeout.load_watch_history(config.WATCH_HISTORY_PATH)
    ids = [takeout.video_id(v.title_url) for v in videos if v.title_url is not None]
    descriptions = load_descriptions(ids)
    transcripts = load_transcripts(ids)
    summaries = load_summaries(ids)
    count = 0
    for video in videos:
        vid = takeout.video_id(video.title_url) if video.title_url is not None else None
        write_markdown(
            video,
            config.MARKDOWN_RAW_DIR,
            descriptions.get(vid) if vid else None,
            transcripts.get(vid) if vid else None,
            summaries.get(vid) if vid else None,
        )
        count += 1
    logger.info(f"Wrote {count} markdown files to {config.MARKDOWN_RAW_DIR}.")
    logger.info("Finished markdown compiler.")


if __name__ == "__main__":
    main()
