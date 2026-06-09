# Search

Host-side hybrid search over curated wiki pages via [qmd](https://github.com/tobi/qmd), exposed to Claude Code as a stdio MCP server alongside the `lat` MCP server.

The Python pipeline and Pi wiki compiler produce persistent markdown artifacts; this layer makes those pages queryable at chat time without changing the Docker sandbox. Raw per-video pages under `Markdown/raw/` are intentionally **not** indexed — wiki syntheses carry richer `tldr`/`aliases` prose and link out to member videos, so a wiki hit surfaces the relevant sources.

## Indexed corpus

One qmd collection, `youtubebrain-wiki`, rooted at `Markdown/wiki/` with a glob mask that includes only semantic page trees:

- `topics/`
- `creators/`
- `syntheses/`
- `questions/`

`index.md` (catalog) and `log.md` (changelog) are excluded — they are operational metadata, not retrieval targets, and would pollute hybrid search with catalog noise.

## Repo-local index

qmd stores its SQLite index under `$XDG_CACHE_HOME/qmd/index.sqlite`. This repo pins the cache to `.qmd/` at the project root (gitignored), not the global `~/.cache/qmd/`.

Both [index-wiki.sh](../index-wiki.sh) and the MCP server set `XDG_CACHE_HOME` to `${REPO_ROOT}/.qmd`. qmd has no CLI `--db` flag; `XDG_CACHE_HOME` is the supported relocation knob.

## Collection context

qmd's **context** strings (stored in the index DB, set via `qmd context add`) ride along with search hits so the LLM knows what corpus it is searching. This is **not** configurable via MCP tool descriptions or `mcp.json`.

[index-wiki.sh](../index-wiki.sh) registers collection-scoped context only (`qmd://youtubebrain-wiki`). There is no global `/` context — that would leak YouTube-specific framing onto any future collections added to the same index. The wording is durable (no hardcoded video counts that drift as the library grows).

## Orchestration

[index-wiki.sh](../index-wiki.sh) is the user-run wrapper (qmd's own agent guidance forbids auto-running `collection add` / `update` / `embed`). It mirrors [compile-wiki.sh](../compile-wiki.sh) at the repo root.

### Repo-local index

Sets `XDG_CACHE_HOME` to `${REPO_ROOT}/.qmd` and creates the directory before any qmd command. The same variable must be set in [`.vscode/mcp.json`](../.vscode/mcp.json) so the MCP server reads the script-built index.

### Preflight checks

Fails fast when the wiki has not been compiled: missing `Markdown/wiki/index.md`, or zero files under `Markdown/wiki/topics/*/*.md`. Also requires `qmd` on `PATH` at version **2.1.0+** (the version this repo is tested against).

### Collection registration

Idempotent: `qmd collection add` runs only when `youtubebrain-wiki` is absent from `qmd collection list`. The collection uses mask `{topics,creators,syntheses,questions}/**/*.md`.

### Index refresh

Runs `qmd update` (re-scan filesystem) once, then loops `qmd embed` until `qmd status` reports zero pending documents; the first embed run also downloads a GGUF model.

`qmd embed` caps each invocation at a hardcoded **30-minute session** — qmd wraps the run in an `LLMSession` with `maxDuration = 30 * 60 * 1000` ms (`store.js`: `generateEmbeddings`), not overridable by any flag or env var. On a large corpus a single run stops early with `⚠ Session expired` and leaves documents pending. Because each run is idempotent and resumable (only documents still missing vectors are embedded), the script re-runs `qmd embed` until the `Pending:` count reaches zero. It detects completion by parsing `qmd status` — the `Pending: N need embedding` line is printed only while work remains and is omitted at zero.

Two guards bound the loop: `MAX_EMBED_PASSES` caps the number of passes (each at most ~30 min), and a pass that makes no progress (unchanged pending count — e.g. a document that errors on every attempt) stops the loop instead of spinning forever.

### Collection context

Re-applies `qmd context add qmd://youtubebrain-wiki "<text>"` every run (idempotent overwrite). Ends with `qmd status` and `qmd context list`.

## MCP registration

[`.vscode/mcp.json`](../.vscode/mcp.json) registers a `qmd` stdio server next to `lat`:

```json
"qmd": {
  "command": "qmd",
  "args": ["mcp"],
  "env": { "XDG_CACHE_HOME": "${workspaceFolder}/.qmd" }
}
```

VS Code / Cursor expand `${workspaceFolder}`. For Claude Code CLI, copy the block into a local `.mcp.json` (gitignored) using an **absolute** path to `.qmd` — CLI config does not reliably expand workspace variables.

MCP tools: `structured_search` (lex/vec/hyde), `get`, `multi_get`, `status`. CLI equivalents: `qmd query` (recommended), `qmd search` (BM25), `qmd vsearch` (vector).

## Lifecycle

[[wiki]] enrichment (`./compile-wiki.sh`) is step 7; indexing with `./index-wiki.sh` is the optional step 8. Re-run whenever wiki pages change. Query via Claude Code MCP or `XDG_CACHE_HOME=$PWD/.qmd qmd query "…"`.

## Verification

User-run checks (agents must not auto-run indexing):

1. `npm install -g @tobilu/qmd@2.1.0` and confirm `qmd --version`.
2. `./index-wiki.sh` — ends with `qmd status` showing `youtubebrain-wiki` with doc + embedding counts; `.qmd/qmd/index.sqlite` exists.
3. Deterministic retrieval (same env): `XDG_CACHE_HOME=$PWD/.qmd qmd search -c youtubebrain-wiki -n 3 anglo-saxon --files` includes a path under `topics/anglo-saxon-migration-britain/`.
4. `XDG_CACHE_HOME=$PWD/.qmd qmd context list` shows `qmd://youtubebrain-wiki`. Reload MCP → `qmd` server connected.
5. `git status --ignored` shows `.qmd/` ignored; `lat check` passes.
