"""Central pipeline paths and .env loading.

Every path the pipeline reads or writes is defined here, and modules look the
constants up at call time (config.X), so tests redirect the whole pipeline by
monkeypatching this single module.
"""

from pathlib import Path

from dotenv import load_dotenv

WATCH_HISTORY_PATH = Path("Takeout/YouTube and YouTube Music/history/watch-history.json")

# @lat: [[markdown#Default output directory]]
MARKDOWN_RAW_DIR = Path("Markdown/raw")

CACHE_DIR = Path("Markdown/.cache")
DESCRIPTIONS_DB_PATH = CACHE_DIR / "descriptions.sqlite"
# Legacy JSON cache path from pre-step-4; cleaned up by descriptions.main().
DESCRIPTIONS_CACHE_PATH = CACHE_DIR / "descriptions.json"
TRANSCRIPTS_DB_PATH = CACHE_DIR / "transcripts.sqlite"
SUMMARIES_DB_PATH = CACHE_DIR / "summaries.sqlite"

EMBEDDINGS_DIR = Path("Markdown/embeddings")
EMBEDDINGS_NPY_PATH = EMBEDDINGS_DIR / "embeddings.npy"
EMBEDDINGS_IDS_JSON_PATH = EMBEDDINGS_DIR / "ids.json"
EMBEDDINGS_META_JSON_PATH = EMBEDDINGS_DIR / "meta.json"

CLUSTERING_DIR = Path("Markdown/clustering")
ASSIGNMENTS_JSON_PATH = CLUSTERING_DIR / "assignments.json"
TOPICS_JSON_PATH = CLUSTERING_DIR / "topics.json"
CLUSTERING_META_JSON_PATH = CLUSTERING_DIR / "meta.json"
BERTOPIC_MODEL_DIR = CLUSTERING_DIR / "bertopic_model"
PLOT_PNG_PATH = CLUSTERING_DIR / "clusters.png"

WIKI_TOPICS_DIR = Path("Markdown/wiki/topics")
WIKI_CREATORS_DIR = Path("Markdown/wiki/creators")


def load_env() -> None:
    """Load .env into the process environment; the only dotenv call site in the package."""
    load_dotenv()
