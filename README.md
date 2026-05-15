## YouTubeBrain

YouTubeBrain generates a markdown knowledge base from your watched YouTube videos.

### Exporting your watch history

The steps below read `Takeout/YouTube and YouTube Music/history/watch-history.json` at the repo root. To produce it:

1. Open [takeout.google.com](https://takeout.google.com) and click **Deselect all**.
2. Select **YouTube and YouTube Music** → **All YouTube data included** → **Deselect all** → enable only **history**.
3. Click **Multiple formats** and set **History** to **JSON**.
4. Submit the export, wait for the email (~20 minutes), and download the ZIP.
5. Unzip it and copy the `Takeout/` folder into the root of this repo. The final path must be `Takeout/YouTube and YouTube Music/history/watch-history.json`.

### Fetch transcripts and ingest

- `uv run transcripts` — long-running transcript fetch (throttled, resumable) into that SQLite DB. If the fetcher marks rows `blocked` after IP throttling, set those rows back to `pending` or `error` in SQLite (or delete them) before the next `uv run transcripts` so they are retried.
- `uv run ingest` — fetches video descriptions (YouTube Data API) and writes `Markdown/raw/<video_id>.md`, including any transcripts already in `Markdown/.cache/transcripts.sqlite`.

