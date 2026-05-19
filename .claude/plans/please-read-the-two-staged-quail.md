# Embedding stage for `Markdown/raw/`

## Context

Need a new pipeline stage that produces sentence-transformer embeddings of every `Markdown/raw/<id>.md` file, so downstream clustering (BERTopic etc.) can build a wiki taxonomy over ~500 videos today, scaling to 10K. Embed `title + summary`, fallback `title + description`. Local CPU, no API. Configurable via `.env`.

## Decisions (locked)

- Backend: `sentence-transformers` direct, CPU. Pydantic AI not used here (no first-class local-ST support); summaries pipeline unchanged.
- Storage: `Markdown/embeddings/{embeddings.npy, ids.json, meta.json}`. No SQLite.
- CLI: new `uv run embed`.
- Re-embed policy: embed once, never re-embed; new raw ids picked up on next run; videos missing both summary+description silently skipped (retried when summary appears).

## Files to add / change

- new [src/youtubebrain/embeddings.py](src/youtubebrain/embeddings.py)
- new [tests/test_embeddings.py](tests/test_embeddings.py)
- new [lat.md/embeddings.md](lat.md/embeddings.md)
- edit [pyproject.toml](pyproject.toml) — deps, script, marker, addopts
- edit [.env.example](.env.example) — `EMBEDDING_MODEL`
- edit [lat.md/lat.md](lat.md/lat.md) — index bullet

## Module design — `src/youtubebrain/embeddings.py`

Constants:
- `EMBEDDINGS_DIR = Path("Markdown/embeddings")`
- `EMBEDDINGS_NPY_PATH`, `IDS_JSON_PATH`, `META_JSON_PATH` derived
- `MARKDOWN_RAW_DIR = Path("Markdown/raw")`
- `_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"`
- `_MODEL_ENV = "EMBEDDING_MODEL"`
- `_BATCH_SIZE = 64` (hardcoded; intentionally not env-exposed — note in lat.md)
- `_UNAVAILABLE_MARKER = "_(unavailable)_"` (matches [ingest.py:85-93](src/youtubebrain/ingest.py#L85-L93))

Public API (each `# @lat:` annotated):
- `parse_raw_markdown(path) -> (id, title, summary|None, description|None)` — pyyaml frontmatter + regex section split on `^## (Summary|Description|Transcript)$`; `_(unavailable)_` and empty → None.
- `compose_text(title, summary, description) -> str | None` — `title\n\nsummary` else `title\n\ndescription` else None.
- `iter_raw_files(raw_dir=MARKDOWN_RAW_DIR)`
- `load_existing() -> (np.ndarray, list[str], dict)` — empty arr `(0,0)` float32 + `[]` + `{}` when missing; length-mismatch → treat as empty + warn.
- `save_atomic(arr, ids, meta)` — write each as `*.tmp` then `os.replace`; order: ids → meta → npy last (readers key off npy presence).
- `embed_pending(raw_dir=MARKDOWN_RAW_DIR) -> int` — main worker.
- `main()` — CLI.

Private:
- `_build_encoder(model_name)` — lazy `from sentence_transformers import SentenceTransformer` inside fn; tests monkeypatch this factory (third-party never imported in tests).
- `_split_sections(body)`, `_normalize_section(raw)`, `_model_name()` (calls `load_dotenv()`).
- `_EncoderProto(typing.Protocol)` with `.encode(...)`.

Encode call: `encoder.encode(texts, batch_size=_BATCH_SIZE, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=True)` → `.astype(np.float32, copy=False)`.

`embed_pending` flow:
1. load existing → existing_set
2. walk raw files, parse, compose; skip if text None or id in existing_set
3. empty pending → log + return 0
4. resolve model name → build encoder → encode pending
5. if existing nonempty and dim mismatch → raise ValueError ("delete `Markdown/embeddings/` to rebuild"); no write
6. vstack + concat ids; `meta = {model, dim, updated_at}` (UTC iso)
7. `save_atomic`; log new + total; return count

## `.env.example`

Append:
```
# Embeddings
EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2"
```

## `pyproject.toml`

- deps: `sentence-transformers>=3.0,<5`, `numpy>=1.26,<3`
- scripts: `embed = "youtubebrain.embeddings:main"`
- markers: add `slow_embedding: tests downloading real ST model`
- addopts: extend filter `not paid and not ollama and not slow_embedding`

## Tests — `tests/test_embeddings.py`

Stub encoder class returns deterministic `np.ndarray(shape=(N,4), dtype=float32)`. Tests monkeypatch `_build_encoder` — never import sentence_transformers.

Leaf specs (each → one `# @lat:`):
- Frontmatter parsing
- Unavailable placeholder recognized as missing
- Compose prefers summary
- Compose falls back to description
- Compose skipped when both missing
- ids.json round-trip
- embeddings.npy shape and dtype
- meta.json records model and dim
- EMBEDDING_MODEL env override (spy `_build_encoder`)
- Idempotent re-run (second pass adds zero ids)
- Skips videos with no embeddable text
- Atomic write leaves no partial files on crash (monkeypatch `np.save` to raise; assert pre-existing trio intact, no `*.tmp` lingers)
- Dim mismatch raises ValueError

Optional `slow_embedding` smoke test: real `_build_encoder("…all-MiniLM-L6-v2").encode(["hello"], …)` asserts dtype + shape (384). Off by default.

## `lat.md/embeddings.md` outline

Frontmatter `lat: require-code-mention: true`. Sections (mirror summaries.md): Overview + mermaid (`Raw -> parse -> compose -> encode -> save`); CLI entry; Storage layout (atomic write order, alignment invariant `len(ids)==arr.shape[0]`); Parsing rules; Text composition; Model factory; Embed loop; Env vars (note `EMBEDDING_BATCH_SIZE` deliberately omitted); Re-embed policy (delete dir to rebuild); Tests (leaves above).

## `lat.md/lat.md` index

Add bullet after `[[summaries]]`:
```
- [[embeddings]] — Compute sentence-transformer embeddings of title + summary/description per raw markdown
```

## Verification

```
uv sync
uv run ruff check src/youtubebrain/embeddings.py tests/test_embeddings.py
uv run pyright src/youtubebrain/embeddings.py
uv run pytest tests/test_embeddings.py -q
uv run embed                          # full run; ~1–2 min on CPU after first model download
python -c "import numpy as np, json; a=np.load('Markdown/embeddings/embeddings.npy'); print(a.shape, a.dtype); print(len(json.load(open('Markdown/embeddings/ids.json'))))"
lat check                             # all wiki links + code refs pass
```

Expect `(N, 384) float32` and `N == len(ids)`.

## Sequencing

1. pyproject deps + script + marker → `uv sync`
2. `embeddings.py`
3. `.env.example`
4. `tests/test_embeddings.py`
5. `lat.md/embeddings.md` + `lat.md/lat.md` index bullet
6. ruff / pyright / pytest / `lat check`

## Unresolved questions

- Confirm `<5` upper pin on `sentence-transformers` (latest is 3.x; pin loose enough)?
- Add a `slow_embedding` marker now or defer?
- Wire embed into `uv run ingest` later, or keep strictly separate?
