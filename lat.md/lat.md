This directory defines the high-level concepts, business logic, and architecture of this project using markdown. It is managed by [lat.md](https://www.npmjs.com/package/lat.md) — a tool that anchors source code to these definitions. Install the `lat` command with `npm i -g lat.md` and run `lat --help`.

- [[overview]] — End-to-end pipeline from Takeout watch history to clustered local wiki, with CLI run order and per-tool output files
- [[config]] — Central pipeline path constants and the single .env loading call site
- [[cache]] — Shared SQLite status-row cache for worker stages
- [[takeout]] — Parse and filter Takeout watch-history records; derive stable video/channel IDs
- [[ingest]] — Ingest YouTube history data from Google Takeout
- [[descriptions]] — Fetch and cache YouTube video descriptions via the Data API v3
- [[transcripts]] — Fetch and cache captions with a resumable SQLite-backed pipeline
- [[summaries]] — Generate cached per-video summaries via a configurable LLM provider
- [[embeddings]] — Compute sentence-transformer embeddings of title + summary/description per raw markdown
- [[clusters]] — UMAP + HDBSCAN + BERTopic over the embedding store, with LLM-named topics
- [[provider]] — Build the pydantic-ai model dispatched by the PROVIDER env var
- [[wiki]] - Compile wiki using the Pi agent and the `fill-` skills.
- [[search]] — Host-side qmd hybrid search over curated wiki pages via MCP.