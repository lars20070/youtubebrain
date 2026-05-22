"""Unit tests for the BERTopic-driven clustering + LLM labelling stage."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
import yaml

from youtubebrain import clusters
from youtubebrain import embeddings as emb
from youtubebrain.clusters import TopicLabel

if TYPE_CHECKING:
    from collections.abc import Iterator


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
    defaults: dict[str, str] = {
        "id": "vidA",
        "title": "Some Title",
        "summary": "_(unavailable)_",
        "description": "_(unavailable)_",
        "transcript": "_(unavailable)_",
    }
    defaults.update(fields)
    path.write_text(_MD_TEMPLATE.format(**defaults), encoding="utf-8")
    return path


def _patch_stores(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    """Redirect embeddings + clustering + raw-markdown constants at tmp paths."""
    emb_dir = tmp_path / "embeddings"
    monkeypatch.setattr(emb, "EMBEDDINGS_DIR", emb_dir)
    monkeypatch.setattr(emb, "EMBEDDINGS_NPY_PATH", emb_dir / "embeddings.npy")
    monkeypatch.setattr(emb, "IDS_JSON_PATH", emb_dir / "ids.json")
    monkeypatch.setattr(emb, "META_JSON_PATH", emb_dir / "meta.json")

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    monkeypatch.setattr(emb, "MARKDOWN_RAW_DIR", raw_dir)

    cl_dir = tmp_path / "clustering"
    monkeypatch.setattr(clusters, "CLUSTERING_DIR", cl_dir)
    monkeypatch.setattr(clusters, "ASSIGNMENTS_JSON_PATH", cl_dir / "assignments.json")
    monkeypatch.setattr(clusters, "TOPICS_JSON_PATH", cl_dir / "topics.json")
    monkeypatch.setattr(clusters, "META_JSON_PATH", cl_dir / "meta.json")
    monkeypatch.setattr(clusters, "MODEL_DIR", cl_dir / "bertopic_model")

    wiki_topics_dir = tmp_path / "wiki" / "topics"
    monkeypatch.setattr(clusters, "WIKI_TOPICS_DIR", wiki_topics_dir)
    return emb_dir, raw_dir, cl_dir


def _seed_embeddings(emb_dir: Path, ids: list[str], dim: int = 4, model: str = "stub-model") -> None:
    """Write a minimal embeddings trio for the given ids."""
    emb_dir.mkdir(parents=True, exist_ok=True)
    arr = np.arange(len(ids) * dim, dtype=np.float32).reshape(len(ids), dim)
    np.save(emb_dir / "embeddings.npy", arr, allow_pickle=False)
    (emb_dir / "ids.json").write_text(json.dumps(ids), encoding="utf-8")
    (emb_dir / "meta.json").write_text(
        json.dumps({"model": model, "dim": dim, "updated_at": "2026-05-21T00:00:00+00:00"}),
        encoding="utf-8",
    )


class _StubColumn:
    """Minimal pandas-Column-shaped object with .tolist()."""

    def __init__(self, vals: list[int]) -> None:
        self._vals = vals

    def tolist(self) -> list[int]:
        return list(self._vals)


class _StubTopicInfo:
    """Minimal pandas-DataFrame-shaped object exposing only the columns clusters.py reads."""

    def __init__(self, rows: list[tuple[int, int]]) -> None:
        self._rows = rows

    def __getitem__(self, key: str) -> _StubColumn:
        if key == "Topic":
            return _StubColumn([r[0] for r in self._rows])
        if key == "Count":
            return _StubColumn([r[1] for r in self._rows])
        raise KeyError(key)

    def iterrows(self) -> Iterator[tuple[int, dict[str, int]]]:
        for i, (topic_id, count) in enumerate(self._rows):
            yield i, {"Topic": topic_id, "Count": count}


class _StubTopicModel:
    """Deterministic stand-in for a fitted BERTopic, exposing only the methods clusters.py calls."""

    def __init__(
        self,
        assignments: list[int],
        topics: dict[int, dict[str, list[str]]],
    ) -> None:
        self._assignments = assignments
        self._topics = topics
        self.saved_to: Path | None = None
        self.save_kwargs: dict[str, Any] | None = None

    def fit_transform(
        self,
        *,
        documents: list[str],
        embeddings: object,
    ) -> tuple[list[int], None]:
        _ = documents, embeddings
        return list(self._assignments), None

    def get_topic_info(self) -> _StubTopicInfo:
        counts: dict[int, int] = {}
        for topic_id in self._assignments:
            counts[topic_id] = counts.get(topic_id, 0) + 1
        return _StubTopicInfo(sorted(counts.items()))

    def get_topic(self, topic_id: int) -> list[tuple[str, float]]:
        keywords = self._topics.get(topic_id, {}).get("keywords", [])
        return [(w, 1.0 - i * 0.1) for i, w in enumerate(keywords)]

    def get_representative_docs(self, topic_id: int) -> list[str]:
        return list(self._topics.get(topic_id, {}).get("rep_docs", []))

    def save(
        self,
        path: str,
        *,
        serialization: str,
        save_ctfidf: bool,
        save_embedding_model: bool,
    ) -> None:
        out = Path(path)
        out.mkdir(parents=True, exist_ok=True)
        (out / "topic_model.safetensors").write_text("stub", encoding="utf-8")
        self.saved_to = out
        self.save_kwargs = {
            "serialization": serialization,
            "save_ctfidf": save_ctfidf,
            "save_embedding_model": save_embedding_model,
        }


class _StubAgentResult:
    def __init__(self, output: TopicLabel) -> None:
        self.output = output


class _StubAgent:
    """Stand-in for `pydantic_ai.Agent` that returns deterministic labels and optionally raises."""

    def __init__(self, raise_for_keywords: set[str] | None = None) -> None:
        self.raise_for_keywords = raise_for_keywords or set()
        self.calls: list[str] = []

    async def run(self, *, user_prompt: str) -> _StubAgentResult:
        self.calls.append(user_prompt)
        for kw in self.raise_for_keywords:
            if kw in user_prompt:
                raise RuntimeError(f"stub agent failure on {kw!r}")
        first_line = user_prompt.split("\n", 1)[0]
        first_kw = first_line.removeprefix("KEYWORDS: ").split(",")[0].strip() or "untitled"
        return _StubAgentResult(
            TopicLabel(label=f"{first_kw}-cluster", description=f"Cluster about {first_kw}."),
        )


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    topic_model: _StubTopicModel,
    agent: _StubAgent,
) -> None:
    """Replace UMAP/HDBSCAN/BERTopic builders + the label agent factory with offline stubs."""

    def _dummy_umap() -> object:
        return object()

    def _dummy_hdbscan(_min_size: int) -> object:
        return object()

    def _dummy_topic_model(_umap: object, _hdbscan: object, _embedding_model_name: str | None) -> _StubTopicModel:
        return topic_model

    def _dummy_agent() -> _StubAgent:
        return agent

    def _dummy_version() -> str:
        return "0.16.0-stub"

    monkeypatch.setattr(clusters, "_build_umap", _dummy_umap)
    monkeypatch.setattr(clusters, "_build_hdbscan", _dummy_hdbscan)
    monkeypatch.setattr(clusters, "_build_topic_model", _dummy_topic_model)
    monkeypatch.setattr(clusters, "_build_label_agent", _dummy_agent)
    monkeypatch.setattr(clusters, "_bertopic_version", _dummy_version)


# @lat: [[clusters#Tests#Round-trip persistence]]
def test_save_atomic_and_load_existing_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """save_atomic followed by load_existing returns the same assignments, topics, and meta."""
    _patch_stores(monkeypatch, tmp_path)
    assignments = [0, 1, -1, 0, 1]
    topics = [
        clusters.TopicInfo(cluster_id=-1, count=1, label="outliers", description="x", keywords=[], representative_ids=[]),
        clusters.TopicInfo(cluster_id=0, count=2, label="a-cluster", description="d0", keywords=["a", "b"], representative_ids=["v1"]),
        clusters.TopicInfo(cluster_id=1, count=2, label="c-cluster", description="d1", keywords=["c"], representative_ids=["v2"]),
    ]
    meta = {"embedding_model": "stub", "n_clusters": 2}
    clusters.save_atomic(assignments, topics, meta, topic_model=None)

    loaded_assignments, loaded_topics, loaded_meta = clusters.load_existing()
    assert loaded_assignments == assignments
    assert [t.model_dump() for t in loaded_topics] == [t.model_dump() for t in topics]
    assert loaded_meta == meta


# @lat: [[clusters#Tests#Assignments aligned with ids]]
def test_assignments_align_with_ids_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """len(assignments) == len(ids.json) after cluster_all on a fixture embeddings store."""
    emb_dir, _raw, _cl = _patch_stores(monkeypatch, tmp_path)
    ids = [f"vid{i:02d}" for i in range(6)]
    _seed_embeddings(emb_dir, ids)
    monkeypatch.setenv(clusters._MIN_SIZE_ENV, "2")

    assignments_list = [0, 0, 1, 1, -1, 0]
    topic_model = _StubTopicModel(
        assignments_list,
        {
            0: {"keywords": ["alpha", "beta"], "rep_docs": []},
            1: {"keywords": ["gamma"], "rep_docs": []},
        },
    )
    _patch_pipeline(monkeypatch, topic_model, _StubAgent())

    clusters.cluster_all()

    saved_ids = json.loads((emb_dir / "ids.json").read_text(encoding="utf-8"))
    saved_assignments = json.loads(clusters.ASSIGNMENTS_JSON_PATH.read_text(encoding="utf-8"))
    assert len(saved_assignments) == len(saved_ids)
    assert saved_assignments == assignments_list


# @lat: [[clusters#Tests#Meta records run summary]]
def test_meta_records_run_summary_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """meta.json records n_clusters, n_outliers, min_cluster_size, llm_model, bertopic_version."""
    emb_dir, _raw, _cl = _patch_stores(monkeypatch, tmp_path)
    ids = [f"vid{i:02d}" for i in range(6)]
    _seed_embeddings(emb_dir, ids)
    monkeypatch.setenv(clusters._MIN_SIZE_ENV, "2")
    monkeypatch.setenv("MODEL", "stub-label-model")
    monkeypatch.setenv("PROVIDER", "ollama")

    topic_model = _StubTopicModel(
        [0, 0, 1, 1, -1, 0],
        {0: {"keywords": ["a"], "rep_docs": []}, 1: {"keywords": ["b"], "rep_docs": []}},
    )
    _patch_pipeline(monkeypatch, topic_model, _StubAgent())

    clusters.cluster_all()

    meta = json.loads(clusters.META_JSON_PATH.read_text(encoding="utf-8"))
    assert meta["n_clusters"] == 2
    assert meta["n_outliers"] == 1
    assert meta["min_cluster_size"] == 2
    assert meta["llm_model"] == "stub-label-model"
    assert meta["bertopic_version"] == "0.16.0-stub"
    assert meta["embedding_model"] == "stub-model"


# @lat: [[clusters#Tests#CLUSTER_MIN_SIZE env override]]
def test_cluster_min_size_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLUSTER_MIN_SIZE env beats the sqrt(n) heuristic."""
    monkeypatch.setenv(clusters._MIN_SIZE_ENV, "37")
    assert clusters._resolve_min_cluster_size(10_000) == 37


# @lat: [[clusters#Tests#Min-size heuristic table]]
@pytest.mark.parametrize(
    ("n_videos", "expected"),
    [
        (1, clusters._MIN_SIZE_FLOOR),
        (50, clusters._MIN_SIZE_FLOOR),
        (100, max(clusters._MIN_SIZE_FLOOR, round(math.sqrt(100)))),
        (517, round(math.sqrt(517))),
        (10_000, round(math.sqrt(10_000))),
    ],
)
def test_min_size_heuristic_uses_floor_then_sqrt(monkeypatch: pytest.MonkeyPatch, n_videos: int, expected: int) -> None:
    """Heuristic returns floor for small n, round(sqrt(n)) for n above the floor squared."""
    monkeypatch.delenv(clusters._MIN_SIZE_ENV, raising=False)
    assert clusters._resolve_min_cluster_size(n_videos) == expected


# @lat: [[clusters#Tests#Cluster all happy path]]
def test_cluster_all_produces_aligned_assignments_and_topic_infos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """cluster_all with stubbed pipeline aligns assignments and emits one TopicInfo per cluster."""
    emb_dir, _raw, _cl = _patch_stores(monkeypatch, tmp_path)
    ids = [f"vid{i:02d}" for i in range(6)]
    _seed_embeddings(emb_dir, ids)
    monkeypatch.setenv(clusters._MIN_SIZE_ENV, "2")

    topic_model = _StubTopicModel(
        [0, 0, 0, 1, 1, 1],
        {
            0: {"keywords": ["alpha", "beta"], "rep_docs": []},
            1: {"keywords": ["gamma", "delta"], "rep_docs": []},
        },
    )
    _patch_pipeline(monkeypatch, topic_model, _StubAgent())

    n_clusters = clusters.cluster_all()
    assert n_clusters == 2

    topics_payload = json.loads(clusters.TOPICS_JSON_PATH.read_text(encoding="utf-8"))
    cluster_ids = sorted(t["cluster_id"] for t in topics_payload)
    assert cluster_ids == [0, 1]
    labels = {t["cluster_id"]: t["label"] for t in topics_payload}
    assert labels[0] == "alpha-cluster"
    assert labels[1] == "gamma-cluster"


# @lat: [[clusters#Tests#Refuses with no embeddings]]
def test_cluster_all_refuses_when_embeddings_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """cluster_all raises ValueError mentioning embeddings when the store is empty."""
    _patch_stores(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="no embeddings"):
        clusters.cluster_all()


# @lat: [[clusters#Tests#Refuses when too few embeddings]]
def test_cluster_all_refuses_when_too_few_embeddings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """cluster_all raises ValueError with an actionable message when n_videos < 2 * min_cluster_size."""
    emb_dir, _raw, _cl = _patch_stores(monkeypatch, tmp_path)
    ids = ["vid0", "vid1", "vid2"]
    _seed_embeddings(emb_dir, ids)
    monkeypatch.setenv(clusters._MIN_SIZE_ENV, "5")

    with pytest.raises(ValueError, match="too few embeddings"):
        clusters.cluster_all()


# @lat: [[clusters#Tests#Outlier cluster is synthetic]]
def test_outlier_cluster_gets_synthetic_topic_info(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cluster -1 is recorded as label="outliers" without an LLM call."""
    emb_dir, _raw, _cl = _patch_stores(monkeypatch, tmp_path)
    ids = [f"vid{i:02d}" for i in range(6)]
    _seed_embeddings(emb_dir, ids)
    monkeypatch.setenv(clusters._MIN_SIZE_ENV, "2")

    topic_model = _StubTopicModel(
        [0, 0, 1, 1, -1, -1],
        {
            0: {"keywords": ["a"], "rep_docs": []},
            1: {"keywords": ["b"], "rep_docs": []},
            -1: {"keywords": ["noise"], "rep_docs": []},
        },
    )
    agent = _StubAgent()
    _patch_pipeline(monkeypatch, topic_model, agent)

    clusters.cluster_all()

    topics_payload = json.loads(clusters.TOPICS_JSON_PATH.read_text(encoding="utf-8"))
    outlier = next(t for t in topics_payload if t["cluster_id"] == -1)
    assert outlier["label"] == "outliers"
    assert outlier["keywords"] == []
    assert outlier["representative_ids"] == []
    assert len(agent.calls) == 2  # only the two real clusters got an LLM call


# @lat: [[clusters#Tests#Agent error triggers fallback label]]
def test_agent_exception_falls_back_per_cluster(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If the agent raises on one cluster, only that cluster gets the fallback label; others unaffected."""
    emb_dir, _raw, _cl = _patch_stores(monkeypatch, tmp_path)
    ids = [f"vid{i:02d}" for i in range(6)]
    _seed_embeddings(emb_dir, ids)
    monkeypatch.setenv(clusters._MIN_SIZE_ENV, "2")

    topic_model = _StubTopicModel(
        [0, 0, 0, 1, 1, 1],
        {
            0: {"keywords": ["BOOM", "explode"], "rep_docs": []},
            1: {"keywords": ["gamma"], "rep_docs": []},
        },
    )
    _patch_pipeline(monkeypatch, topic_model, _StubAgent(raise_for_keywords={"BOOM"}))

    clusters.cluster_all()

    topics_payload = json.loads(clusters.TOPICS_JSON_PATH.read_text(encoding="utf-8"))
    by_id = {t["cluster_id"]: t for t in topics_payload}
    assert by_id[0]["label"] == "topic-0-BOOM"
    assert by_id[1]["label"] == "gamma-cluster"


# @lat: [[clusters#Tests#Refuses when embedding model changed]]
def test_refuses_when_embedding_model_changed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """cluster_all refuses when embeddings/meta.json.model differs from existing clustering/meta.json.embedding_model."""
    emb_dir, _raw, cl_dir = _patch_stores(monkeypatch, tmp_path)
    ids = [f"vid{i:02d}" for i in range(6)]
    _seed_embeddings(emb_dir, ids, model="new-model")
    cl_dir.mkdir(parents=True, exist_ok=True)
    clusters.ASSIGNMENTS_JSON_PATH.write_text(json.dumps([0] * 6), encoding="utf-8")
    clusters.TOPICS_JSON_PATH.write_text(
        json.dumps(
            [
                {
                    "cluster_id": 0,
                    "count": 6,
                    "label": "old-label",
                    "description": "d",
                    "keywords": ["k"],
                    "representative_ids": [],
                },
            ],
        ),
        encoding="utf-8",
    )
    clusters.META_JSON_PATH.write_text(json.dumps({"embedding_model": "old-model"}), encoding="utf-8")
    monkeypatch.setenv(clusters._MIN_SIZE_ENV, "2")

    with pytest.raises(ValueError, match="embedding model changed"):
        clusters.cluster_all()
    # cluster files not overwritten
    assert json.loads(clusters.META_JSON_PATH.read_text(encoding="utf-8"))["embedding_model"] == "old-model"


# @lat: [[clusters#Tests#Atomic write leaves no partial files on crash]]
def test_save_atomic_leaves_no_tmp_siblings_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Mid-write failure leaves no `*.tmp` siblings behind in the clustering dir."""
    _emb, _raw, cl_dir = _patch_stores(monkeypatch, tmp_path)
    cl_dir.mkdir(parents=True, exist_ok=True)
    assignments = [0, 1]
    topics = [
        clusters.TopicInfo(cluster_id=0, count=1, label="a", description="d", keywords=[], representative_ids=[]),
        clusters.TopicInfo(cluster_id=1, count=1, label="b", description="d", keywords=[], representative_ids=[]),
    ]

    real_replace = clusters.os.replace
    call_counter = {"n": 0}

    def flaky_replace(src: str | Path, dst: str | Path) -> None:
        call_counter["n"] += 1
        if call_counter["n"] == 2:
            raise OSError("disk full")
        real_replace(src, dst)

    monkeypatch.setattr(clusters.os, "replace", flaky_replace)

    with pytest.raises(OSError, match="disk full"):
        clusters.save_atomic(assignments, topics, {"embedding_model": "stub"}, topic_model=None)

    lingering = list(cl_dir.glob("*.tmp")) + list(cl_dir.glob("**/*.tmp"))
    assert lingering == []


# @lat: [[clusters#Tests#User prompt formatting]]
def test_user_prompt_includes_keywords_and_rep_text() -> None:
    """_build_user_prompt formats KEYWORDS line and numbered representative excerpts."""
    prompt = clusters._build_user_prompt(["alpha", "beta"], ["First doc", "Second doc"])
    assert prompt.startswith("KEYWORDS: alpha, beta")
    assert "1. First doc" in prompt
    assert "2. Second doc" in prompt


# @lat: [[clusters#Tests#Fallback label deterministic]]
def test_fallback_label_uses_first_keyword() -> None:
    """_fallback_label builds a deterministic label/description from the cluster keywords."""
    label = clusters._fallback_label(3, ["rust", "tokio", "async"])
    assert label.label == "topic-3-rust"
    assert "rust" in label.description


# @lat: [[clusters#Tests#Label concurrency env override]]
def test_label_concurrency_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """LABEL_CONCURRENCY env beats the default; invalid values fall back to >= 1."""
    monkeypatch.setenv(clusters._LABEL_CONCURRENCY_ENV, "12")
    assert clusters._label_concurrency() == 12
    monkeypatch.setenv(clusters._LABEL_CONCURRENCY_ENV, "0")
    assert clusters._label_concurrency() == 1


# @lat: [[clusters#Tests#Label clusters semaphore caps concurrency]]
def test_label_clusters_semaphore_caps_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """_label_clusters never lets more than `concurrency` agent calls run concurrently."""
    _ = monkeypatch  # marker for fixture discovery
    inflight = 0
    peak = 0

    class _ProbeAgent:
        async def run(self, *, user_prompt: str) -> _StubAgentResult:
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            try:
                await asyncio.sleep(0.01)
            finally:
                inflight -= 1
            return _StubAgentResult(TopicLabel(label="x-y-z", description=user_prompt[:10] or "."))

    payloads = [(cid, [f"kw{cid}"], []) for cid in range(8)]
    asyncio.run(clusters._label_clusters(payloads, _ProbeAgent(), concurrency=2))  # type: ignore[arg-type]
    assert peak <= 2


# @lat: [[clusters#Tests#Texts by id walks raw markdown]]
def test_load_texts_by_id_reads_raw_markdown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_load_texts_by_id returns composed text for ids present in Markdown/raw/."""
    _emb, raw_dir, _cl = _patch_stores(monkeypatch, tmp_path)
    _write_md(raw_dir / "vid1.md", id="vid1", title="T1", summary="Body One")
    _write_md(raw_dir / "vid2.md", id="vid2", title="T2", description="Desc Two")
    _write_md(raw_dir / "vidGhost.md", id="vidGhost", title="Ghost")  # both unavailable

    out = clusters._load_texts_by_id(["vid1", "vid2", "vidGhost", "vidMissing"])
    assert out["vid1"] == "T1\n\nBody One"
    assert out["vid2"] == "T2\n\nDesc Two"
    assert "vidGhost" not in out  # both summary and description are unavailable
    assert "vidMissing" not in out  # no file on disk


def _topic(cid: int, label: str, count: int = 1, **overrides: Any) -> clusters.TopicInfo:  # noqa: ANN401
    """Minimal TopicInfo factory for the wiki-topic tests."""
    base: dict[str, Any] = {
        "cluster_id": cid,
        "count": count,
        "label": label,
        "description": "desc.",
        "keywords": [],
        "representative_ids": [],
    }
    base.update(overrides)
    return clusters.TopicInfo(**base)


# @lat: [[clusters#Wiki topics#Tests#Slug resolution dedups]]
def test_resolve_slugs_dedups_collisions_and_handles_outlier() -> None:
    """Two clusters sharing label `x` resolve to `x` and `x-1`; outlier maps to `outliers`."""
    topics = [
        _topic(-1, "anything"),
        _topic(0, "x"),
        _topic(1, "x"),
        _topic(2, "y"),
        _topic(3, "x"),
    ]
    slugs = clusters._resolve_slugs(topics)
    assert slugs[-1] == "outliers"
    assert slugs[0] == "x"
    assert slugs[1] == "x-1"
    assert slugs[2] == "y"
    assert slugs[3] == "x-2"


# @lat: [[clusters#Wiki topics#Tests#Slug sanitisation]]
def test_resolve_slugs_sanitises_unsafe_labels() -> None:
    """Path traversal, whitespace, and empty labels cannot escape the topics directory."""
    topics = [
        _topic(0, "../etc/passwd"),
        _topic(1, "Rust  Async"),
        _topic(2, "   "),
        _topic(3, "--leading--and--trailing--"),
    ]
    slugs = clusters._resolve_slugs(topics)
    for slug in slugs.values():
        assert "/" not in slug
        assert ".." not in slug
        assert slug == slug.strip("-")
    assert slugs[0] == "etc-passwd"
    assert slugs[1] == "rust-async"
    assert slugs[2] == "topic-2"
    assert slugs[3] == "leading-and-trailing"


# @lat: [[clusters#Wiki topics#Tests#Wipe & rewrite removes stale folders]]
def test_write_wiki_topics_wipes_stale_folders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A pre-existing topic folder is removed before the current run writes its slugs."""
    _emb, raw_dir, _cl = _patch_stores(monkeypatch, tmp_path)
    stale_dir = clusters.WIKI_TOPICS_DIR / "stale"
    stale_dir.mkdir(parents=True)
    (stale_dir / "stale.md").write_text("old", encoding="utf-8")

    _write_md(raw_dir / "vid1.md", id="vid1", title="One")
    _write_md(raw_dir / "vid2.md", id="vid2", title="Two")
    topics = [_topic(0, "fresh-topic", count=2)]
    clusters.write_wiki_topics([0, 0], ["vid1", "vid2"], topics)

    assert not stale_dir.exists()
    assert (clusters.WIKI_TOPICS_DIR / "fresh-topic" / "fresh-topic.md").exists()


# @lat: [[clusters#Wiki topics#Tests#Topic page rendered correctly]]
def test_topic_page_frontmatter_and_member_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`<slug>/<slug>.md` has the expected frontmatter dict and a bullet per member."""
    _emb, raw_dir, _cl = _patch_stores(monkeypatch, tmp_path)
    _write_md(raw_dir / "vidA.md", id="vidA", title="Alpha Title")
    _write_md(raw_dir / "vidB.md", id="vidB", title="Beta Title")
    topic = _topic(
        0,
        "demo-cluster",
        count=2,
        description="A demo cluster.",
        keywords=["alpha", "beta"],
        representative_ids=["vidA"],
    )
    clusters.write_wiki_topics([0, 0], ["vidA", "vidB"], [topic])

    page = (clusters.WIKI_TOPICS_DIR / "demo-cluster" / "demo-cluster.md").read_text(encoding="utf-8")
    fence = "---"
    assert page.startswith(fence + "\n")
    _, fm_text, body = page.split(fence + "\n", 2)
    fm = yaml.safe_load(fm_text)
    assert fm == {
        "label": "demo-cluster",
        "cluster_id": 0,
        "count": 2,
        "keywords": ["alpha", "beta"],
        "representative_ids": ["vidA"],
    }
    assert "# demo-cluster" in body
    assert "A demo cluster." in body
    assert "- Alpha Title ([vidA](../../../raw/vidA.md))" in body
    assert "- Beta Title ([vidB](../../../raw/vidB.md))" in body


# @lat: [[clusters#Wiki topics#Tests#Raw frontmatter injected]]
def test_inject_topic_into_raw_adds_fields_idempotently(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`_inject_topic_into_raw` adds `topic` + `cluster_id` without touching body; second call is no-op."""
    _emb, raw_dir, _cl = _patch_stores(monkeypatch, tmp_path)
    path = _write_md(raw_dir / "vid1.md", id="vid1", title="Hi", summary="Body One")

    clusters._inject_topic_into_raw(path, "demo-cluster", 0)
    text1 = path.read_text(encoding="utf-8")
    fence = "---"
    _, fm_text, body = text1.split(fence + "\n", 2)
    fm = yaml.safe_load(fm_text)
    assert fm["topic"] == "demo-cluster"
    assert fm["cluster_id"] == 0
    assert fm["id"] == "vid1"
    assert fm["title"] == "Hi"
    assert "## Summary" in body
    assert "Body One" in body

    clusters._inject_topic_into_raw(path, "demo-cluster", 0)
    text2 = path.read_text(encoding="utf-8")
    assert text1 == text2


# @lat: [[clusters#Wiki topics#Tests#Raw frontmatter never duplicated]]
def test_inject_topic_never_duplicates_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Re-running with different values leaves each key exactly once; pre-existing duplicates collapse."""
    _emb, raw_dir, _cl = _patch_stores(monkeypatch, tmp_path)
    path = _write_md(raw_dir / "vid1.md", id="vid1", title="Hi")

    clusters._inject_topic_into_raw(path, "first-slug", 1)
    clusters._inject_topic_into_raw(path, "second-slug", 7)
    text = path.read_text(encoding="utf-8")
    assert text.count("\ntopic:") == 1
    assert text.count("\ncluster_id:") == 1
    assert "topic: second-slug" in text
    assert "cluster_id: 7" in text

    duplicated = "---\nid: vid2\ntitle: Dup\ntopic: stale-1\ntopic: stale-2\ncluster_id: 9\ncluster_id: 11\n---\n\n## Summary\n\nbody\n"
    dup_path = raw_dir / "vid2.md"
    dup_path.write_text(duplicated, encoding="utf-8")
    clusters._inject_topic_into_raw(dup_path, "third-slug", 3)
    fixed = dup_path.read_text(encoding="utf-8")
    assert fixed.count("\ntopic:") == 1
    assert fixed.count("\ncluster_id:") == 1
    assert "topic: third-slug" in fixed
    assert "cluster_id: 3" in fixed


# @lat: [[clusters#Wiki topics#Tests#Outliers folder written]]
def test_outliers_folder_and_raw_injection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Outlier assignments produce an `outliers/outliers.md` page and inject `topic: outliers` into the raw file."""
    _emb, raw_dir, _cl = _patch_stores(monkeypatch, tmp_path)
    _write_md(raw_dir / "vidA.md", id="vidA", title="A")
    _write_md(raw_dir / "vidB.md", id="vidB", title="B")
    topics = [
        _topic(-1, "outliers", count=1, description="Videos that did not fit any cluster."),
        _topic(0, "real-topic", count=1),
    ]
    clusters.write_wiki_topics([-1, 0], ["vidA", "vidB"], topics)

    out_md = clusters.WIKI_TOPICS_DIR / "outliers" / "outliers.md"
    assert out_md.exists()
    fence = "---"
    _, fm_text, _body = out_md.read_text(encoding="utf-8").split(fence + "\n", 2)
    fm = yaml.safe_load(fm_text)
    assert fm["label"] == "outliers"
    assert fm["cluster_id"] == -1

    a_fm = yaml.safe_load((raw_dir / "vidA.md").read_text(encoding="utf-8").split(fence + "\n", 2)[1])
    assert a_fm["topic"] == "outliers"
    assert a_fm["cluster_id"] == -1


# @lat: [[clusters#Wiki topics#Tests#Cluster all writes wiki]]
def test_cluster_all_writes_wiki_topics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`cluster_all` produces both the clustering trio and the wiki topic pages + injects raw frontmatter."""
    emb_dir, raw_dir, _cl = _patch_stores(monkeypatch, tmp_path)
    ids = [f"vid{i:02d}" for i in range(6)]
    _seed_embeddings(emb_dir, ids)
    for vid in ids:
        _write_md(raw_dir / f"{vid}.md", id=vid, title=f"Title {vid}", summary=f"summary {vid}")
    monkeypatch.setenv(clusters._MIN_SIZE_ENV, "2")

    topic_model = _StubTopicModel(
        [0, 0, 0, 1, 1, 1],
        {
            0: {"keywords": ["alpha"], "rep_docs": []},
            1: {"keywords": ["gamma"], "rep_docs": []},
        },
    )
    _patch_pipeline(monkeypatch, topic_model, _StubAgent())

    clusters.cluster_all()

    assert (clusters.WIKI_TOPICS_DIR / "alpha-cluster" / "alpha-cluster.md").exists()
    assert (clusters.WIKI_TOPICS_DIR / "gamma-cluster" / "gamma-cluster.md").exists()

    fence = "---"
    fm0 = yaml.safe_load((raw_dir / "vid00.md").read_text(encoding="utf-8").split(fence + "\n", 2)[1])
    assert fm0["topic"] == "alpha-cluster"
    assert fm0["cluster_id"] == 0
    fm5 = yaml.safe_load((raw_dir / "vid05.md").read_text(encoding="utf-8").split(fence + "\n", 2)[1])
    assert fm5["topic"] == "gamma-cluster"
    assert fm5["cluster_id"] == 1


# @lat: [[clusters#Wiki topics#Tests#Missing raw file does not abort]]
def test_write_wiki_topics_tolerates_missing_raw_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An id with no raw file does not abort; sibling video still gets injected and topic page written."""
    _emb, raw_dir, _cl = _patch_stores(monkeypatch, tmp_path)
    _write_md(raw_dir / "vidA.md", id="vidA", title="A")
    # vidGhost intentionally has no markdown file on disk.
    topics = [_topic(0, "demo", count=2)]
    clusters.write_wiki_topics([0, 0], ["vidA", "vidGhost"], topics)

    assert (clusters.WIKI_TOPICS_DIR / "demo" / "demo.md").exists()
    fence = "---"
    fm = yaml.safe_load((raw_dir / "vidA.md").read_text(encoding="utf-8").split(fence + "\n", 2)[1])
    assert fm["topic"] == "demo"
    assert fm["cluster_id"] == 0


# @lat: [[clusters#Tests#Real BERTopic smoke]]
@pytest.mark.slow_clustering
def test_real_bertopic_smoke_on_synthetic_blobs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Real BERTopic + UMAP + HDBSCAN on 200 synthetic blobs yields at least 2 clusters."""
    rng = np.random.default_rng(42)
    blobs = []
    blob_centers = [np.full(384, c, dtype=np.float32) for c in (-2.0, 0.0, 2.0)]
    for center in blob_centers:
        blobs.append(center + 0.1 * rng.standard_normal((70, 384)).astype(np.float32))
    arr = np.vstack(blobs)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    ids = [f"vid{i:03d}" for i in range(arr.shape[0])]

    emb_dir, raw_dir, _cl = _patch_stores(monkeypatch, tmp_path)
    emb_dir.mkdir(parents=True, exist_ok=True)
    np.save(emb_dir / "embeddings.npy", arr.astype(np.float32, copy=False), allow_pickle=False)
    (emb_dir / "ids.json").write_text(json.dumps(ids), encoding="utf-8")
    # Use a real SentenceTransformer name: BERTopic's KeyBERTInspired re-embeds candidate words
    # via the topic_model's embedding_model, so a placeholder like "smoke" would crash.
    (emb_dir / "meta.json").write_text(
        json.dumps(
            {"model": "sentence-transformers/all-MiniLM-L6-v2", "dim": int(arr.shape[1]), "updated_at": "now"},
        ),
        encoding="utf-8",
    )
    for vid in ids:
        _write_md(raw_dir / f"{vid}.md", id=vid, title=vid, summary=f"summary of {vid}")

    monkeypatch.setenv(clusters._MIN_SIZE_ENV, "15")

    def _smoke_agent() -> _StubAgent:
        return _StubAgent()

    monkeypatch.setattr(clusters, "_build_label_agent", _smoke_agent)

    n_clusters = clusters.cluster_all()
    assert n_clusters >= 2
