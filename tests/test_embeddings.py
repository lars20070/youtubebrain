"""Unit tests for the local SentenceTransformer embedding stage."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import numpy as np
import pytest

from youtubebrain import config
from youtubebrain import embeddings as emb

if TYPE_CHECKING:
    from pathlib import Path


_MD_TEMPLATE = """\
---
id: {id}
url: https://www.youtube.com/watch?v={id}
title: {title}
channels: []
watch_time: '2026-05-06T14:50:16.546000+00:00'
---

## Summary

{summary}

## Description

{description}

## Transcript

{transcript}
"""


def _write_md(path: Path, **fields: str) -> Path:
    defaults = {
        "id": "vidA",
        "title": "Some Title",
        "summary": "_(unavailable)_",
        "description": "_(unavailable)_",
        "transcript": "_(unavailable)_",
    }
    defaults.update(fields)
    path.write_text(_MD_TEMPLATE.format(**defaults), encoding="utf-8")
    return path


class _StubEncoder:
    """Deterministic encoder used in place of SentenceTransformer."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int,  # noqa: ARG002
        normalize_embeddings: bool,  # noqa: ARG002
        convert_to_numpy: bool,  # noqa: ARG002
        show_progress_bar: bool,  # noqa: ARG002
    ) -> np.ndarray:
        self.calls.append(list(texts))
        rows = []
        for i, _ in enumerate(texts):
            rows.append(np.arange(self.dim, dtype=np.float32) + float(i))
        return np.asarray(rows, dtype=np.float32)


def _patch_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the config persistence constants at tmp_path; return the embeddings dir."""
    out_dir = tmp_path / "embeddings"
    monkeypatch.setattr(config, "EMBEDDINGS_DIR", out_dir)
    monkeypatch.setattr(config, "EMBEDDINGS_NPY_PATH", out_dir / "embeddings.npy")
    monkeypatch.setattr(config, "EMBEDDINGS_IDS_JSON_PATH", out_dir / "ids.json")
    monkeypatch.setattr(config, "EMBEDDINGS_META_JSON_PATH", out_dir / "meta.json")
    return out_dir


# @lat: [[embeddings#Tests#Frontmatter parsing]]
def test_parse_raw_markdown_extracts_fields(tmp_path: Path) -> None:
    """parse_raw_markdown returns id, title, and section bodies from a typical raw file."""
    path = _write_md(
        tmp_path / "vid.md",
        id="vid1",
        title="Why doormen matter",
        summary="A short summary body.",
        description="Promo description body.",
    )
    vid, title, summary, description = emb.parse_raw_markdown(path)
    assert vid == "vid1"
    assert title == "Why doormen matter"
    assert summary == "A short summary body."
    assert description == "Promo description body."


# @lat: [[embeddings#Tests#Unavailable placeholder recognized as missing]]
def test_unavailable_placeholder_treated_as_missing(tmp_path: Path) -> None:
    """Section bodies equal to `_(unavailable)_` parse to None, not text."""
    path = _write_md(tmp_path / "vid.md", id="vid1", title="T", summary="_(unavailable)_", description="real desc")
    _vid, _title, summary, description = emb.parse_raw_markdown(path)
    assert summary is None
    assert description == "real desc"


# @lat: [[embeddings#Tests#Compose prefers summary]]
def test_compose_prefers_summary() -> None:
    """When both summary and description are present, summary is used."""
    text = emb.compose_text("Title", "summary body", "description body")
    assert text == "Title\n\nsummary body"


# @lat: [[embeddings#Tests#Compose falls back to description]]
def test_compose_falls_back_to_description() -> None:
    """Missing summary falls back to description."""
    text = emb.compose_text("Title", None, "description body")
    assert text == "Title\n\ndescription body"


# @lat: [[embeddings#Tests#Compose skipped when both missing]]
def test_compose_returns_none_when_both_missing() -> None:
    """Both fields missing returns None (caller should skip)."""
    assert emb.compose_text("Title", None, None) is None


# @lat: [[embeddings#Tests#ids.json round-trip]]
def test_save_and_load_ids_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """save_atomic then load_existing returns identical ids list and aligned array."""
    _patch_store(monkeypatch, tmp_path)
    arr = np.arange(8, dtype=np.float32).reshape(2, 4)
    ids = ["a", "b"]
    meta = {"model": "stub", "dim": 4, "updated_at": "now"}
    emb.save_atomic(arr, ids, meta)
    loaded_arr, loaded_ids, loaded_meta = emb.load_existing()
    assert loaded_ids == ids
    assert loaded_meta == meta
    np.testing.assert_array_equal(loaded_arr, arr)


# @lat: [[embeddings#Tests#embeddings.npy shape and dtype]]
def test_saved_array_has_float32_dtype_and_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Persisted embeddings file is float32 with shape (N, dim)."""
    _patch_store(monkeypatch, tmp_path)
    arr = np.ones((3, 4), dtype=np.float32)
    emb.save_atomic(arr, ["a", "b", "c"], {})
    loaded = np.load(tmp_path / "embeddings" / "embeddings.npy")
    assert loaded.dtype == np.float32
    assert loaded.shape == (3, 4)


# @lat: [[embeddings#Tests#meta.json records model and dim]]
def test_embed_pending_writes_meta(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """After a run, meta.json contains model, dim, and an updated_at timestamp."""
    out_dir = _patch_store(monkeypatch, tmp_path)
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_md(raw / "vid1.md", id="vid1", title="T", summary="hello")
    monkeypatch.setenv("EMBEDDING_MODEL", "stub-model")
    monkeypatch.setattr(emb, "_build_encoder", lambda _name: _StubEncoder())

    emb.embed_pending(raw)

    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["model"] == "stub-model"
    assert meta["dim"] == 4
    assert "updated_at" in meta


# @lat: [[embeddings#Tests#EMBEDDING_MODEL env override]]
def test_embedding_model_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """EMBEDDING_MODEL env var is forwarded to _build_encoder."""
    _patch_store(monkeypatch, tmp_path)
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_md(raw / "vid1.md", id="vid1", title="T", summary="hello")
    monkeypatch.setenv("EMBEDDING_MODEL", "custom/model")
    captured: list[str] = []

    def fake_builder(name: str) -> _StubEncoder:
        captured.append(name)
        return _StubEncoder()

    monkeypatch.setattr(emb, "_build_encoder", fake_builder)
    emb.embed_pending(raw)
    assert captured == ["custom/model"]


# @lat: [[embeddings#Tests#Idempotent re-run]]
def test_embed_pending_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A second run over unchanged raw markdown adds zero vectors."""
    _patch_store(monkeypatch, tmp_path)
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_md(raw / "vid1.md", id="vid1", title="T", summary="hello")
    encoder = _StubEncoder()
    monkeypatch.setattr(emb, "_build_encoder", lambda _name: encoder)

    first = emb.embed_pending(raw)
    second = emb.embed_pending(raw)

    assert first == 1
    assert second == 0
    assert len(encoder.calls) == 1
    _arr, ids, _meta = emb.load_existing()
    assert ids == ["vid1"]


# @lat: [[embeddings#Tests#Skips videos with no embeddable text]]
def test_skips_video_with_no_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Video where both summary and description are unavailable is not embedded."""
    _patch_store(monkeypatch, tmp_path)
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_md(raw / "vid_empty.md", id="vid_empty", title="T")
    _write_md(raw / "vid_ok.md", id="vid_ok", title="T", summary="hello")
    monkeypatch.setattr(emb, "_build_encoder", lambda _name: _StubEncoder())

    count = emb.embed_pending(raw)
    assert count == 1
    _arr, ids, _meta = emb.load_existing()
    assert ids == ["vid_ok"]


# @lat: [[embeddings#Tests#Atomic write leaves no partial files on crash]]
def test_save_atomic_leaves_no_partial_files_on_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If np.save raises mid-write, no `*.tmp` is left behind in the embeddings dir."""
    out_dir = _patch_store(monkeypatch, tmp_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    arr = np.ones((1, 4), dtype=np.float32)

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(emb.np, "save", boom)
    with pytest.raises(OSError, match="disk full"):
        emb.save_atomic(arr, ["a"], {})

    lingering = list(out_dir.glob("*.tmp")) + list(out_dir.glob("*.tmp.npy"))
    assert lingering == []
    assert not (out_dir / "embeddings.npy").exists()


# @lat: [[embeddings#Tests#Dim mismatch raises]]
def test_dim_mismatch_raises_without_writing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A model that returns a different dim than the existing store raises and does not overwrite."""
    out_dir = _patch_store(monkeypatch, tmp_path)
    pre_arr = np.ones((1, 8), dtype=np.float32)
    emb.save_atomic(pre_arr, ["pre"], {"model": "old", "dim": 8, "updated_at": "old"})
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_md(raw / "vid1.md", id="vid1", title="T", summary="hello")
    monkeypatch.setattr(emb, "_build_encoder", lambda _name: _StubEncoder(dim=4))

    with pytest.raises(ValueError, match="dim"):
        emb.embed_pending(raw)

    loaded = np.load(out_dir / "embeddings.npy")
    assert loaded.shape == (1, 8)
    assert json.loads((out_dir / "ids.json").read_text()) == ["pre"]


# @lat: [[embeddings#Tests#Real encoder smoke]]
@pytest.mark.slow_embedding
def test_real_encoder_smoke() -> None:
    """Downloads the real all-MiniLM-L6-v2 model and encodes a tiny input."""
    encoder = emb._build_encoder("sentence-transformers/all-MiniLM-L6-v2")
    vec = encoder.encode(
        ["hello world"],
        batch_size=1,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    assert vec.shape == (1, 384)
    assert vec.dtype in (np.float32, np.float64)
