"""Embed title + summary (fallback title + description) per raw markdown file with local SentenceTransformer."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

import numpy as np

from youtubebrain import config, logger, markdown

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_MODEL_ENV = "EMBEDDING_MODEL"
_BATCH_SIZE = 64


class _EncoderProto(Protocol):
    """Minimal protocol the embedding loop needs from a SentenceTransformer-like object."""

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> NDArray[np.floating]: ...


# @lat: [[embeddings#Model factory]]
def _build_encoder(model_name: str) -> _EncoderProto:
    """Construct a SentenceTransformer encoder; lazy import keeps tests offline."""
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415

    return cast("_EncoderProto", SentenceTransformer(model_name))


# @lat: [[embeddings#Env vars]]
def _model_name() -> str:
    """Resolve the SentenceTransformer model id from EMBEDDING_MODEL env (default all-MiniLM-L6-v2)."""
    config.load_env()
    return os.environ.get(_MODEL_ENV, _DEFAULT_MODEL)


# @lat: [[embeddings#Storage layout]]
def load_existing() -> tuple[NDArray[np.float32], list[str], dict[str, object]]:
    """Load existing embeddings, ids, and meta; empty when files are missing or mismatched."""
    empty_arr = np.zeros((0, 0), dtype=np.float32)
    ids: list[str] = []
    meta: dict[str, object] = {}
    if config.EMBEDDINGS_IDS_JSON_PATH.exists():
        ids = json.loads(config.EMBEDDINGS_IDS_JSON_PATH.read_text(encoding="utf-8"))
    if config.EMBEDDINGS_META_JSON_PATH.exists():
        meta = json.loads(config.EMBEDDINGS_META_JSON_PATH.read_text(encoding="utf-8"))
    if config.EMBEDDINGS_NPY_PATH.exists():
        arr = np.load(config.EMBEDDINGS_NPY_PATH).astype(np.float32, copy=False)
    else:
        arr = empty_arr
    if len(ids) != arr.shape[0]:
        logger.warning(
            f"Embedding store length mismatch (ids={len(ids)}, rows={arr.shape[0]}); treating as empty. Delete Markdown/embeddings/ to rebuild.",
        )
        return empty_arr, [], {}
    return arr, ids, meta


# @lat: [[embeddings#Storage layout]]
def save_atomic(arr: NDArray[np.float32], ids: list[str], meta: dict[str, object]) -> None:
    """Write ids → meta → embeddings.npy atomically via `*.tmp` + `os.replace`."""
    if arr.shape[0] != len(ids):
        raise ValueError(f"refusing to save: arr rows={arr.shape[0]} != ids={len(ids)}")
    config.EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)

    ids_tmp = config.EMBEDDINGS_IDS_JSON_PATH.with_suffix(config.EMBEDDINGS_IDS_JSON_PATH.suffix + ".tmp")
    meta_tmp = config.EMBEDDINGS_META_JSON_PATH.with_suffix(config.EMBEDDINGS_META_JSON_PATH.suffix + ".tmp")
    # np.save auto-appends ".npy" if absent, so keep the suffix on the tmp path.
    npy_tmp = config.EMBEDDINGS_NPY_PATH.with_name(config.EMBEDDINGS_NPY_PATH.stem + ".tmp.npy")

    ids_tmp.write_text(json.dumps(ids), encoding="utf-8")
    os.replace(ids_tmp, config.EMBEDDINGS_IDS_JSON_PATH)
    meta_tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(meta_tmp, config.EMBEDDINGS_META_JSON_PATH)
    np.save(npy_tmp, arr, allow_pickle=False)
    os.replace(npy_tmp, config.EMBEDDINGS_NPY_PATH)


# @lat: [[embeddings#Embed loop]]
def embed_pending(raw_dir: Path | None = None) -> int:
    """Encode every raw markdown file not already in `ids.json`; return count newly embedded."""
    raw_dir = config.MARKDOWN_RAW_DIR if raw_dir is None else raw_dir
    existing_arr, existing_ids, _existing_meta = load_existing()
    existing_set = set(existing_ids)

    pending_ids: list[str] = []
    pending_texts: list[str] = []
    skipped_no_text = 0
    seen = 0
    for path in markdown.iter_raw_files(raw_dir):
        seen += 1
        try:
            video_id, title, summary, description = markdown.parse_raw_markdown(path)
        except ValueError as exc:
            logger.warning(f"Skipping {path}: {exc}")
            continue
        # @lat: [[embeddings#Re-embed policy]]
        if video_id in existing_set:
            continue
        text = markdown.compose_text(title, summary, description)
        if text is None:
            skipped_no_text += 1
            continue
        pending_ids.append(video_id)
        pending_texts.append(text)

    if not pending_ids:
        logger.info(
            f"Embeddings: nothing to embed (raw={seen}, already={len(existing_ids)}, skipped_no_text={skipped_no_text}).",
        )
        return 0

    model_name = _model_name()
    logger.info(
        f"Embedding {len(pending_ids)} new ids with {model_name!r} (raw={seen}, already={len(existing_ids)}, skipped_no_text={skipped_no_text}).",
    )
    encoder = _build_encoder(model_name)
    new_vectors = encoder.encode(
        pending_texts,
        batch_size=_BATCH_SIZE,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    ).astype(np.float32, copy=False)

    if existing_arr.shape[0] > 0:
        if existing_arr.shape[1] != new_vectors.shape[1]:
            raise ValueError(
                f"Embedding dim mismatch: existing={existing_arr.shape[1]}, "
                f"new={new_vectors.shape[1]} (model={model_name!r}). "
                "Delete Markdown/embeddings/ to rebuild.",
            )
        combined_arr = np.vstack([existing_arr, new_vectors])
    else:
        combined_arr = new_vectors
    combined_ids = existing_ids + pending_ids
    meta = {
        "model": model_name,
        "dim": int(combined_arr.shape[1]),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    save_atomic(combined_arr, combined_ids, meta)
    logger.info(f"Embedded {len(pending_ids)} new ids; total {len(combined_ids)}.")
    return len(pending_ids)


# @lat: [[embeddings#CLI entry]]
def main() -> None:
    """Walk Markdown/raw, embed new ids, persist to Markdown/embeddings/."""
    logger.info("Starting embeddings worker.")
    count = embed_pending()
    logger.info(f"Embeddings finished: {count} new vectors.")


if __name__ == "__main__":
    main()
