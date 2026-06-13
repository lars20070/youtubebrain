# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YouTubeBrain turns a Google Takeout export of your YouTube watch history into a clustered, LLM-labelled markdown knowledge base ("wiki") that any agent can search via MCP. The pipeline fetches video descriptions (YouTube Data API), captions, and LLM summaries into local caches, compiles one markdown file per watched video, embeds them with a local SentenceTransformer, clusters them with BERTopic, and seeds wiki topic pages that are enriched by an agent in a Docker sandbox.

The run order and per-stage inputs/outputs are documented in `README.md` and `lat.md/overview.md` — read those first; do not guess the workflow.

## Development Commands

### Environment Setup

```bash
# Install dependencies (uv, not pip/poetry)
uv sync

# Create environment files from templates
cp .env.example .env        # Python pipeline (YouTube API key, LLM provider, embeddings)
cp .env.pi.example .env.pi  # Pi wiki sandbox (OpenRouter key, used by compile-wiki.sh)
```

Local LLM via Ollama is the default provider for summaries and cluster labels (`PROVIDER`/`MODEL` in `.env`); cloud providers (OpenAI, OpenRouter, Together, DeepInfra, LM Studio) are supported too.

### Running the Pipeline

The CLI entry points are defined in `[project.scripts]` in `pyproject.toml` and run as `uv run <command>`. See `README.md` for the full ordered workflow, including the shell steps `./compile-wiki.sh` (wiki enrichment) and `./index-wiki.sh` (qmd search index).

### Testing

```bash
# Run all tests (addopts skips: paid, ollama, slow_embedding, slow_clustering)
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_transcripts.py

# Run specific test
uv run pytest tests/test_transcripts.py::test_load_transcripts_none_for_non_ok

# Run tests in parallel
uv run pytest -n auto

# Run tests with coverage report
uv run pytest --cov=src/youtubebrain --cov-report=term-missing
```

### Code Quality

```bash
# Format code
uv run ruff format .

# Check and fix linting issues (ALWAYS run with --fix)
uv run ruff check --fix .

# Type checking (ALWAYS run after code changes)
uv run pyright .
```

### Before Committing

Run these checks:

```bash
# 1. Format code
uv run ruff format .

# 2. Check and fix linting issues
uv run ruff check --fix .

# 3. Type checking
uv run pyright .

# 4. Run tests
uv run pytest -n auto

# 5. Validate docs (wiki links + code refs)
lat check
```

## Architecture

A linear pipeline of small CLI tools in `src/youtubebrain/`, each reading and writing files under `Markdown/` (gitignored).

Do not duplicate architecture details here. The full architecture — module responsibilities, the per-stage run order, design decisions, and test specs — lives in the `lat.md/` knowledge base. Start at `lat.md/overview.md` (run order, prerequisites, and workflow diagram), then read the per-module file for the area you are working on (e.g. `lat.md/markdown.md`, `lat.md/clusters.md`). Use `lat search` / `lat locate` to find sections, and keep `lat.md/` in sync with any code change.

## MCP Servers

This project uses Model Context Protocol (MCP) servers to extend AI capabilities. These are automatically invoked when relevant.

### Context7 Documentation Server

**When to use:**
- Looking up library documentation (e.g., "How do I use pydantic-ai streaming?")
- Checking API references for dependencies (httpx, BERTopic, sentence-transformers, ...)
- Finding code examples from official docs
- Verifying correct usage of third-party packages

### GitHub Repository Server

**When to use:**
- Checking open/closed issues in this repository
- Reviewing pull requests and their status
- Reading issue comments and discussions
- Understanding project history and decisions

### Best Practices

- **Be specific:** "Check issue #15" is better than "check issues"
- **Context first:** Read codebase before checking issues
- **Combine sources:** Use Context7 for "how to use X" and GitHub for "what's our approach to X"

## Coding Standards

See the rules files for detailed coding standards:

- `.claude/rules/python.md` - Python coding standards, type hints, async patterns
- `.claude/rules/python-testing.md` - Testing conventions, markers, coverage requirements
