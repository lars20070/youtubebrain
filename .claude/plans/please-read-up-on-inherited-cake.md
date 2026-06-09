# qmd search layer for the wiki (host-side MCP)

## Context

youtubebrain compiles ~14.7K raw video pages (`Markdown/raw/<id>.md`) into a curated wiki (`Markdown/wiki/`: `topics/`, `creators/`, `syntheses/`, `questions/`, `index.md`). Today the only retrieval is `ripgrep`/`fd` inside the Pi compile sandbox. Goal: add **qmd** (on-device hybrid BM25 + vector + LLM-rerank search) as a **host-side stdio MCP server** so Claude Code can answer "find me X" against the curated wiki pages. Wiki pages were chosen over raw/: their `tldr`/`aliases`/synthesis prose make better retrieval targets, and each page links out to its member raw videos — so a wiki hit surfaces the relevant videos.

## Confirmed decisions

- **Consumer**: host-side stdio server for this Claude Code (mirror existing `lat` server). No Docker/sandbox changes.
- **Corpus**: `Markdown/wiki/**/*.md` only — one collection `youtubebrain-wiki`.
- **Indexing**: user-run wrapper script + docs. (qmd's own CLAUDE.md forbids agents auto-running `collection add`/`embed`/`update` — so Claude never executes indexing; the user runs the script.)

## How qmd works (relevant facts)

- Install: `npm install -g @tobilu/qmd` (Node, not Python/uv).
- Global index DB: `~/.cache/qmd/index.sqlite` (outside repo → nothing to gitignore).
- Build: `qmd collection add <dir> --name <n>` → `qmd update` (re-scan FS) → `qmd embed` (vectors; first run downloads a GGUF embed model — network + minutes).
- CLI verbs: `query` (expansion+rerank, recommended), `search` (BM25), `vsearch` (vector), `get`, `multi-get`, `status`.
- MCP (stdio) = `qmd mcp`; serves all collections from the global DB. MCP tools: `structured_search` (lex/vec/hyde), `get`, `multi_get`, `status`.

## Files to add / change

1. **`index-wiki.sh`** (repo root, parallels `compile-wiki.sh`) — idempotent wrapper:
   - resolve abs path to `Markdown/wiki`; collection name `youtubebrain-wiki`.
   - error if `qmd` missing (print the `npm i -g` hint).
   - add collection if absent (`qmd collection list`), else skip add; then `qmd update`; `qmd embed`; `qmd status`.
   - optional: `qmd context add <wiki> "<one-line description of the collection>"` to aid rerank.
   - carry `# @lat:` refs to the new `lat.md/search.md` sections (shell `# @lat:` already used in `compile-wiki.sh`).

2. **`.vscode/mcp.json`** — add a `qmd` server next to existing `lat`:
   ```json
   "qmd": { "command": "qmd", "args": ["mcp"] }
   ```
   Also note `.mcp.json` (`mcpServers`) as the Claude Code CLI equivalent (optional, mention in docs).

3. **`README.md`** — short "Search the wiki (qmd)" section: install, run `./index-wiki.sh`, reload MCP, example queries. Note ordering: run after `compile-wiki.sh` (or whenever wiki pages change).

4. **`lat.md/search.md`** (new) — document the search layer: overview, indexed corpus (wiki pages, why not raw/), `[[index-wiki.sh]]` build/refresh lifecycle, MCP registration, query verbs vs MCP tools. Link `[[overview]]`, `[[wiki]]`, `[[clusters#Wiki topics]]`. Add a backlink line in `lat.md/overview.md` (and/or `lat.md/wiki.md`) pointing to it. Obey leading-paragraph (≤250 char) rule. Run `lat check`.

## Lifecycle

`compile-wiki.sh` (enrich pages) → `./index-wiki.sh` (refresh search index) → query via Claude Code MCP or `qmd query`. Re-run `index-wiki.sh` whenever wiki pages change; `qmd embed` only re-embeds changed docs.

## Verification (user-run — Claude must not auto-run qmd indexing)

1. `npm install -g @tobilu/qmd` && `qmd --version`.
2. `./index-wiki.sh` → first run pulls the embed model, indexes `wiki/`, embeds; ends with `qmd status` showing `youtubebrain-wiki` with N docs + embeddings.
3. Sanity: `qmd query "anglo saxon migration"` → anglo-saxon topic page; `qmd query "espresso without electricity"` → 9barista page.
4. Reload MCP in Claude Code → `qmd` server connected; call `mcp__qmd__structured_search` and `mcp__qmd__get`.
5. `lat check` passes. No Python changed → `uv run pytest -n auto` unaffected.

## Unresolved questions

- Script name/location: `index-wiki.sh` at root vs `scripts/qmd-index.sh`?
- Collection name `youtubebrain-wiki` ok?
- Add optional `qmd context add` line, or keep script minimal?
- Also write `.mcp.json` (CLI) in addition to `.vscode/mcp.json`?
