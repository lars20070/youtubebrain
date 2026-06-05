"""Cluster the local embedding store with UMAP + HDBSCAN via BERTopic, then label clusters with an LLM."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent

from youtubebrain import embeddings, logger
from youtubebrain.provider import create_model

CLUSTERING_DIR = Path("Markdown/clustering")
ASSIGNMENTS_JSON_PATH = CLUSTERING_DIR / "assignments.json"
TOPICS_JSON_PATH = CLUSTERING_DIR / "topics.json"
META_JSON_PATH = CLUSTERING_DIR / "meta.json"
MODEL_DIR = CLUSTERING_DIR / "bertopic_model"
WIKI_TOPICS_DIR = Path("Markdown/wiki/topics")
WIKI_CREATORS_DIR = Path("Markdown/wiki/creators")

_MIN_SIZE_FLOOR = 10
_MIN_SIZE_ENV = "CLUSTER_MIN_SIZE"
_UMAP_N_COMPONENTS = 5
_UMAP_N_NEIGHBORS = 15
_UMAP_MIN_DIST = 0.0
_UMAP_RANDOM_STATE = 42
_HDBSCAN_MIN_SAMPLES = 5
_MMR_DIVERSITY = 0.3
_REPRESENTATIVE_DOCS = 4
_AGENT_RETRIES = 3
_DEFAULT_LABEL_CONCURRENCY = 4
_LABEL_CONCURRENCY_ENV = "LABEL_CONCURRENCY"
_REP_TEXT_CHAR_BUDGET = 600
_OUTLIER_CLUSTER_ID = -1
_OUTLIER_SLUG = "outliers"
_FRONTMATTER_FENCE = "---"

_LABEL_SYSTEM_PROMPT = """\
You name a cluster of YouTube videos. Given top keywords and up to 4 representative title+summary excerpts, return:
- label: 3-6 words, kebab-case, lowercase ASCII (e.g. "rust-async-runtime")
- description: 3 sentences suitable as a wiki topic page intro, plain prose, no markdown

Return strictly a TopicLabel.\
"""


# @lat: [[clusters#Output schema]]
class TopicLabel(BaseModel):
    """LLM-produced human label and description for a single cluster."""

    label: str
    description: str


# @lat: [[clusters#Output schema]]
class TopicInfo(BaseModel):
    """One row in `topics.json`; serialized alongside the cluster assignments."""

    cluster_id: int
    count: int
    label: str
    description: str
    keywords: list[str]
    representative_ids: list[str]


# @lat: [[clusters#Min-cluster-size heuristic]]
def _resolve_min_cluster_size(n_videos: int) -> int:
    """Resolve `min_cluster_size`: env override beats `max(floor, round(sqrt(n)))`."""
    load_dotenv()
    raw = os.environ.get(_MIN_SIZE_ENV)
    if raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{_MIN_SIZE_ENV} must be int, got {raw!r}") from exc
        if value < 2:
            raise ValueError(f"{_MIN_SIZE_ENV} must be >= 2, got {value}")
        return value
    return max(_MIN_SIZE_FLOOR, round(math.sqrt(n_videos)))


# @lat: [[clusters#Env vars]]
def _label_concurrency() -> int:
    """Resolve `LABEL_CONCURRENCY` (default 4); used to cap parallel LLM label calls."""
    load_dotenv()
    raw = os.environ.get(_LABEL_CONCURRENCY_ENV)
    if not raw:
        return _DEFAULT_LABEL_CONCURRENCY
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{_LABEL_CONCURRENCY_ENV} must be int, got {raw!r}") from exc
    return max(1, value)


# @lat: [[clusters#Pipeline components]]
def _build_umap() -> Any:  # noqa: ANN401
    """Construct a deterministic UMAP reducer (cosine metric, random_state=42)."""
    from umap import UMAP  # noqa: PLC0415

    return UMAP(
        n_components=_UMAP_N_COMPONENTS,
        n_neighbors=_UMAP_N_NEIGHBORS,
        min_dist=_UMAP_MIN_DIST,
        metric="cosine",
        random_state=_UMAP_RANDOM_STATE,
    )


# @lat: [[clusters#Pipeline components]]
def _build_hdbscan(min_cluster_size: int) -> Any:  # noqa: ANN401
    """Construct an HDBSCAN clusterer with prediction_data=True for future partial_fit."""
    from hdbscan import HDBSCAN  # noqa: PLC0415

    return HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=_HDBSCAN_MIN_SAMPLES,
        metric="euclidean",
        cluster_selection_method="eom",
        prediction_data=True,
    )


# @lat: [[clusters#Pipeline components]]
def _build_topic_model(
    umap_model: Any,  # noqa: ANN401
    hdbscan_model: Any,  # noqa: ANN401
    embedding_model_name: str | None,
) -> Any:  # noqa: ANN401
    """Construct a BERTopic with KeyBERTInspired + MMR deterministic representations."""
    from bertopic import BERTopic  # noqa: PLC0415
    from bertopic.representation import KeyBERTInspired, MaximalMarginalRelevance  # noqa: PLC0415

    # KeyBERTInspired re-embeds candidate keywords via the BERTopic instance's `embedding_model`,
    # so passing precomputed document `embeddings=` is not enough — we must also wire the same
    # SentenceTransformer name used for the document store, else fit_transform crashes.
    # BERTopic accepts a dict of chained representations at runtime, though its type stubs only
    # declare the single-model overload.
    representation_model = {"Main": [KeyBERTInspired(), MaximalMarginalRelevance(diversity=_MMR_DIVERSITY)]}
    return BERTopic(
        embedding_model=embedding_model_name,
        umap_model=umap_model,
        hdbscan_model=hdbscan_model,
        representation_model=representation_model,  # type: ignore[arg-type]
        calculate_probabilities=False,
        verbose=True,
    )


# @lat: [[clusters#LLM labeller]]
def _build_label_agent() -> Agent[None, TopicLabel]:
    """Build a pydantic-ai Agent that produces TopicLabel for one cluster."""
    return Agent(
        create_model(),
        output_type=TopicLabel,
        system_prompt=_LABEL_SYSTEM_PROMPT,
        retries=_AGENT_RETRIES,
    )


# @lat: [[clusters#Representative-doc plumbing]]
def _load_texts_by_id(ids: list[str]) -> dict[str, str]:
    """Walk Markdown/raw/ once, return {id: composed_text} for ids that match a raw file with content."""
    import yaml  # noqa: PLC0415

    wanted = set(ids)
    out: dict[str, str] = {}
    # Resolve MARKDOWN_RAW_DIR at call time so test monkeypatches take effect.
    for path in embeddings.iter_raw_files(embeddings.MARKDOWN_RAW_DIR):
        try:
            video_id, title, summary, description = embeddings.parse_raw_markdown(path)
        except (ValueError, yaml.YAMLError) as exc:
            logger.warning(f"Skipping {path}: {exc}")
            continue
        if video_id not in wanted:
            continue
        text = embeddings.compose_text(title, summary, description)
        if text:
            out[video_id] = text
    missing = [vid for vid in ids if vid not in out]
    if missing:
        logger.warning(
            f"No text recovered for {len(missing)} of {len(ids)} ids (e.g. {missing[:3]}); "
            "those clusters will fall back to keywords-only LLM prompts.",
        )
    return out


def _build_user_prompt(keywords: list[str], rep_texts: list[str]) -> str:
    """Format keywords + truncated representative texts as one user message."""
    kw_line = ", ".join(keywords[:10]) if keywords else "(none)"
    if rep_texts:
        numbered = "\n\n".join(f"{i + 1}. {t[:_REP_TEXT_CHAR_BUDGET]}" for i, t in enumerate(rep_texts))
    else:
        numbered = "(none)"
    return f"KEYWORDS: {kw_line}\n\nREPRESENTATIVE VIDEOS:\n{numbered}"


# @lat: [[clusters#LLM labeller]]
def _fallback_label(cluster_id: int, keywords: list[str]) -> TopicLabel:
    """Deterministic placeholder when the LLM call fails for a cluster."""
    first = keywords[0] if keywords else "unknown"
    return TopicLabel(
        label=f"topic-{cluster_id}-{first}",
        description=", ".join(keywords[:5]) or "Unlabelled cluster.",
    )


# @lat: [[clusters#LLM labeller]]
async def _label_clusters(
    payloads: list[tuple[int, list[str], list[str]]],
    agent: Agent[None, TopicLabel],
    concurrency: int,
) -> list[TopicLabel | None]:
    """Run one LLM call per (cluster_id, keywords, rep_texts); cap with asyncio.Semaphore."""
    sem = asyncio.Semaphore(concurrency)

    async def one(cluster_id: int, keywords: list[str], rep_texts: list[str]) -> TopicLabel | None:
        async with sem:
            user_prompt = _build_user_prompt(keywords, rep_texts)
            try:
                result = await agent.run(user_prompt=user_prompt)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"LLM label failed for cluster {cluster_id}: {exc}")
                return None
            return result.output

    return await asyncio.gather(*(one(cid, kws, reps) for cid, kws, reps in payloads))


# @lat: [[clusters#Storage layout]]
def load_existing() -> tuple[list[int], list[TopicInfo], dict[str, Any]]:
    """Load the assignments/topics/meta trio; treat any mismatch as empty + warn."""
    empty: tuple[list[int], list[TopicInfo], dict[str, Any]] = ([], [], {})
    if not ASSIGNMENTS_JSON_PATH.exists() and not TOPICS_JSON_PATH.exists() and not META_JSON_PATH.exists():
        return empty
    try:
        assignments_raw = json.loads(ASSIGNMENTS_JSON_PATH.read_text(encoding="utf-8")) if ASSIGNMENTS_JSON_PATH.exists() else []
        topics_raw = json.loads(TOPICS_JSON_PATH.read_text(encoding="utf-8")) if TOPICS_JSON_PATH.exists() else []
        meta = json.loads(META_JSON_PATH.read_text(encoding="utf-8")) if META_JSON_PATH.exists() else {}
    except (ValueError, OSError) as exc:
        logger.warning(f"Cluster store unreadable ({exc}); treating as empty.")
        return empty
    if not isinstance(assignments_raw, list) or not isinstance(topics_raw, list) or not isinstance(meta, dict):
        logger.warning("Cluster store malformed; treating as empty.")
        return empty
    try:
        assignments = [int(x) for x in assignments_raw]
        topics = [TopicInfo.model_validate(t) for t in topics_raw]
    except (TypeError, ValueError) as exc:
        logger.warning(f"Cluster store schema mismatch ({exc}); treating as empty.")
        return empty
    return assignments, topics, meta


# @lat: [[clusters#Storage layout]]
def save_atomic(
    assignments: list[int],
    topics: list[TopicInfo],
    meta: dict[str, Any],
    topic_model: Any | None,  # noqa: ANN401
) -> None:
    """Write assignments → topics → meta → bertopic_model/ atomically via `*.tmp` + os.replace."""
    CLUSTERING_DIR.mkdir(parents=True, exist_ok=True)

    assignments_tmp = ASSIGNMENTS_JSON_PATH.with_suffix(ASSIGNMENTS_JSON_PATH.suffix + ".tmp")
    topics_tmp = TOPICS_JSON_PATH.with_suffix(TOPICS_JSON_PATH.suffix + ".tmp")
    meta_tmp = META_JSON_PATH.with_suffix(META_JSON_PATH.suffix + ".tmp")
    model_tmp = MODEL_DIR.with_name(MODEL_DIR.name + ".tmp")

    try:
        assignments_tmp.write_text(json.dumps(assignments), encoding="utf-8")
        os.replace(assignments_tmp, ASSIGNMENTS_JSON_PATH)

        topics_payload = [t.model_dump() for t in topics]
        topics_tmp.write_text(json.dumps(topics_payload, indent=2), encoding="utf-8")
        os.replace(topics_tmp, TOPICS_JSON_PATH)

        meta_tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        os.replace(meta_tmp, META_JSON_PATH)

        if topic_model is not None:
            embedding_model_name = meta.get("embedding_model")
            save_embedding_model: str | bool = embedding_model_name if isinstance(embedding_model_name, str) and embedding_model_name else False
            shutil.rmtree(model_tmp, ignore_errors=True)
            topic_model.save(
                str(model_tmp),
                serialization="safetensors",
                save_ctfidf=True,
                save_embedding_model=save_embedding_model,
            )
            shutil.rmtree(MODEL_DIR, ignore_errors=True)
            os.replace(model_tmp, MODEL_DIR)
    finally:
        for tmp in (assignments_tmp, topics_tmp, meta_tmp):
            if tmp.exists():
                tmp.unlink()
        if model_tmp.exists():
            shutil.rmtree(model_tmp, ignore_errors=True)


def _bertopic_version() -> str:
    """Best-effort BERTopic version string for meta.json."""
    try:
        from bertopic import __version__ as v  # noqa: PLC0415
    except ImportError:
        return "unknown"
    return str(v)


# @lat: [[clusters#Re-cluster policy]]
def _check_embedding_model_match(emb_meta: dict[str, object], existing_meta: dict[str, Any]) -> None:
    """Refuse when the existing cluster store was built against a different embedding model."""
    prior = existing_meta.get("embedding_model") if existing_meta else None
    current = emb_meta.get("model") if emb_meta else None
    if prior and current and prior != current:
        raise ValueError(
            f"embedding model changed since last cluster run: existing={prior!r}, current={current!r}. Delete Markdown/clustering/ to rebuild.",
        )


# @lat: [[clusters#Pipeline components]]
def _extract_cluster_payloads(
    topic_model: Any,  # noqa: ANN401
    ids: list[str],
    texts: list[str],
) -> tuple[list[tuple[int, list[str], list[str]]], dict[int, list[str]], dict[int, int]]:
    """Pull keywords + representative_ids + per-cluster counts out of a fitted BERTopic.

    Returns (label_payloads, representative_ids_by_cluster, count_by_cluster).
    label_payloads excludes the outlier cluster `-1` (no LLM call for outliers).
    """
    text_to_id: dict[str, str] = {}
    for vid, text in zip(ids, texts, strict=True):
        if text and text not in text_to_id:
            text_to_id[text] = vid

    info_df = topic_model.get_topic_info()
    cluster_ids = sorted({int(c) for c in info_df["Topic"].tolist()})
    count_lookup = {int(row["Topic"]): int(row["Count"]) for _, row in info_df.iterrows()}

    payloads: list[tuple[int, list[str], list[str]]] = []
    rep_ids_by_cluster: dict[int, list[str]] = {}

    for cid in cluster_ids:
        topic_terms = topic_model.get_topic(cid) or []
        keywords = [w for w, _score in topic_terms][:10]
        if cid == _OUTLIER_CLUSTER_ID:
            rep_ids_by_cluster[cid] = []
            continue
        rep_docs = topic_model.get_representative_docs(cid) or []
        rep_ids: list[str] = []
        rep_texts: list[str] = []
        for doc in rep_docs[:_REPRESENTATIVE_DOCS]:
            vid = text_to_id.get(doc)
            if vid and vid not in rep_ids:
                rep_ids.append(vid)
                rep_texts.append(doc)
        rep_ids_by_cluster[cid] = rep_ids
        payloads.append((cid, keywords, rep_texts))

    return payloads, rep_ids_by_cluster, count_lookup


def _build_topic_info(
    cluster_id: int,
    count: int,
    keywords: list[str],
    representative_ids: list[str],
    label_or_none: TopicLabel | None,
) -> TopicInfo:
    """Combine LLM-or-fallback label with metadata into one TopicInfo row."""
    if cluster_id == _OUTLIER_CLUSTER_ID:
        return TopicInfo(
            cluster_id=cluster_id,
            count=count,
            label="outliers",
            description="Videos that did not fit any cluster.",
            keywords=[],
            representative_ids=[],
        )
    label = label_or_none if label_or_none is not None else _fallback_label(cluster_id, keywords)
    return TopicInfo(
        cluster_id=cluster_id,
        count=count,
        label=label.label,
        description=label.description,
        keywords=keywords,
        representative_ids=representative_ids,
    )


_SLUG_NON_KEBAB_RE = re.compile(r"[^a-z0-9]+")


# @lat: [[clusters#Wiki topics#Slug resolution]]
def _resolve_slugs(topics: list[TopicInfo]) -> dict[int, str]:
    """Map cluster_id → unique kebab slug; suffix `-1`, `-2`, ... on label collisions.

    Sanitises each non-outlier `TopicInfo.label` into filesystem-safe `[a-z0-9-]+` form
    (with `topic-{cluster_id}` as fallback when the result would be empty) before the dedup
    loop, so no raw LLM-or-fallback label ever becomes a path. The outlier cluster (-1) always
    resolves to the literal slug "outliers". Iteration is in cluster_id ascending order so the
    suffix assignment is deterministic.
    """
    slug_by_cluster: dict[int, str] = {}
    used: set[str] = set()
    for topic in sorted(topics, key=lambda t: t.cluster_id):
        if topic.cluster_id == _OUTLIER_CLUSTER_ID:
            slug = _OUTLIER_SLUG
        else:
            base = _SLUG_NON_KEBAB_RE.sub("-", topic.label.lower()).strip("-") or f"topic-{topic.cluster_id}"
            slug = base
            n = 1
            while slug in used:
                slug = f"{base}-{n}"
                n += 1
        used.add(slug)
        slug_by_cluster[topic.cluster_id] = slug
    return slug_by_cluster


# @lat: [[clusters#Wiki topics#Page rendering]]
def _load_titles_by_id(ids: list[str]) -> dict[str, str]:
    """Walk Markdown/raw/ once; return {id: title} for ids that match a raw file."""
    import yaml  # noqa: PLC0415

    wanted = set(ids)
    out: dict[str, str] = {}
    for path in embeddings.iter_raw_files(embeddings.MARKDOWN_RAW_DIR):
        try:
            video_id, title, _summary, _description = embeddings.parse_raw_markdown(path)
        except (ValueError, yaml.YAMLError) as exc:
            logger.warning(f"Skipping {path}: {exc}")
            continue
        if video_id in wanted:
            out[video_id] = title
    missing = [vid for vid in ids if vid not in out]
    if missing:
        logger.warning(
            f"No raw file for {len(missing)} of {len(ids)} ids (e.g. {missing[:3]}); those entries will be listed by id only.",
        )
    return out


# @lat: [[clusters#Wiki topics#Page rendering]]
def _render_topic_page(topic: TopicInfo, slug: str, members: list[tuple[str, str]]) -> str:
    """Render a topic page: frontmatter + heading + description + bulleted member list."""
    import yaml  # noqa: PLC0415

    frontmatter = {
        "label": topic.label,
        "cluster_id": topic.cluster_id,
        "count": topic.count,
        "keywords": list(topic.keywords),
        "representative_ids": list(topic.representative_ids),
    }
    yaml_body = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip("\n")
    if members:
        member_lines = [f"- {title} ([{vid}](../../../raw/{vid}.md))" for vid, title in members]
    else:
        member_lines = ["_(no members)_"]
    body_lines = [
        _FRONTMATTER_FENCE,
        yaml_body,
        _FRONTMATTER_FENCE,
        "",
        f"# {slug}",
        "",
        topic.description.strip() or "_(no description)_",
        "",
        "## Videos",
        "",
        *member_lines,
        "",
    ]
    return "\n".join(body_lines)


# @lat: [[clusters#Wiki topics#Raw frontmatter contract]]
def _inject_topic_into_raw(path: Path, slug: str, cluster_id: int) -> None:
    """Set `topic` + `cluster_id` in the raw markdown frontmatter, exactly once each, idempotently.

    Reads the file, parses the frontmatter dict via `yaml.safe_load` (collapses any pre-existing
    duplicates), overwrites the two keys, and re-dumps with `sort_keys=False` so original key
    order is preserved. The body after the second `---` fence is written back verbatim.
    """
    import yaml  # noqa: PLC0415

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        raise ValueError(f"Missing frontmatter fence in {path}")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == _FRONTMATTER_FENCE)
    except StopIteration as exc:
        raise ValueError(f"Unclosed frontmatter in {path}") from exc
    try:
        fm = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed frontmatter in {path}: {exc}") from exc
    if fm is None:
        fm = {}
    elif not isinstance(fm, dict):
        raise ValueError(f"Malformed frontmatter in {path}: expected mapping but got {type(fm).__name__}")
    fm["topic"] = slug
    fm["cluster_id"] = cluster_id
    yaml_body = yaml.safe_dump(
        fm,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip("\n")
    body_tail = "\n".join(lines[end + 1 :])
    rendered = f"{_FRONTMATTER_FENCE}\n{yaml_body}\n{_FRONTMATTER_FENCE}\n{body_tail}"
    if not rendered.endswith("\n"):
        rendered += "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(rendered, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


# @lat: [[clusters#Wiki topics#Wipe-and-rewrite policy]]
def write_wiki_topics(assignments: list[int], ids: list[str], topics: list[TopicInfo]) -> tuple[int, int]:
    """Wipe & rewrite `Markdown/wiki/topics/<slug>/<slug>.md`; inject `topic`/`cluster_id` into raw files.

    Returns (n_topics_written, n_raw_updated). Missing raw files are skipped with a count log.
    """
    slug_by_cluster = _resolve_slugs(topics)

    shutil.rmtree(WIKI_TOPICS_DIR, ignore_errors=True)
    WIKI_TOPICS_DIR.mkdir(parents=True, exist_ok=True)

    members_by_cluster: dict[int, list[str]] = {}
    for vid, cid in zip(ids, assignments, strict=True):
        members_by_cluster.setdefault(cid, []).append(vid)

    titles_by_id = _load_titles_by_id(ids)

    for topic in topics:
        slug = slug_by_cluster[topic.cluster_id]
        member_ids = members_by_cluster.get(topic.cluster_id, [])
        members = [(vid, titles_by_id.get(vid, vid)) for vid in member_ids]
        page_dir = WIKI_TOPICS_DIR / slug
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / f"{slug}.md").write_text(_render_topic_page(topic, slug, members), encoding="utf-8")

    n_updated = 0
    n_missing = 0
    for vid, cid in zip(ids, assignments, strict=True):
        raw_path = embeddings.MARKDOWN_RAW_DIR / f"{vid}.md"
        if not raw_path.exists():
            n_missing += 1
            continue
        try:
            _inject_topic_into_raw(raw_path, slug_by_cluster[cid], cid)
        except (ValueError, OSError) as exc:
            logger.warning(f"Failed to inject topic into {raw_path}: {exc}")
            continue
        n_updated += 1
    if n_missing:
        logger.warning(f"{n_missing} raw files missing during topic injection (out of {len(ids)} ids).")

    return len(topics), n_updated


# @lat: [[clusters#Wiki creators]]
def _iter_channels_from_raw() -> dict[str, dict[str, str]]:
    """Walk Markdown/raw/ once; return {channel_id: {name, id, url}} deduped by id, first occurrence wins.

    Reads the `channels` list out of each raw frontmatter. Malformed frontmatter or channel entries
    are logged and skipped, never aborting the walk. Channel ids that are non-string, empty, or contain
    path separators / `..` are rejected for filesystem safety.
    """
    import yaml  # noqa: PLC0415

    channels: dict[str, dict[str, str]] = {}
    for path in embeddings.iter_raw_files(embeddings.MARKDOWN_RAW_DIR):
        try:
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()
            if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
                raise ValueError("missing frontmatter fence")
            end = next(i for i in range(1, len(lines)) if lines[i].strip() == _FRONTMATTER_FENCE)
            fm = yaml.safe_load("\n".join(lines[1:end]))
        except (ValueError, StopIteration, yaml.YAMLError, OSError) as exc:
            logger.warning(f"Skipping {path} during creator scan: {exc}")
            continue
        if not isinstance(fm, dict):
            continue
        for entry in fm.get("channels") or []:
            if not isinstance(entry, dict):
                continue
            name, channel_id, url = entry.get("name"), entry.get("id"), entry.get("url")
            if not (isinstance(name, str) and isinstance(channel_id, str) and isinstance(url, str)):
                logger.warning(f"Skipping malformed channel entry in {path}: {entry!r}")
                continue
            if not channel_id or "/" in channel_id or "\\" in channel_id or ".." in channel_id:
                logger.warning(f"Skipping channel with unsafe id in {path}: {channel_id!r}")
                continue
            channels.setdefault(channel_id, {"name": name, "id": channel_id, "url": url})
    return channels


# @lat: [[clusters#Wiki creators]]
def write_wiki_creators() -> tuple[int, int]:
    """Write one stub `Markdown/wiki/creators/<channel_id>.md` per distinct channel; preserve existing files.

    Returns (n_created, n_existing). Each created page is YAML frontmatter `{name, id, url}` with an empty
    body. Existing files are never overwritten, so hand-added body content survives re-runs.
    """
    import yaml  # noqa: PLC0415

    channels = _iter_channels_from_raw()
    WIKI_CREATORS_DIR.mkdir(parents=True, exist_ok=True)

    n_created = 0
    n_existing = 0
    for channel_id, channel in channels.items():
        path = WIKI_CREATORS_DIR / f"{channel_id}.md"
        if path.exists():
            n_existing += 1
            continue
        yaml_body = yaml.safe_dump(
            channel,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ).rstrip("\n")
        page = f"{_FRONTMATTER_FENCE}\n{yaml_body}\n{_FRONTMATTER_FENCE}\n"
        path.write_text(page, encoding="utf-8")
        n_created += 1
    return n_created, n_existing


# @lat: [[clusters#CLI entry]]
def cluster_all() -> int:
    """Load embeddings, fit BERTopic, label clusters via LLM, save atomically. Returns n_clusters excl. outliers."""
    arr, ids, emb_meta = embeddings.load_existing()
    if len(ids) == 0:
        raise ValueError("no embeddings to cluster — run `uv run embed`")

    _existing_assignments, _existing_topics, existing_meta = load_existing()
    _check_embedding_model_match(emb_meta, existing_meta)

    min_size = _resolve_min_cluster_size(len(ids))
    if len(ids) < 2 * min_size:
        raise ValueError(
            f"too few embeddings ({len(ids)}) for min_cluster_size={min_size}; set {_MIN_SIZE_ENV} lower (>= 2) or wait for more videos.",
        )

    texts_by_id = _load_texts_by_id(ids)
    texts = [texts_by_id.get(vid, "") for vid in ids]

    umap_model = _build_umap()
    hdbscan_model = _build_hdbscan(min_size)
    embedding_model_name = emb_meta.get("model")
    topic_model = _build_topic_model(
        umap_model,
        hdbscan_model,
        embedding_model_name if isinstance(embedding_model_name, str) else None,
    )

    logger.info(f"Fitting BERTopic on {len(ids)} embeddings (min_cluster_size={min_size}).")
    topic_assignments_raw, _probs = topic_model.fit_transform(documents=texts, embeddings=arr)
    assignments = [int(c) for c in topic_assignments_raw]
    if len(assignments) != len(ids):
        raise ValueError(f"BERTopic returned {len(assignments)} assignments for {len(ids)} ids")

    payloads, rep_ids_by_cluster, count_lookup = _extract_cluster_payloads(topic_model, ids, texts)

    agent = _build_label_agent()
    concurrency = _label_concurrency()
    logger.info(f"Labelling {len(payloads)} clusters with concurrency={concurrency}.")
    labels = asyncio.run(_label_clusters(payloads, agent, concurrency))
    label_by_cluster = {payload[0]: label for payload, label in zip(payloads, labels, strict=True)}

    topics: list[TopicInfo] = []
    for cid in sorted(count_lookup):
        keywords_terms = topic_model.get_topic(cid) or []
        keywords = [w for w, _score in keywords_terms][:10]
        topics.append(
            _build_topic_info(
                cluster_id=cid,
                count=count_lookup[cid],
                keywords=keywords,
                representative_ids=rep_ids_by_cluster.get(cid, []),
                label_or_none=label_by_cluster.get(cid),
            ),
        )

    n_clusters = sum(1 for t in topics if t.cluster_id != _OUTLIER_CLUSTER_ID)
    n_outliers = next((t.count for t in topics if t.cluster_id == _OUTLIER_CLUSTER_ID), 0)

    meta: dict[str, Any] = {
        "embedding_model": emb_meta.get("model"),
        "embedding_dim": emb_meta.get("dim"),
        "n_videos": len(ids),
        "n_clusters": n_clusters,
        "n_outliers": n_outliers,
        "min_cluster_size": min_size,
        "umap_random_state": _UMAP_RANDOM_STATE,
        "llm_provider": os.environ.get("PROVIDER", "ollama"),
        "llm_model": os.environ.get("MODEL"),
        "label_concurrency": concurrency,
        "bertopic_version": _bertopic_version(),
        "updated_at": datetime.now(UTC).isoformat(),
    }

    save_atomic(assignments, topics, meta, topic_model)
    logger.info(
        f"Cluster run complete: n_clusters={n_clusters}, n_outliers={n_outliers}, min_cluster_size={min_size}, llm_model={meta['llm_model']!r}.",
    )
    n_topics_written, n_raw_updated = write_wiki_topics(assignments, ids, topics)
    logger.info(
        f"Wiki topics written: {n_topics_written}, raw frontmatter updated: {n_raw_updated}.",
    )
    n_creators_created, n_creators_existing = write_wiki_creators()
    logger.info(
        f"Wiki creators written: {n_creators_created} new, {n_creators_existing} already present.",
    )
    return n_clusters


# @lat: [[clusters#CLI entry]]
def main() -> None:
    """CLI entry for `uv run cluster`: fit + label + persist."""
    logger.info("Starting clustering worker.")
    n = cluster_all()
    logger.info(f"Clustering finished: {n} clusters (excl. outliers).")


if __name__ == "__main__":
    main()
