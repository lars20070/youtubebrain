"""Load YouTube watch history from Google Takeout."""

import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pydantic import HttpUrl, TypeAdapter

from youtubebrain import logger
from youtubebrain.descriptions import fetch_descriptions
from youtubebrain.models import WatchedVideo
from youtubebrain.transcripts import load_transcripts

WATCH_HISTORY_PATH = Path("Takeout/YouTube and YouTube Music/history/watch-history.json")

# @lat: [[ingest#Default output directory]]
MARKDOWN_RAW_DIR = Path("Markdown/raw")

_adapter = TypeAdapter(list[WatchedVideo])


# @lat: [[ingest#Non-watch activity filtering]]
def _is_video_watch(video: WatchedVideo) -> bool:
    """Whether the record describes a video watch (title prefixed by 'Watched ')."""
    return video.title.startswith("Watched ")


# @lat: [[ingest#Unresolved title filtering]]
def _is_unresolved(video: WatchedVideo) -> bool:
    """Whether the record's title is a URL placeholder from a failed Takeout lookup."""
    return video.title_url is not None and video.title == f"Watched {video.title_url}"


# @lat: [[ingest#Video ID extraction]]
def _video_id(url: HttpUrl) -> str:
    """Extract the YouTube video ID (the 'v' query parameter) from a watch URL."""
    params = parse_qs(urlparse(str(url)).query)
    values = params.get("v")
    if not values:
        raise ValueError(f"URL has no 'v' query parameter: {url}")
    return values[0]


# @lat: [[ingest#Markdown writer]]
def _render_markdown(
    video: WatchedVideo,
    description: str | None = None,
    transcript: str | None = None,
) -> str:
    """Render a WatchedVideo as a markdown document body, optionally including description and transcript."""
    lines = [
        "## Video",
        "",
        f"- Title: {video.title.removeprefix('Watched ')}",
        f"- Title URL: {video.title_url}",
        f"- Time: {video.time.isoformat()}",
        "",
        "## Channels",
        "",
    ]
    if video.subtitles:
        lines.extend(f"- [{s.name}]({s.url})" for s in video.subtitles)
    else:
        lines.append("_(none)_")
    lines.extend(
        [
            "",
            "## Description",
            "",
            description if description else "_(unavailable)_",
            "",
            "## Transcript",
            "",
            transcript if transcript else "_(unavailable)_",
            "",
        ]
    )
    return "\n".join(lines)


# @lat: [[ingest#Markdown writer]]
def write_markdown(
    video: WatchedVideo,
    out_dir: Path,
    description: str | None = None,
    transcript: str | None = None,
) -> Path:
    """Write a markdown file for video into out_dir, named <video_id>.md."""
    if video.title_url is None:
        raise ValueError(f"WatchedVideo has no title_url: {video.title!r}")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_video_id(video.title_url)}.md"
    path.write_text(_render_markdown(video, description, transcript))
    return path


# @lat: [[ingest#Loader]]
def load_watch_history(path: Path) -> list[WatchedVideo]:
    """Parse Takeout watch-history.json, dropping non-watch and unresolved entries."""
    logger.info(f"Loading watch history from {path}.")
    videos = _adapter.validate_json(path.read_bytes())
    kept = [v for v in videos if _is_video_watch(v) and not _is_unresolved(v)]
    logger.info(f"Parsed {len(videos)} records, kept {len(kept)} after filtering.")
    return kept


def main() -> None:
    """Fetch descriptions and write a markdown file for every watched video to MARKDOWN_RAW_DIR."""
    logger.info("Starting main function.")
    videos = load_watch_history(WATCH_HISTORY_PATH)
    ids = [_video_id(v.title_url) for v in videos if v.title_url is not None]
    descriptions = asyncio.run(fetch_descriptions(ids))
    transcripts = load_transcripts(ids)
    count = 0
    for video in videos:
        vid = _video_id(video.title_url) if video.title_url is not None else None
        write_markdown(
            video,
            MARKDOWN_RAW_DIR,
            descriptions.get(vid) if vid else None,
            transcripts.get(vid) if vid else None,
        )
        count += 1
    logger.info(f"Wrote {count} markdown files to {MARKDOWN_RAW_DIR}.")
    logger.info("Finished main function.")


if __name__ == "__main__":
    main()
