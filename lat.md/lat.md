This directory defines the high-level concepts, business logic, and architecture of this project using markdown. It is managed by [lat.md](https://www.npmjs.com/package/lat.md) — a tool that anchors source code to these definitions. Install the `lat` command with `npm i -g lat.md` and run `lat --help`.

- [[ingest]] — Ingest YouTube history data from Google Takeout
- [[descriptions]] — Fetch and cache YouTube video descriptions via the Data API v3
- [[transcripts]] — Fetch and cache captions with a resumable SQLite-backed pipeline