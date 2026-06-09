#!/bin/bash
set -euo pipefail

if [[ ! -f .env.pi ]]; then
	echo "Missing .env.pi — copy .env.pi.example to .env.pi and set OPENROUTER_API_KEY." >&2
	exit 1
fi

# Build the Pi agent image. All container config (security, resource limits,
# mounts, env) lives declaratively in compose.yaml.
# @lat: [[wiki#Orchestration#Image build]]
docker compose build

# Compile wikis for all topics.
# Make sure OPENROUTER_API_KEY is set in the .env.pi file.
# @lat: [[wiki#Orchestration#Batch loop over topics]]
for page in Markdown/wiki/topics/*/*.md; do
	# Skip pages fill-topic has already enriched (it stamps last_updated on completion).
	# @lat: [[wiki#Orchestration#Resumable skip of already-filled pages]]
	if grep -q '^last_updated:' "$page"; then
		echo "Skipping already-filled page $page"
		continue
	fi
	# Map the host path (Markdown/wiki/...) to the in-container mount
	# (/workspace/wiki/..., see compose.yaml). The agent only sees /workspace.
	# @lat: [[wiki#Orchestration#Host-to-container path mapping]]
	page_inside="/workspace/${page#Markdown/}"
	echo "Compiling page $page_inside"
	# @lat: [[wiki#Orchestration#Per-page agent invocation]]
	docker compose run --rm -T pi-sandbox \
		--provider openrouter \
		--model qwen/qwen3.6-27b \
		-xt bash \
		-p "Read $page_inside carefully. Then run the fill-topic skill exactly as defined in AGENTS.md." \
		</dev/null
done
