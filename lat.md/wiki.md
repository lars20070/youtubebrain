# Wiki

The seventh, agent-driven stage: after [[clusters]] seeds `Markdown/wiki/topics/` and `Markdown/wiki/creators/`, an autonomous coding agent ("Pi") compiles those stubs into rich, interlinked wiki pages using the `fill-topic` and `fill-creator` skills.

Unlike stages 1–6, this stage is not a Python CLI tool. It is a sandboxed LLM agent run via `compile-wiki.sh`, governed entirely by the schema in `Markdown/AGENTS.md`. The agent is a *compiler* of knowledge: every page it writes is a persistent artefact that compounds across sessions, never a query-time retrieval.

## Sandbox harness

`compile-wiki.sh` builds `Dockerfile.pi` (a `node` base with the `@earendil-works/pi-coding-agent` CLI) and runs the agent under strict container isolation.

Isolation flags: `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--read-only` rootfs, non-root `--user 1000:1000`, plus `--pids-limit` / `--memory` / `--cpus` resource caps.

Mounts enforce least privilege over the vault: `Markdown/raw` and `Markdown/.pi` are bind-mounted read-only, `Markdown/AGENTS.md` is read-only, and only `Markdown/wiki` is writable. A named volume (`pi-agent-home`) persists the agent's own state across runs.

## Agent schema (AGENTS.md)

`Markdown/AGENTS.md` is the agent's loaded-at-startup schema. It defines the three-layer model and the absolute rules that keep the compilation grounded and reversible.

The three layers are:

- **`raw/` — immutable sources.** One `raw/<video_id>.md` per video, written by [[ingest]]. The agent only ever reads these and spot-checks its own claims against them.
- **`wiki/` — the agent's output.** It owns `topics/`, `creators/`, `syntheses/`, `questions/`, `index.md`, and `log.md` entirely.
- **The schema** — `AGENTS.md` itself; if a convention no longer fits the data, the agent surfaces it to the human rather than silently diverging.

Key invariants: never modify `raw/`; never invent facts not traceable to specific video IDs; never silently overwrite a contradicting claim (record both per the Contradiction Policy); keep `index.md` and `log.md` in sync with every page change.

## Seeded inputs

The agent does not cluster — it enriches what [[clusters]] already wrote. The clustering pipeline is a one-time **seeder**, not a co-editor.

- `clustering/{topics,assignments,meta}.json` give cluster membership and machine labels (read-only reference).
- `wiki/topics/<slug>/<slug>.md` are seeded per cluster by [[clusters#Wiki topics]] with a label, description, and flat `## Videos` list. Because [[clusters#Wiki topics#Wipe-and-rewrite policy]] wipes that directory on every run, the agent must not re-run `cluster` after it begins enriching.
- `wiki/creators/<channel_id>.md` are preserve-existing stubs from [[clusters#Wiki creators]].

## Skills

Page-enrichment procedures live as on-demand skills under `Markdown/.pi/skills/`, each a directory with a `SKILL.md`. Only their descriptions stay in context; full instructions load when invoked.

### fill-topic

`fill-topic` (`Markdown/.pi/skills/fill-topic/SKILL.md`) enriches one seeded topic page into a standalone 400–1000 word synthesis of everything its videos cover. Invoked as `/skill:fill-topic <slug>` (or a `cluster_id`).

It reads member-video summaries (starting from `representative_ids`, sampling broadly for large clusters), rewrites the body into themes/claims/contradictions, preserves the `## Videos` source index, updates the required frontmatter, and appends to `log.md` / `index.md`.

### fill-creator

`fill-creator` (`Markdown/.pi/skills/fill-creator/SKILL.md`) turns a channel stub into a real 150–500 word page. Invoked as `/skill:fill-creator <channel_id>`.

It finds the channel's videos by scanning `raw/` frontmatter for `channels[].id == <channel_id>`, reads their summaries, and writes who the creator is, what they cover, and which `[[topics]]` they contribute to.

## Page conventions

Every wiki page the agent writes carries required frontmatter and preserves the machine-written fields already present from the seed (`label`, `cluster_id`, `keywords`, …).

Required fields: `slug`, a ≤200-char `tldr`, `aliases` (to prevent duplicate pages), `sources` (the `raw/` video IDs backing the claims), a `confidence` of `low | medium | high`, and `last_updated`.

Cross-references between wiki pages use `[[wikilinks]]` (alias form `[[UC...|Creator Name]]` for creators, whose ids are unreadable); citations of raw videos use relative Markdown links matching the seeded depth (e.g. `[Title](../../../raw/<video_id>.md)` from a topic page). `index.md` is a content catalogue grouped by section; `log.md` is an append-only, grep-friendly action log.
