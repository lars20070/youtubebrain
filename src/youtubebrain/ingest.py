"""Load YouTube watch history from Google Takeout."""

from pathlib import Path

from pydantic import TypeAdapter

from youtubebrain.models import WatchedVideo

WATCH_HISTORY_PATH = Path("Takeout/YouTube and YouTube Music/history/watch-history.json")

_adapter = TypeAdapter(list[WatchedVideo])


# @lat: [[ingest#Loader]]
def load_watch_history(path: Path) -> list[WatchedVideo]:
    """Parse Takeout watch-history.json into a list of WatchedVideo."""
    return _adapter.validate_json(path.read_bytes())


def main() -> None:
    """Print every watched video's title to stdout."""
    for video in load_watch_history(WATCH_HISTORY_PATH):
        print(video.title)


if __name__ == "__main__":
    main()
