#!/bin/bash
set -euo pipefail

# Build the Pi agent image. All container config (security, resource limits,
# mounts, env) lives declaratively in compose.yaml.
docker compose build

# Run the container with some security and resource limits.
# Make sure OPENROUTER_API_KEY is set in the .env file.
docker compose run --rm -it pi-sandbox \
	--provider openrouter \
	--model qwen/qwen3.6-27b \
	-xt bash \
	-p "Read /workspace/wiki/topics/gravel-vs-road-bikes/gravel-vs-road-bikes.md Run the fill-topic skill exactly as defined in AGENTS.md."
