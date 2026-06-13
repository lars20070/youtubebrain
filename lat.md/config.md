# Config

Central home for every filesystem path the pipeline reads or writes, plus the single `.env` loading call site. Consumers look constants up at call time (`config.X`), so tests redirect the whole pipeline by monkeypatching this one module.

## Path constants

All pipeline paths are repo-root-relative module-level `Path` constants in `src/youtubebrain/config.py`; no other module defines one.

They cover the Takeout export ([[src/youtubebrain/config.py#WATCH_HISTORY_PATH]]), the raw markdown corpus ([[src/youtubebrain/config.py#MARKDOWN_RAW_DIR]]), the cache directory ([[src/youtubebrain/config.py#CACHE_DIR]]) with three SQLite stores ([[src/youtubebrain/config.py#DESCRIPTIONS_DB_PATH]], [[src/youtubebrain/config.py#TRANSCRIPTS_DB_PATH]], [[src/youtubebrain/config.py#SUMMARIES_DB_PATH]]), the embedding store under [[src/youtubebrain/config.py#EMBEDDINGS_DIR]], the clustering store under [[src/youtubebrain/config.py#CLUSTERING_DIR]], and the wiki seed folders ([[src/youtubebrain/config.py#WIKI_TOPICS_DIR]], [[src/youtubebrain/config.py#WIKI_CREATORS_DIR]]).

Functions that accept a path take `Path | None = None` and resolve the config default inside the body — never as a def-time default — so a `monkeypatch.setattr(config, "X", tmp_path)` in a test takes effect everywhere at once.

The two clashing `meta.json` files are disambiguated as [[src/youtubebrain/config.py#EMBEDDINGS_META_JSON_PATH]] and [[src/youtubebrain/config.py#CLUSTERING_META_JSON_PATH]].

## Env loading

[[src/youtubebrain/config.py#load_env]] wraps `load_dotenv()` and is the only place in the package that touches the `.env` file. Modules that read env vars call `config.load_env()` first ([[descriptions#API key requirement]], [[transcripts#Pacing configuration]], [[embeddings#Env vars]], [[provider#Model factory]], [[clusters#Env vars]]).

The autouse `_block_dotenv` fixture in `tests/conftest.py` patches `load_dotenv` inside this module, which blocks on-disk `.env` reads for the entire suite with a single monkeypatch. Only the file is blocked — exported process env vars still apply, so tests that need a variable absent must `delenv` it explicitly.
