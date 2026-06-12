"""Load YouTube watch history from Google Takeout."""

import asyncio
from pathlib import Path

import yaml

from youtubebrain import config, logger, takeout
from youtubebrain.descriptions import fetch_descriptions
from youtubebrain.models import WatchedVideo
from youtubebrain.summaries import load_summaries
from youtubebrain.transcripts import load_transcripts


# @lat: [[ingest#Markdown writer]]
def _render_markdown(
    video: WatchedVideo,
    description: str | None = None,
    transcript: str | None = None,
    summary: str | None = None,
) -> str:
    """Render a WatchedVideo as a markdown document with YAML frontmatter, description, and transcript."""
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
        "---",
        yaml_body,
        "---",
        "",
        "## Summary",
        "",
        summary if summary else "_(unavailable)_",
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
    return "\n".join(body_lines)


# @lat: [[ingest#Markdown writer]]
def write_markdown(
    video: WatchedVideo,
    out_dir: Path,
    description: str | None = None,
    transcript: str | None = None,
    summary: str | None = None,
) -> Path:
    """Write a markdown file for video into out_dir, named <video_id>.md."""
    if video.title_url is None:
        raise ValueError(f"WatchedVideo has no title_url: {video.title!r}")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{takeout.video_id(video.title_url)}.md"
    path.write_text(_render_markdown(video, description, transcript, summary))
    return path


# @lat: [[ingest#CLI entry]]
def main() -> None:
    """Fetch descriptions and write a markdown file for every watched video to config.MARKDOWN_RAW_DIR."""
    logger.info("Starting main function.")
    videos = takeout.load_watch_history(config.WATCH_HISTORY_PATH)
    ids = [takeout.video_id(v.title_url) for v in videos if v.title_url is not None]
    descriptions = asyncio.run(fetch_descriptions(ids))
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
    logger.info("Finished main function.")


if __name__ == "__main__":
    main()
