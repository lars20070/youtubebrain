## YouTubeBrain

YouTubeBrain generates a markdown knowledge base from your watched YouTube videos.

### Exporting your watch history

The steps below read `Takeout/YouTube and YouTube Music/history/watch-history.json` at the repo root. To produce it:

1. Open [takeout.google.com](https://takeout.google.com) and click **Deselect all**.
2. Select **YouTube and YouTube Music** → **All YouTube data included** → **Deselect all** → enable only **history**.
3. Click **Multiple formats** and set **History** to **JSON**.
4. Submit the export, wait for the email (~20 minutes), and download the ZIP.
5. Unzip it and copy the `Takeout/` folder into the root of this repo. The final path must be `Takeout/YouTube and YouTube Music/history/watch-history.json`.

### Configuration

Two env files, scoped by pipeline stage:

```bash
cp .env.example .env      # steps 1–6 (Python pipeline)
cp .env.pi.example .env.pi  # step 7 (Pi wiki sandbox)
```

Edit `.env` with your YouTube API key, LLM provider, and embedding settings. Edit `.env.pi` with your OpenRouter API key (used only by the Docker sandbox in step 7).

### Run the pipeline

Seven steps in the order below. `ingest` is run twice — once to seed the descriptions cache the summarizer needs, then again to fold transcripts and summaries into the raw markdown the embedder reads. Steps 1–6 are the Python pipeline (see this [overview](lat.md/overview.md) for the full diagram, per-stage prerequisites, and output files); step 7 compiles the wiki (see [wiki](lat.md/wiki.md)).

```bash
uv run ingest
uv run transcripts
uv run summaries
uv run ingest
uv run embed
uv run cluster
./compile-wiki.sh
```

1. `uv run ingest` — fetch descriptions (YouTube Data API), write initial `Markdown/raw/<id>.md` with placeholder Summary / Transcript sections.
2. `uv run transcripts` — long-running, throttled, resumable caption fetch into `Markdown/.cache/transcripts.sqlite`. If rows are marked `blocked` after IP throttling, reset them to `pending` or `error` (or delete) before the next run.
3. `uv run summaries` — long-running LLM summarizer; reads descriptions + transcripts, writes `Markdown/.cache/summaries.sqlite`.
4. `uv run ingest` — second pass; rewrites `Markdown/raw/<id>.md` with the now-cached transcripts and summaries folded in.
5. `uv run embed` — local SentenceTransformer encoding into `Markdown/embeddings/`.
6. `uv run cluster` — BERTopic (UMAP + HDBSCAN) over the embedding store with LLM-named topics; writes `Markdown/clustering/`, `Markdown/wiki/topics/` (wipe-and-rewrite), and `Markdown/wiki/creators/` (preserve-existing stub pages).
7. `./compile-wiki.sh` — runs the [Pi agent](https://pi.dev) in a Docker sandbox to enrich each seeded `Markdown/wiki/topics/` page into a full synthesis (`fill-topic` skill). Requires Docker and `.env.pi` (from `.env.pi.example`). Do not re-run `cluster` after this, or enriched pages are wiped.

