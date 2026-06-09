#!/bin/bash
set -euo pipefail

# @lat: [[search#Orchestration#Repo-local index]]
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
export XDG_CACHE_HOME="${REPO_ROOT}/.qmd"
mkdir -p "$XDG_CACHE_HOME"

QMD_MIN_VERSION="2.1.0"
COLLECTION_NAME="youtubebrain-wiki"
WIKI_DIR="${REPO_ROOT}/Markdown/wiki"
# Semantic wiki pages only — exclude catalog (index.md) and changelog (log.md).
WIKI_MASK="{topics,creators,syntheses,questions}/**/*.md"
CONTEXT_TEXT="Wiki synthesizing previously watched YouTube videos into per-topic and per-creator pages; each page links to its source videos. Search here to find which watched videos cover a subject."

version_ge() {
	printf '%s\n%s' "$2" "$1" | sort -C -V
}

if ! command -v qmd >/dev/null 2>&1; then
	echo "qmd not found. Install the tested version: npm install -g @tobilu/qmd@${QMD_MIN_VERSION}" >&2
	exit 1
fi

QMD_VERSION="$(qmd --version 2>/dev/null | awk '{print $2}')"
if [[ -z "${QMD_VERSION}" ]] || ! version_ge "${QMD_VERSION}" "${QMD_MIN_VERSION}"; then
	echo "qmd ${QMD_MIN_VERSION}+ required (found: ${QMD_VERSION:-unknown}). Run: npm install -g @tobilu/qmd@${QMD_MIN_VERSION}" >&2
	exit 1
fi

# @lat: [[search#Orchestration#Preflight checks]]
if [[ ! -f "${WIKI_DIR}/index.md" ]]; then
	echo "Missing ${WIKI_DIR}/index.md — run the pipeline through ./compile-wiki.sh first." >&2
	exit 1
fi

shopt -s nullglob
topic_pages=("${WIKI_DIR}"/topics/*/*.md)
shopt -u nullglob
if ((${#topic_pages[@]} == 0)); then
	echo "No topic pages under ${WIKI_DIR}/topics/ — run ./compile-wiki.sh to enrich seeded wiki pages first." >&2
	exit 1
fi

# @lat: [[search#Orchestration#Collection registration]]
if ! qmd collection list 2>/dev/null | grep -q "^${COLLECTION_NAME} "; then
	qmd collection add "${WIKI_DIR}" --name "${COLLECTION_NAME}" --mask "${WIKI_MASK}"
fi

# @lat: [[search#Orchestration#Index refresh]]
qmd update

# qmd embed caps each invocation at a hardcoded 30-minute "session": qmd wraps the run
# in an LLMSession with maxDuration = 30*60*1000 ms (qmd store.js: generateEmbeddings),
# which is NOT configurable via any flag or env var. On a large corpus a single run stops
# early with "Session expired" and leaves documents pending. Each run is idempotent and
# resumable — only documents still missing vectors are embedded — so we loop until
# 'qmd status' reports nothing pending.
#
# Guards against an unbounded loop:
#   - MAX_EMBED_PASSES caps the number of passes (each pass is at most ~30 min).
#   - A pass that makes no progress (pending count unchanged — e.g. a document that errors
#     on every attempt) stops the loop instead of spinning forever.
pending_count() {
	# qmd prints "Pending:  N need embedding" only while documents still need vectors and
	# omits the line entirely once everything is embedded, so a missing line means 0.
	# grep -oE strips the surrounding text/colour codes so we read the integer directly.
	local n
	n="$(qmd status 2>/dev/null | grep -oE 'Pending:[[:space:]]*[0-9]+' | grep -oE '[0-9]+' | head -1 || true)"
	printf '%s\n' "${n:-0}"
}

MAX_EMBED_PASSES=20
prev_pending=-1
for ((pass = 1; pass <= MAX_EMBED_PASSES; pass++)); do
	pending="$(pending_count)"
	if ((pending == 0)); then
		echo "Embeddings complete - 0 documents pending."
		break
	fi
	if ((pending == prev_pending)); then
		echo "Warning: no progress - ${pending} document(s) still pending after a full pass; stopping." >&2
		echo "  Some documents may be failing repeatedly; inspect with: XDG_CACHE_HOME=\"\$PWD/.qmd\" qmd status" >&2
		break
	fi
	echo "Embedding pass ${pass}/${MAX_EMBED_PASSES}: ${pending} document(s) pending..."
	prev_pending="${pending}"
	qmd embed
done
if ((pass > MAX_EMBED_PASSES)); then
	echo "Warning: reached MAX_EMBED_PASSES=${MAX_EMBED_PASSES} with documents still pending - re-run ./index-wiki.sh to finish." >&2
fi

# @lat: [[search#Orchestration#Collection context]]
qmd context add "qmd://${COLLECTION_NAME}" "${CONTEXT_TEXT}"

qmd status
qmd context list
