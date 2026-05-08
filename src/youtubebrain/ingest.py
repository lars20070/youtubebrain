"""Load YouTube watch history from Google Takeout."""

from pathlib import Path

from pydantic import TypeAdapter

from youtubebrain.models import WatchedVideo

WATCH_HISTORY_PATH = Path("Takeout/YouTube and YouTube Music/history/watch-history.json")

_adapter = TypeAdapter(list[WatchedVideo])


# @lat: [[ingest#Non-watch activity filtering]]
def _is_video_watch(video: WatchedVideo) -> bool:
    """Whether the record describes a video watch (title prefixed by 'Watched ')."""
    return video.title.startswith("Watched ")


# @lat: [[ingest#Unresolved title filtering]]
def _is_unresolved(video: WatchedVideo) -> bool:
    """Whether the record's title is a URL placeholder from a failed Takeout lookup."""
    return video.title_url is not None and video.title == f"Watched {video.title_url}"


# @lat: [[ingest#Loader]]
def load_watch_history(path: Path) -> list[WatchedVideo]:
    """Parse Takeout watch-history.json, dropping non-watch and unresolved entries."""
    videos = _adapter.validate_json(path.read_bytes())
    return [v for v in videos if _is_video_watch(v) and not _is_unresolved(v)]


def main() -> None:
    """Print every watched video's title to stdout."""
    for video in load_watch_history(WATCH_HISTORY_PATH):
        print(video.title)


if __name__ == "__main__":
    main()
