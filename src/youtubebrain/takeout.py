"""Parse and filter Google Takeout watch-history records."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pydantic import HttpUrl, TypeAdapter

from youtubebrain import config, logger
from youtubebrain.models import WatchedVideo

_adapter = TypeAdapter(list[WatchedVideo])


# @lat: [[takeout#Non-watch activity filtering]]
def is_video_watch(video: WatchedVideo) -> bool:
    """Whether the record describes a video watch (title prefixed by 'Watched ')."""
    return video.title.startswith("Watched ")


# @lat: [[takeout#Unresolved title filtering]]
def is_unresolved(video: WatchedVideo) -> bool:
    """Whether the record's title is a URL placeholder from a failed Takeout lookup."""
    return video.title_url is not None and video.title == f"Watched {video.title_url}"


# @lat: [[takeout#Video ID extraction]]
def video_id(url: HttpUrl) -> str:
    """Extract the YouTube video ID (the 'v' query parameter) from a watch URL."""
    params = parse_qs(urlparse(str(url)).query)
    values = params.get("v")
    if not values:
        raise ValueError(f"URL has no 'v' query parameter: {url}")
    return values[0]


# @lat: [[takeout#Channel ID extraction]]
def channel_id(url: HttpUrl) -> str:
    """Extract the YouTube channel ID (last path segment) from a /channel/<id> URL."""
    parts = [p for p in urlparse(str(url)).path.split("/") if p]
    if len(parts) < 2 or parts[-2] != "channel":
        raise ValueError(f"URL is not a /channel/<id> URL: {url}")
    return parts[-1]


# @lat: [[takeout#Loader]]
def load_watch_history(path: Path) -> list[WatchedVideo]:
    """Parse Takeout watch-history.json, dropping non-watch and unresolved entries."""
    logger.info(f"Loading watch history from {path}.")
    videos = _adapter.validate_json(path.read_bytes())
    kept = [v for v in videos if is_video_watch(v) and not is_unresolved(v)]
    logger.info(f"Parsed {len(videos)} records, kept {len(kept)} after filtering.")
    return kept


# @lat: [[takeout#Video IDs]]
def load_video_ids(path: Path | None = None) -> list[str]:
    """Return video IDs from the filtered watch-history records."""
    path = config.WATCH_HISTORY_PATH if path is None else path
    videos = load_watch_history(path)
    return [video_id(v.title_url) for v in videos if v.title_url is not None]
