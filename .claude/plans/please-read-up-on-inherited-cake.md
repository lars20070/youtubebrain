# qmd search layer for the wiki (host-side MCP)

## Context

youtubebrain compiles ~14.7K raw video pages (`Markdown/raw/<id>.md`) into a curated wiki (`Markdown/wiki/`: `topics/`, `creators/`, `syntheses/`, `questions/`, `index.md`). Today the only retrieval is `ripgrep`/`fd` inside the Pi compile sandbox. Goal: add **qmd** (on-device hybrid BM25 + vector + LLM-rerank search) as a **host-side stdio MCP server** so Claude Code can answer "find me X" against the curated wiki pages. Wiki pages were chosen over raw/: their `tldr`/`aliases`/synthesis prose make better retrieval targets, and each page links out to its member raw videos — so a wiki hit surfaces the relevant videos.

## Confirmed decisions

- **Consumer**: host-side stdio server for this Claude Code (mirror existing `lat` server). No Docker/sandbox changes.
- **Corpus**: `Markdown/wiki/**/*.md` only — one collection `youtubebrain-wiki`.
- **Indexing**: user-run wrapper script + docs. (qmd's own CLAUDE.md forbids agents auto-running `collection add`/`embed`/`update` — so Claude never executes indexing; the user runs the script.)
- **Index location**: repo-local, **not** the global `~/.cache/qmd/`. Achieved by pointing `XDG_CACHE_HOME` at a repo dir (`.qmd/`) → index at `.qmd/qmd/index.sqlite`. Gitignored. Both the wrapper script and the `qmd mcp` process must set the same `XDG_CACHE_HOME` so they share one index.

## How qmd works (relevant facts)

- Install: `npm install -g @tobilu/qmd` (Node, not Python/uv).
- Index DB path: default `~/.cache/qmd/index.sqlite`; resolves under `$XDG_CACHE_HOME/qmd/index.sqlite` when `XDG_CACHE_HOME` is set. **No** `--db`/`--database` CLI flag (only the SDK `createStore({ dbPath })` takes an explicit path). `--index <name>` selects a *named* index, not a path. → `XDG_CACHE_HOME` is the only CLI knob for relocating the DB, and it applies uniformly to `update`/`embed`/`query`/`mcp`. (GGUF model cache likely lands under the same dir — fine, gitignored.)
- Build: `qmd collection add <dir> --name <n>` → `qmd update` (re-scan FS) → `qmd embed` (vectors; first run downloads a GGUF embed model — network + minutes).
- CLI verbs: `query` (expansion+rerank, recommended), `search` (BM25), `vsearch` (vector), `get`, `multi-get`, `status`.
- MCP (stdio) = `qmd mcp`; serves all collections from whatever DB its `XDG_CACHE_HOME` resolves to. MCP tools: `structured_search` (lex/vec/hyde), `get`, `multi_get`, `status`.
- **What the LLM sees / "this is about watched YouTube videos"**: NOT the MCP tool descriptions (generic, baked into qmd, not customizable) and NOT mcp.json. It lives in qmd's **context** (`path_contexts` table in the index DB), set via `qmd context add`: per-collection context (`qmd://youtubebrain-wiki`) is returned alongside matching results; global context (`/`) acts as an always-on "system message". qmd README calls this its key feature. → the corpus description is index data built by the script, so the same `XDG_CACHE_HOME` makes `qmd mcp` serve it.

## Files to add / change

1. **`index-wiki.sh`** (repo root, parallels `compile-wiki.sh`) — idempotent wrapper:
   - `export XDG_CACHE_HOME="$(cd "$(dirname "$0")" && pwd)/.qmd"` + `mkdir -p` it — pins the index to repo-local `.qmd/qmd/index.sqlite`. Must match the env in mcp.json.
   - resolve abs path to `Markdown/wiki`; collection name `youtubebrain-wiki`.
   - error if `qmd` missing (print the `npm i -g` hint).
   - add collection if absent (`qmd collection list`), else skip add; then `qmd update`; `qmd embed`; `qmd status`.
   - **required** (this is the LLM-facing "what is this" description): `qmd context add qmd://youtubebrain-wiki "Wiki synthesizing ~14,700 previously watched YouTube videos into per-topic and per-creator pages; each page links to its source videos. Search here to find which watched videos cover a subject."` — re-run is idempotent (overwrites). Optionally also a global `qmd context add / "Personal YouTube watch-history knowledge base."`.
   - carry `# @lat:` refs to the new `lat.md/search.md` sections (shell `# @lat:` already used in `compile-wiki.sh`).

2. **`.vscode/mcp.json`** — add a `qmd` server next to existing `lat`, with `XDG_CACHE_HOME` pointing at the same repo-local dir so it serves the script-built index:
   ```json
   "qmd": {
     "command": "qmd",
     "args": ["mcp"],
     "env": { "XDG_CACHE_HOME": "${workspaceFolder}/.qmd" }
   }
   ```
   `${workspaceFolder}` is supported by VS Code mcp.json. Note `.mcp.json` (`mcpServers`) as the Claude Code CLI equivalent (its var-expansion differs — may need an absolute path); mention in docs.

3. **`.gitignore`** — add `.qmd/` (repo-local qmd index + model cache). Sits alongside existing `.ruff_cache/`, `Markdown/wiki/`, etc.

4. **`README.md`** — short "Search the wiki (qmd)" section: install, run `./index-wiki.sh`, reload MCP, example queries. Note the repo-local `.qmd/` index and ordering: run after `compile-wiki.sh` (or whenever wiki pages change).

5. **`lat.md/search.md`** (new) — document the search layer: overview, indexed corpus (wiki pages, why not raw/), repo-local index via `XDG_CACHE_HOME`, the qmd **context** as the LLM-facing corpus description (watched-YouTube-videos), `[[index-wiki.sh]]` build/refresh lifecycle, MCP registration, query verbs vs MCP tools. Link `[[overview]]`, `[[wiki]]`, `[[clusters#Wiki topics]]`. Add a backlink line in `lat.md/overview.md` (and/or `lat.md/wiki.md`) pointing to it. Obey leading-paragraph (≤250 char) rule. Run `lat check`.

## Lifecycle

`compile-wiki.sh` (enrich pages) → `./index-wiki.sh` (refresh search index) → query via Claude Code MCP or `qmd query`. Re-run `index-wiki.sh` whenever wiki pages change; `qmd embed` only re-embeds changed docs.

## Verification (user-run — Claude must not auto-run qmd indexing)

1. `npm install -g @tobilu/qmd` && `qmd --version`.
2. `./index-wiki.sh` → first run pulls the embed model, indexes `wiki/`, embeds; ends with `qmd status` showing `youtubebrain-wiki` with N docs + embeddings. Confirm `.qmd/qmd/index.sqlite` exists in the repo and `~/.cache/qmd/` was **not** touched.
3. Sanity (with the same env): `XDG_CACHE_HOME=$PWD/.qmd qmd query "anglo saxon migration"` → anglo-saxon topic page; `... "espresso without electricity"` → 9barista page.
4. `qmd context list` shows the `youtubebrain-wiki` context. Reload MCP in Claude Code → `qmd` server connected (reading `.qmd/`); call `mcp__qmd__structured_search` and confirm the watched-YouTube-videos context rides along with results; `mcp__qmd__get`.
5. `git status --ignored` shows `.qmd/` ignored. `lat check` passes. No Python changed → `uv run pytest -n auto` unaffected.

## Unresolved questions

- macOS XDG: confirm qmd honors `XDG_CACHE_HOME` on darwin at impl time (`XDG_CACHE_HOME=/tmp/x qmd status` → DB under /tmp/x). Docs reference XDG + `~/.cache`, so expected; fallback = `HOME` redirect if not.
- Index dir name `.qmd/` at root ok? (db nests at `.qmd/qmd/index.sqlite`.)
- Script name `index-wiki.sh` at root vs `scripts/qmd-index.sh`; collection name `youtubebrain-wiki`.
- Exact wording of the collection context string (above is a draft); also set the global `/` context, or collection-only? Also write `.mcp.json` (CLI) alongside `.vscode/mcp.json`?
