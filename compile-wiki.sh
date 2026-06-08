#!/bin/bash
set -euo pipefail

# Build the Pi agent image. All container config (security, resource limits,
# mounts, env) lives declaratively in compose.yaml.
docker compose build

# Compile wikis for all topics.
# Make sure OPENROUTER_API_KEY is set in the .env file.
for page in Markdown/wiki/topics/*/*.md; do
	# Map the host path (Markdown/wiki/...) to the in-container mount
	# (/workspace/wiki/..., see compose.yaml). The agent only sees /workspace.
	page_inside="/workspace/${page#Markdown/}"
	echo "Compiling page $page_inside"
	docker compose run --rm -T pi-sandbox \
		--provider openrouter \
		--model qwen/qwen3.6-27b \
		-xt bash \
		-p "Read $page_inside carefully. Then run the fill-topic skill exactly as defined in AGENTS.md."
done
