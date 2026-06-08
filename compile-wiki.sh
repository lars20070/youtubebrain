#!/bin/bash
set -euo pipefail

# Build the Docker image for the Pi agent.
docker build -t pi-sandbox -f Dockerfile.pi .

# export OPENROUTER_API_KEY="sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxx"

# Run the container with some security and resource limits.
docker run --rm -it \
	--network=bridge \
	--cap-drop=ALL \
	--security-opt=no-new-privileges \
	--read-only \
	--tmpfs /tmp --tmpfs /run --tmpfs /home/node \
	--pids-limit=512 --memory=4g --cpus=2 \
	--user 1000:1000 \
	--env HOME=/home/node \
	--env OPENROUTER_API_KEY \
	-v "$PWD/Markdown/raw:/workspace/raw:ro" \
	-v "$PWD/Markdown/wiki:/workspace/wiki:rw" \
	-v "$PWD/Markdown/.pi:/workspace/.pi:ro" \
	-v "$PWD/Markdown/AGENTS.md:/workspace/AGENTS.md:ro" \
	-v pi-agent-home:/home/node/.pi/agent \
	pi-sandbox \
	--provider openrouter \
	--model qwen/qwen3.6-27b \
	pi --help
