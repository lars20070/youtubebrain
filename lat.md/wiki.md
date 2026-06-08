# Wiki

Compiles the machine-seeded topic pages under `Markdown/wiki/` into rich, interlinked synthesis pages by running the **Pi** coding agent inside a hardened Docker sandbox — one disposable container per page.

This stage is *not* part of the Python pipeline. The six `uv run` tools end at [[overview#Run order]]'s `cluster` step, which only *seeds* minimal pages (see [[clusters#Wiki topics]] and [[clusters#Wiki creators]]). The wiki stage is a separate, language-agnostic layer: a shell orchestrator ([compile-wiki.sh](../compile-wiki.sh)), a Docker image ([Dockerfile.pi](../Dockerfile.pi)), a Compose sandbox ([compose.yaml](../compose.yaml)), an agent schema ([AGENTS.md](../Markdown/AGENTS.md)), and two on-demand skills. The agent is a *compiler of knowledge*: every page it writes is a persistent artifact grounded only in the immutable `raw/` sources.

## Two-stage hand-off

The wiki is produced by two decoupled stages: `uv run cluster` seeds, the Pi agent enriches. Understanding the boundary is essential because the seeder is destructive.

The Python clustering step ([[clusters#Wiki topics]]) writes one minimal page per cluster — frontmatter, an H1, the LLM description, and a flat `## Videos` list — and it **wipes and rewrites** `Markdown/wiki/topics/` on every run ([[clusters#Wiki topics#Wipe-and-rewrite policy]]). The Pi agent then rewrites the body of each seeded page in place into a 400–1000 word synthesis, preserving the `## Videos` index.

Because cluster ids and labels are not stable across runs ([[clusters#Re-cluster policy]]), the seeder and the agent must not interleave: once enrichment has begun, re-running `cluster` discards every enriched topic page. Creator pages are safer — [[clusters#Wiki creators]] is preserve-existing, so hand- or agent-written creator bodies survive a re-cluster.

## Orchestration

[compile-wiki.sh](../compile-wiki.sh) is the host-side driver: it builds the sandbox image once, then enriches every seeded topic page in its own disposable container.

It runs under `set -euo pipefail`, so any failed build or container run aborts the whole batch.

### Image build

The script's first action, `docker compose build`, builds the `pi-sandbox` image from [Dockerfile.pi](../Dockerfile.pi).

All container configuration (security, resource limits, mounts, env) lives declaratively in [compose.yaml](../compose.yaml) rather than in the script, so the build line itself carries no flags.

### Batch loop over topics

The script globs `Markdown/wiki/topics/*/*.md` — every seeded `<slug>/<slug>.md` page from [[clusters#Wiki topics#Page rendering]], including the synthetic `outliers` page — and processes them one at a time.

Only topic pages are driven in batch, and only via the `fill-topic` skill. The `fill-creator` skill exists for creator pages but is **not** wired into this loop; creators are filled on demand (see [[wiki#Skills#fill-creator]]).

### Host-to-container path mapping

Each host path `Markdown/wiki/<...>.md` is rewritten to its in-container location with `page_inside="/workspace/${page#Markdown/}"`: the `Markdown/` prefix is stripped and `/workspace` is prepended.

This is required because the sandbox only ever sees `/workspace` — [[wiki#Sandbox runtime#Mount layout]] mounts the host `Markdown/` subtree there. The agent is handed the in-container path, never the host path.

### Per-page agent invocation

Each page is enriched by `docker compose run --rm -T pi-sandbox <pi-args>`: `--rm` deletes the container on exit, `-T` disables pseudo-TTY allocation, and everything after the service name is appended to the `pi` entrypoint.

The Pi arguments are `--provider openrouter --model qwen/qwen3.6-27b -xt bash -p "Read <page_inside> carefully. Then run the fill-topic skill exactly as defined in AGENTS.md."`:

- `--provider openrouter --model qwen/qwen3.6-27b` selects the cloud model (matching the model pinned in [AGENTS.md](../Markdown/AGENTS.md)). This is the Pi CLI's own model selection — distinct from the project's pydantic-ai [[provider#Model factory]], which the wiki stage never touches.
- `-xt bash` is `--exclude-tools bash`: the agent runs with the **bash tool disabled**, restricted to Pi's read / edit / write tools. It cannot execute arbitrary shell commands; it can only read sources and write wiki pages. (Pi's internal file search still works because `ripgrep`/`fd` are installed in the image — see [[wiki#Sandbox image#Search tooling]].)
- `-p` (`--print`) is one-shot non-interactive mode: process the prompt and exit.
- The prompt is deliberately thin — read the target page, then run `fill-topic`. The agent's real instructions come from auto-discovered [AGENTS.md](../Markdown/AGENTS.md) and the skill files (Pi loads both unless `--no-context-files` / `--no-skills` are passed, which they are not).

The invocation also redirects stdin from `/dev/null` (`</dev/null`). With `-T` (no TTY), Pi treats stdin as a pipe and reads it to support `echo … | pi`, blocking on EOF that the terminal never sends — a silent startup hang before any LLM call. The prompt arrives via `-p`, so `/dev/null` (immediate EOF) lets the agent proceed.

A fresh container per page enforces the schema's "process one page at a time" discipline ([[wiki#Agent schema#Absolute rules]]): each enrichment starts cold, with no carried-over state beyond what is on disk.

## Sandbox image

[Dockerfile.pi](../Dockerfile.pi) builds the `pi-sandbox` image from `node:24.16.0-bookworm-slim`. It installs the Pi agent and the search tools it relies on, and prepares a writable state directory compatible with the read-only, non-root runtime.

### Search tooling

The image installs `bash`, `ca-certificates`, `git`, `ripgrep`, and `fd-find` via `apt-get`, and symlinks `fdfind` to `fd`.

`ripgrep` and `fd` back Pi's built-in file-search/glob capabilities, which is why they are present even though the bash *tool* is disabled at invocation time ([[wiki#Orchestration#Per-page agent invocation]]).

### Pi agent install

`npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.78.1` installs the pinned Pi CLI globally. The version is pinned for reproducibility, and `--ignore-scripts` avoids running untrusted package lifecycle scripts during the build.

### Writable agent-state directory

The image pre-creates `/home/node/.pi/agent` and `chown`s `/home/node` to the non-root `node` user.

This is required so the root-initialised named volume mounted there ([[wiki#Sandbox runtime#Mount layout]]) stays writable when the container runs as `--user 1000` under a read-only root filesystem. `WORKDIR` is `/workspace` and the `ENTRYPOINT` is `pi`.

## Sandbox runtime

[compose.yaml](../compose.yaml) defines the single `pi-sandbox` service and holds all runtime configuration declaratively — env, mounts, security, and resource limits.

It injects the OpenRouter key, mounts the vault, and aggressively confines the agent so the AGENTS.md rules are enforced by the kernel, not just by prompt.

### Pi environment

Scoped env file for the Pi sandbox so only OpenRouter credentials enter the container; pipeline secrets stay on the host.

The Python pipeline (steps 1–6) reads [`.env`](../.env) via `load_dotenv()`. The Pi sandbox uses [`.env.pi`](../.env.pi) — copy from [`.env.pi.example`](../.env.pi.example) — containing only `OPENROUTER_API_KEY` (required). Pi hardcodes the OpenRouter base URL (`https://openrouter.ai/api/v1`); `OPENROUTER_BASE_URL` in `.env` is for the Python pipeline only (step 3 summaries). [`compose.yaml`](../compose.yaml) sets `env_file: .env.pi`; `HOME` is set to `/home/node` so Pi finds its state directory.

### Mount layout

The host `Markdown/` subtree is bind-mounted under `/workspace`, with write access granted only to the layer the agent owns:

- `./Markdown/raw → /workspace/raw` (**ro**) — the immutable per-video sources; read-only at the kernel level, making the AGENTS.md "never modify raw/" rule unbreakable.
- `./Markdown/wiki → /workspace/wiki` (**rw**) — the only writable layer; the agent's topic/creator/synthesis pages plus `index.md` and `log.md`.
- `./Markdown/clustering → /workspace/clustering` (**ro**) — the BERTopic output ([[clusters#Storage layout]]) the agent reads for cluster membership.
- `./Markdown/.pi → /workspace/.pi` (**ro**) — the skill definitions.
- `./Markdown/AGENTS.md → /workspace/AGENTS.md` (**ro**) — the schema, loaded at startup.
- `pi-agent-home → /home/node/.pi/agent` — a named volume persisting Pi's own agent state across runs (the rest of `/home/node` is ephemeral `tmpfs`).

### Security and resource limits

The container is hardened: `network_mode: bridge` (outbound only, for the LLM API), `cap_drop: ALL`, `security_opt: no-new-privileges`, `read_only: true` root filesystem, and non-root `user: "1000:1000"`.

Writable scratch is provided by `tmpfs` mounts at `/tmp`, `/run`, and `/home/node`.

Resource limits cap a runaway agent: `pids_limit: 512`, `mem_limit: 4g`, `cpus: 2`. Together with the read-only `raw/` mount, these make the sandbox defense-in-depth: even a misbehaving model cannot corrupt sources, escalate privileges, or exhaust the host.

## Agent schema

[AGENTS.md](../Markdown/AGENTS.md) is the agent's schema — loaded by Pi at startup from `/workspace/AGENTS.md` and governing every action.

It defines the agent's identity (an autonomous wiki maintainer compiling ~14,700 videos), the layer model, the inviolable rules, the page conventions, and the working discipline.

### Three layers

The vault has three layers with different ownership: `raw/` (immutable per-video sources, read-only), `wiki/` (owned entirely by the agent), and the schema file itself.

`wiki/` holds `topics/`, `creators/`, `syntheses/`, `questions/`, `index.md`, and `log.md`; each `raw/<video_id>.md` is one video the agent may read but never write.

The `raw/` source format and the [[clusters#Storage layout]] clustering output are documented in AGENTS.md as read-only references; `topic` + `cluster_id` frontmatter (injected by [[clusters#Wiki topics#Raw frontmatter contract]]) tell the agent which topic a video belongs to, and `channels[].id` which creator.

### Absolute rules

AGENTS.md hard-codes the constraints the agent must never break, several of which the sandbox also enforces physically.

Chief among them: never modify, delete, or move anything under `raw/`; never invent facts not traceable to specific video IDs (write less rather than pad); never silently overwrite a contradicting claim; never delete a wiki page others link to.

It also mandates upkeep: always keep `index.md` and `log.md` in sync with every page touched, always use `[[wikilinks]]` between wiki pages and relative Markdown links to cite raw videos, and process one page at a time so each result is inspectable.

### Page frontmatter

Every wiki page the agent writes must carry required frontmatter, while **preserving** the machine-written fields the seeder added (`label`, `cluster_id`, `count`, `keywords`, `representative_ids`, `name`, `url`).

The agent adds: `slug` (kebab-case; folder name for topics, channel id for creators), `tldr` (≤200-char load-bearing summary), `aliases` (alternative names, to prevent duplicate pages), `sources` (the `raw/` video IDs backing the claims), `confidence` (`low`/`medium`/`high`), and `last_updated` (`YYYY-MM-DD`).

### Linking

Cross-references between wiki pages use `[[topic-slug]]` or the alias form `[[UC...|Creator Name]]` (creators' canonical ids are unreadable). Citations to a raw video use a relative Markdown link matching the seeded style, never an absolute path.

The relative depth differs by page type: from a topic page `topics/<slug>/<slug>.md` it is `[Title](../../../raw/<video_id>.md)`; from a creator page `creators/<channel_id>.md` it is `[Title](../../raw/<video_id>.md)`.

### Contradiction policy

When videos disagree on facts, predictions, or opinions, the agent must not pick a winner — contradictions are treated as assets, not errors.

It records a short `### Contradictions` block (or a `syntheses/comparison-<slug>.md` page for cross-topic disputes) that states each position and cites the video ID(s) behind it.

## Skills

Page-enrichment procedures live as on-demand skills under `Markdown/.pi/skills/`, each a directory with a `SKILL.md`. Only their descriptions stay in context; Pi loads the full instructions on match or via `/skill:<name>`.

### fill-topic

[fill-topic](../Markdown/.pi/skills/fill-topic/SKILL.md) turns one seeded topic page into a standalone 400–1000 word synthesis. It is the skill the batch loop ([[wiki#Orchestration#Batch loop over topics]]) runs for every topic, invoked as `/skill:fill-topic <slug>` (or a `cluster_id`).

Its steps: read the seeded page's frontmatter and `## Videos` list; read member-video summaries (starting from `representative_ids`, sampling broadly for large clusters); rewrite the body into a synthesis of dominant themes, recurring people/tools/works, tensions/contradictions, and evolution over time, cross-linking related `[[topics]]` and `[[creators]]`; **preserve the `## Videos` list** as the page's source index; update the required frontmatter; append a line to `wiki/log.md`; and update the page's entry in `wiki/index.md`.

### fill-creator

[fill-creator](../Markdown/.pi/skills/fill-creator/SKILL.md) gives a channel a real 150–500 word page from the stub seeded by [[clusters#Wiki creators]], invoked as `/skill:fill-creator <channel_id>`. It is not part of the batch script and is run on demand.

Its steps: open the stub `creators/<channel_id>.md` (preserving its `name`/`id`/`url` frontmatter); find the channel's videos by scanning `raw/` frontmatter for `channels[].id == <channel_id>` and read their summaries; write who the creator is, the subjects/format they cover, which `[[topics]]` they contribute to, and notable videos cited as relative links; add the required frontmatter (with the human-readable name in `aliases`); then append to `log.md` and update `index.md`.

## index.md and log.md

Two vault-level files keep the wiki navigable and auditable, and AGENTS.md requires the agent to update both on every write. The seeder leaves them empty; the agent populates them as it enriches pages.

`wiki/index.md` is a content-oriented catalog grouped by section (Topics, Creators, Series, Syntheses, Questions), one line per page: the `[[slug]]` (or alias), its `tldr`, and a source count. `wiki/log.md` is append-only, newest entries at the bottom, one grep-friendly line per action — `## [YYYY-MM-DD] <skill> | <slug> | <short note>` — and past lines are never edited or deleted.
