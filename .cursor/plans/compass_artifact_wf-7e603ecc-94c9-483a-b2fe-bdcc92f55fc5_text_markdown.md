## Bulk-Fetching YouTube Transcripts (10,000+ videos) from a Home IP: A Practical Research Report (May 2026)

**Short answer up front.** As of mid‑2026, the only reliable *free* path for pulling 10,000+ YouTube transcripts from a single home internet connection is:

1. **Use a residential (home) IP**, not a cloud VM — YouTube blanket‑blocks AWS/GCP/Azure/DO ranges for transcript endpoints.
2. **Use `youtube-transcript-api` ≥ 1.1.0 (currently 1.2.4)** as the primary fetcher. The maintainer rewrote it in March 2025 to hit the Innertube `/youtubei/v1/player` endpoint instead of scraping `/watch`, which fixed the wave of mass blocks from early 2025.
3. **Throttle to roughly 1 request every 3–8 seconds with jitter**, plus exponential backoff on 429 / `RequestBlocked` / `IpBlocked`.
4. **Fall back to `yt-dlp --write-auto-subs --write-subs --skip-download`** (with `--sleep-subtitles`, `--sleep-requests`, a mobile client, and optionally a local **bgutil-ytdlp-pot-provider**) for videos that the primary path rejects (PO‑token‑gated auto‑translated tracks, age‑restricted videos, etc.).
5. **Persist progress in SQLite or JSONL** so the run is resumable across nights.

At ~5 s per video, 10,000 videos take roughly **14 hours of wall time** — so plan for a single overnight run with a margin, or split across two nights with a checkpoint database. Expect a real‑world success rate of 85–95% (the remainder being legitimately unavailable: no captions, private, deleted, members‑only, age‑restricted without cookies).

The rest of this report goes deep on each piece of that recommendation.

## 1. The Library Landscape in 2025–2026

### youtube-transcript-api (jdepoix) — the primary tool

This is by far the most popular Python library for the task (≈7.5k GitHub stars, 769 forks). Its current state as of May 2026:

- **Latest version: 1.2.4** (PyPI). Major versions of note:
  - **v1.0.0 (2025)** — Replaced the old static methods (`get_transcript`, `list_transcripts`) with an instance‑based `YouTubeTranscriptApi().fetch(video_id)` / `.list(video_id)` API. The old static methods were removed in a subsequent breaking release; using a stale pre‑1.0 tutorial will silently bypass any proxy or session config you set.
  - **v1.1.0 (March 25, 2025)** — *Critical fix.* Refactored caption‑track discovery from scraping the `/watch` HTML to calling YouTube's internal **Innertube** API (`/youtubei/v1/player`). This fixed the mass breakage that hit users in early 2025 when YouTube changed the watch‑page JS. A new `PoTokenRequired` exception was added.
  - **v1.1.x → 1.2.x** — Added retry logic that triggers IP rotation when used with Webshare, more robust JS parsing, and an `__all__` export. Cookie auth has been "temporarily disabled" since 1.1.0 because the maintainer hasn't yet ported it to the Innertube flow (the code in `_api.py` literally has a comment saying so).
- **How it works under the hood** (per `_transcripts.py`):
  1. GET the `/watch?v=…` HTML to extract `INNERTUBE_API_KEY` and other context tokens.
  2. POST to `https://www.youtube.com/youtubei/v1/player` with a fixed `INNERTUBE_CONTEXT` and the video ID. The response includes `captions.playerCaptionsTracklistRenderer.captionTracks[].baseUrl`.
  3. GET that `baseUrl` (a `timedtext` URL) to retrieve the actual XML transcript.
  So **each video = 3 HTTP requests** to youtube.com. The watch‑HTML fetch is the bandwidth‑heavy one and the maintainer has confirmed it cannot be skipped because the tokens are per‑request.
- **Known issues in 2025–2026**:
  - On cloud‑provider IPs (AWS / GCP / Azure / DigitalOcean / Hetzner / OVH), virtually 100% of requests now return `RequestBlocked` / `IpBlocked`. This is *not* rate‑limit, it's IP‑reputation blocking — the very first request fails.
  - On residential IPs blocking still happens but only after sustained traffic. Reported anecdata in GitHub issue #66 and others puts the threshold at roughly **200–300 consecutive fast requests** before a temporary block. The TooManyRequests / `RequestBlocked` state typically clears in hours, sometimes minutes, sometimes a day.
  - The maintainer's recommended fix is exclusively rotating residential proxies via the built‑in `WebshareProxyConfig` (paid). There is no free, automatic anti‑block mechanism baked in.
- **Cookie support**: Disabled in the current Innertube‑based code path. There is an open PR (#565 by PhilBrk8) to add yt-dlp‑style `cookies_from_browser='chrome'` support for age‑restricted videos but it had not been merged at the time of writing. If you need cookies today you must use yt-dlp instead.

**Verdict:** youtube-transcript-api 1.2.x is the right *primary* fetcher for the task because (a) it's the lowest‑bandwidth option (raw XML, no HTML player extraction beyond the watch page), (b) it works without a PO token for the standard `timedtext` endpoints, and (c) it is the most mature library specifically for transcripts. But you cannot rely on its built‑in defenses — you must add throttling, retry, and a fallback yourself.

### yt-dlp — the heavyweight fallback

yt-dlp (latest stable in 2025‑09 ≈ `2025.09.23`; nightly releases roughly weekly) is the reference implementation for everything YouTube‑related. For subtitles only:

```
yt-dlp --skip-download --write-auto-subs --write-subs \
       --sub-langs "en.*" --sub-format "json3/vtt/best" \
       --sleep-subtitles 3 --sleep-requests 1.5 \
       --extractor-args "youtube:player_client=tv,web_safari" \
       -o "%(id)s.%(ext)s" -a video_ids.txt
```

Strengths:
- Actively maintained, gets fixes within days of YouTube changes.
- Robust client selection — when one player client breaks you can switch (`player_client=tv`, `web_safari`, `mweb`, `android_vr` are commonly cited as working in 2025).
- Native cookie support (`--cookies cookies.txt` or `--cookies-from-browser firefox`).
- Built‑in throttling: `--sleep-requests`, `--sleep-subtitles`, `--sleep-interval`, `--max-sleep-interval`, `--limit-rate`.
- Handles age‑restricted, premieres, members‑only, livestream captions, etc.
- PO‑token framework with pluggable providers (see below).

Weaknesses for *bulk transcript‑only* use:
- About **5–10× the bandwidth and CPU** of youtube-transcript-api because it does full player extraction, n‑sig decryption, m3u8 manifest parsing, format sorting, etc., even with `--skip-download`. On a 600‑video channel one developer reported ~40 minutes "mostly waiting on YouTube's subtitle endpoints, not yt-dlp itself."
- Known issue (#13075, #14452, May 2025+): some `timedtext` URLs now require a PO token (`pot=…&potc=1` parameters); auto‑translated tracks have been hit hardest, and removing `--sub-langs` filters that pull auto‑translated languages avoids it.
- Known issue (#13831): `HTTP Error 429: Too Many Requests` on auto‑translated subtitle endpoints is now common even at modest rates; using `--write-auto-subs` for an *exact* `en` (rather than `en.*` glob that pulls translated variants) substantially reduces 429s.
- `--sleep-interval` only fires on video download, not metadata extraction — the relevant flags for our use case are **`--sleep-requests`** (between every HTTP request) and **`--sleep-subtitles`** (between subtitle downloads).

### pytube / pytubefix

- **pytube** is essentially abandoned for serious use; it breaks every few months when YouTube changes the player JS.
- **pytubefix** is the actively maintained community fork (currently 10.6.x). It exposes `yt.captions[…].xml_captions` and `.generate_srt_captions()`, and importantly added **automatic PoToken generation via Node.js** (`use_po_token=True` or `YouTube(url, 'WEB')`) using bundled `nodejs-wheel-binaries`. So a fresh pytubefix install can produce its own PO token without Docker, which is uniquely useful for free home use.
- Downsides: requires Node.js at runtime, slower than youtube-transcript-api (it fetches the full watch page and parses streams even when you only want captions), and PoToken generation still doesn't get you past raw IP bans — only past the "Sign in to confirm you're not a bot" gate.

It is reasonable to add pytubefix as a *third* fallback after youtube-transcript-api and yt-dlp, mainly for videos where yt-dlp keeps failing on PO‑token‑gated captions.

### Direct Innertube approaches

The Innertube path is what youtube-transcript-api 1.1+ uses internally. Several other projects ride the same approach:
- **LuanRT/YouTube.js** — Excellent JS Innertube client; widely used in the JS ecosystem (npm `youtube-transcript-plus`, `@danielxceron/youtube-transcript` 1.2.6 use it as a fallback when HTML scraping fails). Not a Python library, but the technique is documented in detail and easy to port.
- **trldvix/youtube-transcript-api** — Java port, also Innertube‑based, and supports playlist/channel bulk fetch natively.
- **`youtube_transcript_api` (Dart)** on pub.dev — Dart port with Innertube integration.

The Innertube approach in JavaScript (often referenced in 2025 tutorials) is essentially:
1. `GET https://www.youtube.com/watch?v=ID` to scrape `INNERTUBE_API_KEY`.
2. `POST https://www.youtube.com/youtubei/v1/player?key=…` with `{"context":{"client":{"clientName":"ANDROID","clientVersion":"20.10.38"}},"videoId":"ID"}` — note the **ANDROID client identity** historically reduced bot challenges (it has been getting tightened in 2025; `IOS` and `TV` clients are now often cited as more reliable).
3. Parse `captions.playerCaptionsTracklistRenderer.captionTracks[].baseUrl`.
4. GET that URL (XML/`fmt=json3`).

Rolling your own Innertube client is a viable last‑resort fallback if both youtube-transcript-api and yt-dlp fail, because you can experiment with different `clientName` values (`ANDROID`, `IOS`, `TVHTML5`, `MWEB`, `WEB_EMBEDDED_PLAYER`) which trigger different anti‑bot pathways. In practice you rarely need to write this yourself.

### Other notable options

- **Hosted/free‑tier APIs** (Supadata, transcriptapi.com, youtube‑transcript.io, ScrapeCreators, Apify, SerpApi). All offer 100–250 free credits per month or per signup; none are remotely sufficient for 10,000 videos unless you stack many accounts (which violates their TOS). Useful only as a tiny last‑resort fallback (~50 videos/day across free tiers). Be aware many of these are simply proxied calls to youtube-transcript-api with residential proxies in front.
- **`@danielxceron/youtube-transcript`** (Node.js, npm 1.2.6) — Notable for documenting an automatic HTML‑scraping → Innertube fallback pattern; same idea you should adopt in your own Python pipeline.
- **Browser-automation scrapers** (Selenium/Playwright clicking the "Show transcript" button) — Reliable but **slow (≈15–30 s/video)** and trivially detected as a headless browser unless you use stealth plugins. Not recommended for 10,000 videos but it's the absolute last resort for videos where every API path fails.
- **`youtube-local`** (user234683) — Browser‑based local YouTube viewer with built‑in Tor integration. Its issue tracker (#134, #121) documents that **YouTube blocks Tor exit nodes essentially completely** for the desktop/web client; android‑user‑agent requests sometimes still got through circa 2022 but the more recent threads say "all Tor requests now blocked." Treat Tor as **not viable** for sustained bulk fetching in 2026.

## 2. How YouTube Detects Bot Traffic on Transcript Endpoints

There is no public documentation of YouTube's bot‑detection rules, but the yt-dlp and youtube-transcript-api issue trackers, combined with the official yt-dlp **PO Token Guide** wiki, give us a reliable picture.

### The four signals that get you blocked

1. **IP reputation.** This is the dominant signal in 2025–2026. Two sub‑signals:
   - **Cloud provider ASN.** Every cloud range (AWS, GCP, Azure, DigitalOcean, OVH, Hetzner, Linode, Vultr, Oracle, etc.) is *pre‑blocked*. The first request from a fresh EC2 instance now returns `RequestBlocked` immediately, no warm‑up. youtube-transcript-api's own README and dozens of GitHub issues confirm this.
   - **Volume from a single residential IP.** Once you cross some unpublished threshold (anecdotally 100–500 requests inside a short window, faster from a single TCP session), the IP enters a "captcha required" state. The library reports it as `RequestBlocked` with the message "YouTube is receiving too many requests from this IP and now requires solving a captcha to continue."

2. **Missing or invalid PO Token (Proof of Origin).**  PO Token is YouTube's anti‑bot attestation. The yt-dlp wiki defines it as a token "generated by either BotGuard (Web), DroidGuard (Android), or iOSGuard (iOS)" that proves the request came from a genuine player on a genuine client device. Each token has a *content binding* (either to a session/visitor ID or to a video ID). Tokens cannot cross client families — a Web PO token will not work for the Android client.
   PO tokens are required in three contexts ("PO Token contexts"):
   - **GVS** — Google Video Server (video stream URLs)
   - **Player** — Innertube `/player` requests (format URLs)
   - **Subs** — `timedtext` subtitle URLs (this is the one that matters for us)
   
   In late 2024 / 2025 YouTube progressively rolled out PO‑token enforcement for `timedtext`. Auto‑translated subtitles have been the first to require it (see yt-dlp issue #13075 and #13831). Native captions in the original language usually do **not** require a PO token as of mid‑2026, which is why youtube-transcript-api still works on most videos from a residential IP without one. But when YouTube tightens enforcement (which can happen overnight), you'll start seeing the `PoTokenRequired` exception that the library added in 1.1.0 specifically to surface this.

3. **Client fingerprint and headers.** YouTube inspects:
   - User‑Agent (a default Python `requests` UA is an instant red flag).
   - The `client.clientName` / `clientVersion` field inside the Innertube context JSON; these are version‑pinned and YouTube increments the minimum acceptable version every few weeks. yt-dlp ships fresh versions in each release.
   - TLS / JA3 fingerprint — yt-dlp now ships with `curl_cffi` to mimic real‑browser TLS handshakes; this matters when raw `requests` from a flagged IP fails but a `curl_cffi`-backed call succeeds.
   - HTTP/2 vs HTTP/1.1 — youtube‑local#134 notes Tor Browser worked but Python requests through Tor didn't, and the only obvious difference was HTTP/2.

4. **Cookie / session state.**
   - No cookies at all is fine for most public videos, but a *consent* cookie is sometimes required in the EU.
   - A real user cookie (logged‑in session) raises the rate limit significantly **but** YouTube will eventually **ban the account itself** if you saturate it, per youtube-transcript-api's own warning. Use a throwaway account if you do this, and treat the account as expendable.

### What "blocked" looks like in practice

- `RequestBlocked` / `IpBlocked` exception (youtube-transcript-api).
- `HTTP Error 429: Too Many Requests` (yt-dlp on `timedtext`).
- `ERROR: [youtube] VIDEO_ID: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies for the authentication.` (yt-dlp).
- An empty response body on the `timedtext` GET (PO token now required but not supplied).
- A 302 redirect to `https://consent.youtube.com/` or `https://www.google.com/sorry/` (common on Tor / VPN exits).

The block is **per‑IP and time‑bound**, not permanent. From residential IPs the block typically lifts in 15 minutes to a few hours; from cloud IPs the block tends to be persistent because the entire ASN is flagged.

### What does NOT meaningfully trigger blocks

- Distinct video IDs (it's volume, not the targets).
- Time of day (no evidence of TOD‑based shaping).
- Mixing Shorts and regular videos (same endpoints).
- Single‑user moderate browsing in parallel from the same household (a few extra requests/hour is invisible).

## 3. Free Anti‑Blocking Strategies — What Actually Works

Within the user's constraints (free only, single home IP, overnight), here is the realistic assessment of each anti‑blocking technique.

### 3.1 Throttling and pacing — the single most important free defense

This is what actually works. Concrete recommendations:

- **Inter‑request delay: 3–8 seconds with jitter.** A reasonable default is `random.uniform(3, 7)` between videos. At 5 s average, you complete 10,000 videos in ~14 hours.
- **Exponential backoff on block.** When `RequestBlocked` / `IpBlocked` / 429 fires, sleep 5 minutes → 15 minutes → 45 minutes → 2 hours → give up for the night. Critically, **flush any in‑memory rate‑limit state** when this happens; do not just retry immediately with a slightly longer delay.
- **Soft daily cap.** Around 7,000–10,000 transcript fetches per 24 hours from a single residential IP is plausible. Beyond that, blocks become frequent enough that throughput inverts (you spend more time backing off than fetching).
- **Avoid bursts**, even small ones. Five fast requests in a row are more dangerous than 20 evenly‑spaced requests.

### 3.2 Cookie‑based authentication

- youtube-transcript-api: cookie support is **disabled** in the current Innertube path (see `_api.py`'s `# Cookie auth has been temporarily disabled` comment). A PR is open (#565) but not merged.
- yt-dlp: full support via `--cookies cookies.txt` (Netscape format) or `--cookies-from-browser firefox`. Logged‑in cookies raise the soft rate limit substantially — anecdotally 2–5×. **But** the maintainer's explicit warning applies: with sustained bulk abuse the account itself can be banned. Use a throwaway Google account, expect to burn it, and never use cookies from your daily‑driver account.
- A useful middle ground is **anonymous consent cookies** (just satisfying the EU consent banner). youtube-transcript-api handles this automatically via `_create_consent_cookie`.

### 3.3 PO Token generation (free, local)

For the small minority of videos that require a PO token for `timedtext`, you can generate tokens locally for free:

- **bgutil-ytdlp-pot-provider** (Brainicism, TypeScript, current 1.3.x) — the reference free PO token provider. Two deployment modes:
  - **HTTP server (recommended)**: `docker run --name bgutil-provider -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider`. yt-dlp auto‑detects it on `127.0.0.1:4416` via the bundled plugin. Caches tokens internally (default 6h TTL).
  - **Script mode**: Node.js script invoked per request.
- **bgutil-ytdlp-pot-provider-rs** (jim60105, Rust port) — faster, single binary, no Docker required.
- **pytubefix's built‑in node.js generator** — easiest free path if you only need one token at a time, no Docker, no separate service. Triggered by `YouTube(url, use_po_token=True)`.

All of these use LuanRT's BotGuard‑interfacing library and produce a real PO token by running the same JavaScript challenge a browser would run. **They do *not* magically bypass IP blocks** — the yt-dlp wiki explicitly states a PO token only makes traffic *look more legitimate* on an already‑flagged IP. Combined with a clean residential IP, they meaningfully reduce the "Sign in to confirm you're not a bot" gate.

For the user's use case (transcripts only, residential IP), **you probably don't need PO tokens at all** for the bulk of fetches. Treat the bgutil provider as something you spin up *only when* `PoTokenRequired` starts appearing.

### 3.4 Tor / free VPN rotation

- **Tor: not viable.** youtube-local issue #134 (2022, never fixed) and many subsequent reports confirm that YouTube has aggressively blocked Tor exit nodes. Most exits return 429 / consent redirect / `google.com/sorry` immediately. Tor Browser sometimes works because of HTTP/2 differences, but Python `requests` over Tor essentially does not. Even if it worked, Tor's bandwidth (~1–5 Mbit/s per circuit) and request latency make 10,000 transcript fetches impractical.
- **Free VPNs (ProtonVPN free, Windscribe free, etc.):** All popular free VPN gateway IPs are *already* flagged by YouTube because everyone else doing this has used them. ProtonVPN's free tier servers in particular are well‑known. You may get 50–500 requests before each gateway IP is blocked, after which you must rotate gateways manually. Free VPNs typically rotate IPs only when you reconnect, and most cap you at a few servers.
- **Phone tethering / cellular:** Underrated free option. Your phone's CGNAT‑pooled cellular IP is shared with many real users, has good reputation, and **toggling airplane mode** typically gives you a fresh IP from the carrier's pool. This is genuinely useful as a manual reset when your wired home IP gets blocked. Watch your data cap — 10,000 transcripts is roughly 2–5 GB of traffic counting the watch‑HTML fetches.
- **Free proxy lists:** Effectively dead for YouTube. Public proxy lists (FreeProxyList, PubProxy, etc.) consist almost entirely of either honeypots or IPs already blacklisted by every major site. Success rate against YouTube is <5%.

### 3.5 Free residential alternatives

The only genuinely *free* residential routing is **your own home IP** plus, optionally, **your phone's cellular IP**. There are no legitimately free rotating‑residential‑proxy services; "free trials" from Webshare, Bright Data, SmartProxy, Oxylabs typically give 100–1,000 MB of bandwidth which evaporates in dozens of transcripts and triggers credit‑card requirements. The other end of the spectrum — peer‑to‑peer "free residential" networks like Hola — sells *your* bandwidth out the back door and you should not install them.

If you have a **friend or family member elsewhere** willing to run a small SOCKS5 proxy (`ssh -D 1080 friend@their-home`) or a WireGuard endpoint, you have a free second residential IP. The ClawHub "YouTube Transcript" skill is essentially this pattern — a WireGuard tunnel to a residential endpoint to route youtube-transcript-api traffic.

### 3.6 Session rotation / UA rotation

- **User‑Agent rotation:** Marginal benefit but very cheap. Pick 3–5 modern desktop and mobile UA strings and rotate every N requests. Don't rotate every request — that itself is a fingerprint.
- **Innertube client rotation:** More impactful. When the WEB client starts being challenged, switching to TV (`TVHTML5`), MWEB, or IOS clients in the Innertube context can buy hours of additional throughput. yt-dlp does this internally via `--extractor-args "youtube:player_client=tv,web_safari,mweb"`.
- **TCP session rotation:** Closing and reopening the `requests.Session` (and the underlying TCP connection) every 100–500 requests can help; long‑lived keepalive connections appear more "scraper‑shaped" than browser‑shaped traffic.

### 3.7 Splitting across multiple devices on a home network

This is one of the most useful free tactics that people overlook. From the perspective of YouTube's bot detection, **all devices behind your residential NAT share one public IPv4 address**. So splitting work across two laptops does *not* double your rate budget — you'd just hit the IP block faster. **However:**
- IPv6 is a different story. If your ISP gives you a /56 or /64 IPv6 block (most residential ISPs do today), each device can have a *distinct* public IPv6 address. YouTube's `timedtext`, `youtubei`, and `www.youtube.com` endpoints all serve over IPv6 (`AAAA` records). In practice rate limits seem to be tracked per‑/64 in many cases, but per‑/128 has been reported intermittently. It is worth testing whether `curl -6` from two different devices on your LAN with different IPv6 addresses gets separately rate‑limited; if so, you can parallelize work across 2–4 devices for a meaningful throughput boost.
- Multiple devices on different physical networks (your home Wi‑Fi + your phone's cellular tethering + a friend's network) genuinely give you N times the budget and are the most reliable parallelization.

### 3.8 Mobile vs desktop endpoints

The `m.youtube.com` host and the mobile Innertube clients (`MWEB`, `ANDROID`, `IOS`) sometimes have looser anti‑bot enforcement on `timedtext`, although this has been tightening. yt-dlp's `--extractor-args "youtube:player_client=mweb"` is a one‑liner to try when the default WEB client starts being challenged. The android/iOS clients additionally avoid some JS challenge requirements but each requires its own client version pinning.

### 3.9 What does NOT help

- Switching DNS resolvers.
- Spoofing `X-Forwarded-For` (YouTube ignores it).
- Adding more `Accept` headers.
- Calling `/watch` with a referer.
- "Stealthy" Selenium plugins — they help against Cloudflare and DataDome, not against YouTube's volume‑based heuristics.

## 4. Practical Implementation Patterns

### 4.1 Recommended request rate from a single home IP

Based on (a) yt-dlp issue threads where users report 429s after ~100 fast subtitle downloads, (b) youtube-transcript-api GitHub issue #66 reporting blocks at ~250 requests in a tight loop, and (c) blog posts on bulk transcript collection (`ahmetky.dev/blog/post-02`, Bright Data scraping guides) recommending residential rotation for any scale:

| Rate (req/min) | Behavior from a home residential IP |
|---|---|
| 30+ (≤2 s avg) | Block within 5–15 minutes, almost guaranteed |
| 10–15 (4–6 s avg) | **Recommended baseline.** Mostly stable for hours; occasional transient blocks |
| 6–10 (6–10 s avg) | Very safe; almost no blocks reported anecdotally |
| <6 (>10 s avg) | Wasteful for an overnight job — you won't finish 10,000 |

The sweet spot for a 10,000‑video overnight job is **roughly 12 requests/min average → 5 s mean delay**, with `random.uniform(3, 7)` jitter. Add a longer "breather" pause every 500 videos (e.g. 60–120 s) to reset any short‑window counter.

Note that each youtube-transcript-api `fetch()` is internally **3 HTTP requests** (watch HTML + Innertube player + timedtext), so 12 fetches/min ≈ 36 HTTP requests/min — still well within what a residential IP can do invisibly.

### 4.2 Detecting that you've been blocked

| Signal | What it means | Reaction |
|---|---|---|
| `youtube_transcript_api._errors.RequestBlocked` | IP currently flagged | Stop, long backoff (15+ min) |
| `youtube_transcript_api._errors.IpBlocked` | Same, more persistent | Stop for several hours |
| `youtube_transcript_api._errors.TooManyRequests` | Captcha required | Stop, long backoff |
| `youtube_transcript_api._errors.YouTubeRequestFailed` / `CouldNotRetrieveTranscript` (generic) | Could be transient | One retry, then skip |
| `PoTokenRequired` (added in v1.1.0) | Specific track needs a PO token | Skip this language, try another, or fall back to yt-dlp + bgutil |
| `TranscriptsDisabled` | Video legitimately has no captions | Skip permanently, mark as `no_captions` |
| `NoTranscriptFound` (in requested language) | Captions exist but not in your language | Try `.translate('en').fetch()` or list available |
| `VideoUnavailable` | Deleted, private, region‑locked, members‑only | Skip permanently, mark as `unavailable` |
| `AgeRestricted` | Need auth | Skip, queue for yt-dlp + cookies fallback |
| HTTP 429 (yt-dlp) | Rate limited | Stop, exponential backoff |
| HTTP 403 with empty body | Likely PO token required | Switch player_client or use bgutil |
| 302 to `consent.youtube.com` or `google.com/sorry` | IP fully flagged | Stop for the night |

A robust loop logs both the exception type and the HTTP status so you can post‑hoc tell legitimate "no transcript" failures from blocking failures.

### 4.3 Resumability and state management

For 10,000+ videos, you **must** make the script resumable. SQLite is the right tool:

```python
# schema
CREATE TABLE transcripts (
    video_id      TEXT PRIMARY KEY,
    status        TEXT NOT NULL,    -- 'pending','ok','no_captions','unavailable',
                                    -- 'age_restricted','blocked','error'
    language      TEXT,
    is_generated  INTEGER,
    transcript    TEXT,             -- raw text or JSON
    error_message TEXT,
    fetched_at    TIMESTAMP,
    attempts      INTEGER DEFAULT 0,
    last_attempt  TIMESTAMP
);
CREATE INDEX idx_status ON transcripts(status);
```

Logic:
1. On startup, INSERT OR IGNORE all video IDs with `status='pending'`.
2. The main loop selects `WHERE status IN ('pending','error','blocked') AND attempts < 5 ORDER BY attempts ASC, RANDOM()`.
3. After each video, UPDATE the row with the new status.
4. Commit every N rows (e.g. every 10) to balance durability and write cost.

A JSONL "append‑only log" is fine too if you don't need re‑queries, but SQLite makes "show me everything still pending" trivial. Either way, **write the transcript content to disk immediately** — don't buffer in memory.

### 4.4 Concurrent vs sequential

The instinct is "use `ThreadPoolExecutor(max_workers=30)`" as in the ahmetky.dev blog. **Don't.** From a single home IP that just trades wall‑clock time for blocked time — YouTube counts concurrent connections from the same IP. The blog's pattern only works with a proxy pool.

The right amount of concurrency from one IP is **1 worker for fetches**, optionally with a tiny amount of pipelining (fetch the next video's HTML while parsing the current transcript). If you have IPv6‑per‑device or a friend's residential proxy, scale up to **1 worker per distinct external IP**.

### 4.5 Handling each failure mode

- **No captions** (`TranscriptsDisabled`, `NoTranscriptFound` and no translation works): mark `no_captions`, never retry.
- **Blocked** (`RequestBlocked`, `IpBlocked`, `TooManyRequests`, 429): mark `blocked`, retry with backoff; if 3 consecutive videos block, stop the run entirely and resume tomorrow.
- **Deleted / private / age‑restricted / members‑only** (`VideoUnavailable`, `AgeRestricted`): mark `unavailable`, never retry on the same path; optionally queue for the yt-dlp+cookies fallback.
- **PO token required** (`PoTokenRequired`): queue for yt-dlp+bgutil fallback.
- **Network error / DNS / timeout:** retry up to 3 times with short backoff; then mark `error`.

### 4.6 Caching

Trivially handled by SQLite — never re‑fetch a row whose status is `ok`, `no_captions`, or `unavailable`. For idempotency across runs, key strictly on `video_id`.

### 4.7 Realistic throughput

Assume:
- 5 s average per video on the happy path.
- 10% of videos take 30 s extra (translation fallback, retries).
- Two block events per night each costing 30 minutes of backoff.

→ Effective throughput ≈ **600–700 videos/hour** from one residential IP at the recommended rate. For 10,000 videos that is **~14–17 hours of wall time**, i.e. one long overnight run starting in the evening and finishing by mid‑morning, or two shorter nights. If you parallelize across two distinct IPs (e.g. home Wi‑Fi + phone tether) you can roughly halve that.

## 5. Specific Recent Developments (2024–2026)

### What happened in 2024–2025

There were two distinct waves of breakage:

1. **Late 2024 — IP / cloud‑provider blocking went global.** Up through mid‑2024, youtube-transcript-api worked fine on Heroku, Vercel, AWS Lambda, etc. Sometime in Q3–Q4 2024 YouTube started rejecting requests from those ranges outright. This is the wave that produced the wall of GitHub issues like #303, #511, #335 and the Medium / Hugging Face Forum posts complaining that "my code worked locally but breaks on deploy." Nothing changed in the library; YouTube changed the IP policy. Workaround: residential proxies (Webshare) or run from home.

2. **Early 2025 — the watch‑page HTML parser broke.** YouTube refactored the JS variable structure on the `/watch` page so the regex used by youtube-transcript-api (and many others) stopped finding the caption track JSON. This caused mass failures even on residential IPs.

### The fix — Innertube migration in v1.1.0 (March 25, 2025)

The maintainer (jdepoix) shipped v1.1.0 which refactored caption track discovery to use the Innertube `/youtubei/v1/player` API instead of regex‑scraping the watch HTML. Key changes:

- New code path: GET watch HTML to extract `INNERTUBE_API_KEY` → POST to `/youtubei/v1/player` → read `captions.playerCaptionsTracklistRenderer.captionTracks` → GET each `baseUrl`.
- New `PoTokenRequired` exception, raised when the returned `timedtext` URL appears to require a PO token (so users get a *clear* error rather than silent empty data, and so the maintainer gets fast feedback when YouTube starts enforcing PO tokens for more tracks).
- Cookie authentication was *disabled* in this release because the old cookie logic was tied to the HTML‑scraping path and didn't translate cleanly to Innertube. As of 1.2.4 it is still disabled (the `_api.py` constructor has the explicit comment).
- v1.1.x and v1.2.x added retries for Webshare proxies (auto‑rotate on `RequestBlocked`), the more robust JS parser, mypy `__all__`, and so on.

### Is there a working free approach in 2026?

**Yes, with caveats.** The Innertube path in youtube-transcript-api 1.1+ is essentially the same path the JS Innertube clients (LuanRT/YouTube.js) and yt-dlp use, and it currently works on a residential IP for the *vast majority* of public videos without any PO token. The caveats:

- It is **rate‑sensitive on the home IP** in a way it wasn't two years ago. The block threshold is lower now.
- A growing fraction of `timedtext` tracks (especially auto‑translated ones) now require PO tokens. This share has been creeping up through 2025. If it ever crosses 50%, the free local workflow becomes painful and you'll need bgutil + yt-dlp for many videos rather than as a fallback.
- The cookie‑auth re‑enable PR (#565) is still open. Until merged, age‑restricted videos require the yt-dlp path.

### Notable forks and alternative libraries

- **`@danielxceron/youtube-transcript` (npm 1.2.6)** — Node fork of `youtube-transcript` with automatic HTML‑scraping → Innertube fallback. Worth studying its pattern even if you use Python.
- **`ericmmartin/youtube-transcript-plus` (Node, v1.1.x)** — switched to Innertube in v1.1.0, similar to the Python flagship.
- **`trldvix/youtube-transcript-api` (Java)** — Innertube‑based, supports bulk over playlists/channels.
- **`pub.dev/youtube_transcript_api` (Dart)** — Dart port.
- **PR #565 to jdepoix's repo** — adds `cookies_from_browser=` à la yt-dlp.

There is **no major Python fork** of youtube-transcript-api with materially different anti‑blocking behavior. The maintainer is responsive and the library tracks the canonical Innertube approach. The right defensive posture is "stay on the latest 1.2.x and add your own throttling/fallback layer," not "switch to fork X."

### The "WatchTimer / Innertube" approach

Some of the JS community libraries (and the Medium / Apify guides) refer to a "WatchTimer" approach — this is essentially: scrape the embedded JSON on `/watch`, extract the timed‑text URL, and hit it directly. This is the *old* method that broke in early 2025. The Innertube approach replaces it; youtube-transcript-api 1.1+ uses Innertube, not WatchTimer. If you read a tutorial that talks about "WatchTimer" or "ytInitialPlayerResponse parsing," it is dated and likely doesn't work anymore.

## 6. Recommended Architecture for a 10,000‑Video Overnight Job

Here is a concrete, opinionated design synthesized from everything above.

### 6.1 Stack and versions

```
python                       >= 3.11
youtube-transcript-api       == 1.2.4   # primary
yt-dlp                       >= 2026.01 (or current stable / nightly)
pytubefix                    == 10.6.x   # optional last-resort
tenacity                     >= 9.0      # retry helpers
sqlalchemy or sqlite3        # progress DB
httpx[http2]                 # optional, for HTTP/2 if you experiment
```

Optional (only spin up if you start seeing `PoTokenRequired`):
```
docker run --name bgutil -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider
```

### 6.2 Fallback chain (per video)

```
1. youtube-transcript-api.fetch(video_id, languages=['en','en-US','en-GB','a.en'])
     └─ on NoTranscriptFound: list() → find_transcript([...]) → .translate('en').fetch()
2. (only if 1 raised PoTokenRequired)
     yt-dlp --skip-download --write-auto-subs --write-subs
            --sub-langs "en.*"  --sub-format "json3"
            --extractor-args "youtube:player_client=tv,mweb"
            [+ bgutil provider auto-discovered on localhost:4416]
            [+ --cookies cookies.txt if age-restricted]
3. (only if 2 fails and you really want this video)
     pytubefix.YouTube(url, use_po_token=True).captions['a.en'].xml_captions
4. (give up) mark status='error', move on.
```

Most videos resolve at step 1 in ≈ 2 s. Step 2 takes 5–15 s. Step 3 takes 15–30 s and is rarely needed.

### 6.3 Architecture sketch

```
                ┌────────────────────────────┐
                │  video_ids.txt  (10,000)   │
                └─────────────┬──────────────┘
                              │ one-shot load
                              ▼
                ┌────────────────────────────┐
                │ progress.sqlite            │
                │  (video_id PK, status,...) │
                └─────────────┬──────────────┘
                              │ SELECT pending LIMIT 1
                              ▼
                ┌────────────────────────────┐
                │  Fetcher loop (1 worker)   │
                │  - random delay 3-7 s      │
                │  - User-Agent pool         │
                │  - exponential backoff     │
                │  - block detection         │
                └─────┬──────────────────┬───┘
                      │                  │
        primary       │                  │   fallback (rare)
                      ▼                  ▼
        youtube-transcript-api      yt-dlp subprocess
        (Innertube path)            (--write-subs --skip-download)
                      │                  │
                      └────────┬─────────┘
                               ▼
                    transcripts/{video_id}.json
                    (also: status update in DB)
```

### 6.4 Critical defaults

| Knob | Recommended value | Rationale |
|---|---|---|
| Inter‑video delay | `random.uniform(3, 7)` s | ~12 req/min average |
| Long pause every N videos | 60–120 s every 500 | Resets short‑window counters |
| Worker count | **1** (per public IP) | Concurrency is counter‑productive on one IP |
| Block backoff | 5 m → 15 m → 45 m → 2 h → abort | Empirical, errs on cautious side |
| Max attempts per video | 5 | After 5 it's almost certainly unrecoverable |
| Languages requested | `['en','en-US','en-GB','a.en']` then translate | Maximizes hit rate |
| User‑Agent pool | 3 modern desktop UAs | Marginal benefit, low cost |
| HTTP session | Recycle every 200–500 requests | Avoid extreme keepalive patterns |
| Cookies | None initially; add throwaway account only if needed | Avoid burning your real account |
| yt-dlp args | `--sleep-requests 1.5 --sleep-subtitles 3 --extractor-args "youtube:player_client=tv,mweb"` | Tracks current best practices |

### 6.5 Estimated wall time

| Scenario | Throughput | 10,000 videos |
|---|---|---|
| Single home IP, recommended pacing, no blocks | ~720 videos/h | **~14 h** |
| Single home IP, occasional blocks (2× 30 m loss) | ~600/h | ~16–17 h |
| Single home IP, heavy blocks (forced multi‑night) | ~300/h effective | 2 nights × 16 h |
| Home IP + phone cellular IP (2 distinct externals) | ~1,200/h | **~8–9 h** |
| Home IP + friend's home SSH SOCKS proxy | ~1,200–1,400/h | ~7–8 h |

Plan as **one long overnight job (≈14–17 hours)**. Start at 18:00 local, expect completion by ~10:00 next morning, with a SQLite checkpoint that lets you resume cleanly if you abort or the script crashes. If you have two IPs available, run two scripts pointing at the same SQLite database (use `BEGIN IMMEDIATE` or row‑level "claim" with `UPDATE … WHERE status='pending' LIMIT 1 RETURNING video_id`) and finish in roughly half the time.

### 6.6 Pseudocode for the inner loop

```python
import random, time, sqlite3, json
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    RequestBlocked, IpBlocked, TooManyRequests, PoTokenRequired,
    TranscriptsDisabled, NoTranscriptFound, VideoUnavailable,
    AgeRestricted, CouldNotRetrieveTranscript,
)

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]
LANGS = ["en", "en-US", "en-GB", "a.en"]

def make_api():
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = random.choice(UA_POOL)
    s.headers["Accept-Language"] = "en-US,en;q=0.9"
    return YouTubeTranscriptApi(http_client=s)

def fetch_one(video_id):
    api = make_api()
    try:
        ft = api.fetch(video_id, languages=LANGS)
        return ("ok", ft.language_code, ft.is_generated, ft.to_raw_data(), None)
    except NoTranscriptFound:
        # try translation
        try:
            tl = api.list(video_id).find_transcript(LANGS).translate("en").fetch()
            return ("ok", "en-translated", True, tl.to_raw_data(), None)
        except Exception as e:
            return ("no_captions", None, None, None, str(e))
    except TranscriptsDisabled as e: return ("no_captions",  None,None,None,str(e))
    except VideoUnavailable    as e: return ("unavailable", None,None,None,str(e))
    except AgeRestricted       as e: return ("age_restricted",None,None,None,str(e))
    except PoTokenRequired     as e: return ("needs_pot",    None,None,None,str(e))
    except (RequestBlocked, IpBlocked, TooManyRequests) as e:
        raise BlockedError(str(e))
    except CouldNotRetrieveTranscript as e:
        return ("error", None, None, None, str(e))

class BlockedError(Exception): pass

BACKOFFS = [300, 900, 2700, 7200]  # 5m, 15m, 45m, 2h

def run(db_path):
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    consecutive_blocks = 0
    while True:
        row = con.execute(
            "SELECT video_id FROM transcripts "
            "WHERE status IN ('pending','blocked','error') AND attempts < 5 "
            "ORDER BY attempts ASC, RANDOM() LIMIT 1"
        ).fetchone()
        if row is None: break
        vid = row["video_id"]
        try:
            status, lang, gen, data, err = fetch_one(vid)
            consecutive_blocks = 0
            con.execute(
                "UPDATE transcripts SET status=?, language=?, is_generated=?, "
                "transcript=?, error_message=?, fetched_at=CURRENT_TIMESTAMP, "
                "attempts=attempts+1 WHERE video_id=?",
                (status, lang, gen, json.dumps(data) if data else None, err, vid),
            )
            con.commit()
        except BlockedError as e:
            consecutive_blocks += 1
            con.execute(
                "UPDATE transcripts SET status='blocked', error_message=?, "
                "attempts=attempts+1, last_attempt=CURRENT_TIMESTAMP "
                "WHERE video_id=?", (str(e), vid))
            con.commit()
            if consecutive_blocks >= len(BACKOFFS):
                print("Aborting for the night."); break
            time.sleep(BACKOFFS[consecutive_blocks-1])
            continue
        # normal inter-video delay
        time.sleep(random.uniform(3, 7))
        # longer pause every 500 successful fetches
        n_ok = con.execute("SELECT COUNT(*) c FROM transcripts WHERE status='ok'").fetchone()["c"]
        if n_ok and n_ok % 500 == 0:
            time.sleep(random.uniform(60, 120))
```

You then add a second pass that handles the `needs_pot` and `age_restricted` rows by shelling out to yt-dlp.

### 6.7 Operational tips

- **Test on 100 videos first.** A 30‑minute pilot will reveal whether your IP is already in YouTube's doghouse (e.g. if you've been doing this experimentally for weeks) or fresh.
- **Run on a wired (Ethernet) connection** for stability — Wi‑Fi reconnects mid‑run cause noisy intermittent failures that look like blocks.
- **Tail the log; don't watch a progress bar.** A noisy stream of `RequestBlocked` is your signal to abort, not "the bar is going slowly."
- **Don't run YouTube playback in the same browser** in parallel while the script runs — it adds requests to the same IP's budget. Watch videos on your phone instead.
- **Save raw transcripts plus normalized text.** Keep the `to_raw_data()` JSON (so you have timings) and also a flat `.txt` for downstream NLP — re‑fetching is expensive.
- **Mind your storage.** 10,000 transcripts is typically 100–500 MB; not large, but plan a directory layout (e.g. `transcripts/{video_id[0]}/{video_id}.json`) so you don't drop 10,000 files in one folder.
- **Update `yt-dlp` right before the run.** YouTube extractor regressions appear and disappear weekly; using last month's `yt-dlp` is the most common cause of "it worked yesterday."
- **Verify legal/TOS context** for your use case. YouTube's TOS prohibits automated access to content outside the official Data API, and the official Data API does not expose transcripts. Personal research, fair use, and small‑scale academic uses are typically tolerated but not licensed. This report is a technical analysis only and not legal advice.

# Bulk-Fetching YouTube Transcripts (10,000+ videos) from a Home IP: A Practical Research Report (May 2026)

**Short answer up front.** As of mid‑2026, the only reliable *free* path for pulling 10,000+ YouTube transcripts from a single home internet connection is:

1. **Use a residential (home) IP**, not a cloud VM — YouTube blanket‑blocks AWS/GCP/Azure/DO ranges for transcript endpoints.
2. **Use `youtube-transcript-api` ≥ 1.1.0 (currently 1.2.4)** as the primary fetcher. The maintainer rewrote it in March 2025 to hit the Innertube `/youtubei/v1/player` endpoint instead of scraping `/watch`, which fixed the wave of mass blocks from early 2025.
3. **Throttle to roughly 1 request every 3–8 seconds with jitter**, plus exponential backoff on 429 / `RequestBlocked` / `IpBlocked`.
4. **Fall back to `yt-dlp --write-auto-subs --write-subs --skip-download`** (with `--sleep-subtitles`, `--sleep-requests`, a mobile client, and optionally a local **bgutil-ytdlp-pot-provider**) for videos that the primary path rejects (PO‑token‑gated auto‑translated tracks, age‑restricted videos, etc.).
5. **Persist progress in SQLite or JSONL** so the run is resumable across nights.

At ~5 s per video, 10,000 videos take roughly **14 hours of wall time** — so plan for a single overnight run with a margin, or split across two nights with a checkpoint database. Expect a real‑world success rate of 85–95% (the remainder being legitimately unavailable: no captions, private, deleted, members‑only, age‑restricted without cookies).

The rest of this report goes deep on each piece of that recommendation.

## 1. The Library Landscape in 2025–2026

### youtube-transcript-api (jdepoix) — the primary tool

This is by far the most popular Python library for the task (≈7.5k GitHub stars, 769 forks). Its current state as of May 2026:

- **Latest version: 1.2.4** (PyPI). Major versions of note:
  - **v1.0.0 (2025)** — Replaced the old static methods (`get_transcript`, `list_transcripts`) with an instance‑based `YouTubeTranscriptApi().fetch(video_id)` / `.list(video_id)` API. The old static methods were removed in a subsequent breaking release; using a stale pre‑1.0 tutorial will silently bypass any proxy or session config you set.
  - **v1.1.0 (March 25, 2025)** — *Critical fix.* Refactored caption‑track discovery from scraping the `/watch` HTML to calling YouTube's internal **Innertube** API (`/youtubei/v1/player`). This fixed the mass breakage that hit users in early 2025 when YouTube changed the watch‑page JS. A new `PoTokenRequired` exception was added.
  - **v1.1.x → 1.2.x** — Added retry logic that triggers IP rotation when used with Webshare, more robust JS parsing, and an `__all__` export. Cookie auth has been "temporarily disabled" since 1.1.0 because the maintainer hasn't yet ported it to the Innertube flow (the code in `_api.py` literally has a comment saying so).
- **How it works under the hood** (per `_transcripts.py`):
  1. GET the `/watch?v=…` HTML to extract `INNERTUBE_API_KEY` and other context tokens.
  2. POST to `https://www.youtube.com/youtubei/v1/player` with a fixed `INNERTUBE_CONTEXT` and the video ID. The response includes `captions.playerCaptionsTracklistRenderer.captionTracks[].baseUrl`.
  3. GET that `baseUrl` (a `timedtext` URL) to retrieve the actual XML transcript.
  So **each video = 3 HTTP requests** to youtube.com. The watch‑HTML fetch is the bandwidth‑heavy one and the maintainer has confirmed it cannot be skipped because the tokens are per‑request.
- **Known issues in 2025–2026**:
  - On cloud‑provider IPs (AWS / GCP / Azure / DigitalOcean / Hetzner / OVH), virtually 100% of requests now return `RequestBlocked` / `IpBlocked`. This is *not* rate‑limit, it's IP‑reputation blocking — the very first request fails.
  - On residential IPs blocking still happens but only after sustained traffic. Reported anecdata in GitHub issue #66 and others puts the threshold at roughly **200–300 consecutive fast requests** before a temporary block. The TooManyRequests / `RequestBlocked` state typically clears in hours, sometimes minutes, sometimes a day.
  - The maintainer's recommended fix is exclusively rotating residential proxies via the built‑in `WebshareProxyConfig` (paid). There is no free, automatic anti‑block mechanism baked in.
- **Cookie support**: Disabled in the current Innertube‑based code path. There is an open PR (#565 by PhilBrk8) to add yt-dlp‑style `cookies_from_browser='chrome'` support for age‑restricted videos but it had not been merged at the time of writing. If you need cookies today you must use yt-dlp instead.

**Verdict:** youtube-transcript-api 1.2.x is the right *primary* fetcher for the task because (a) it's the lowest‑bandwidth option (raw XML, no HTML player extraction beyond the watch page), (b) it works without a PO token for the standard `timedtext` endpoints, and (c) it is the most mature library specifically for transcripts. But you cannot rely on its built‑in defenses — you must add throttling, retry, and a fallback yourself.

### yt-dlp — the heavyweight fallback

yt-dlp (latest stable in 2025‑09 ≈ `2025.09.23`; nightly releases roughly weekly) is the reference implementation for everything YouTube‑related. For subtitles only:

```
yt-dlp --skip-download --write-auto-subs --write-subs \
       --sub-langs "en.*" --sub-format "json3/vtt/best" \
       --sleep-subtitles 3 --sleep-requests 1.5 \
       --extractor-args "youtube:player_client=tv,web_safari" \
       -o "%(id)s.%(ext)s" -a video_ids.txt
```

Strengths:
- Actively maintained, gets fixes within days of YouTube changes.
- Robust client selection — when one player client breaks you can switch (`player_client=tv`, `web_safari`, `mweb`, `android_vr` are commonly cited as working in 2025).
- Native cookie support (`--cookies cookies.txt` or `--cookies-from-browser firefox`).
- Built‑in throttling: `--sleep-requests`, `--sleep-subtitles`, `--sleep-interval`, `--max-sleep-interval`, `--limit-rate`.
- Handles age‑restricted, premieres, members‑only, livestream captions, etc.
- PO‑token framework with pluggable providers (see below).

Weaknesses for *bulk transcript‑only* use:
- About **5–10× the bandwidth and CPU** of youtube-transcript-api because it does full player extraction, n‑sig decryption, m3u8 manifest parsing, format sorting, etc., even with `--skip-download`. On a 600‑video channel one developer reported ~40 minutes "mostly waiting on YouTube's subtitle endpoints, not yt-dlp itself."
- Known issue (#13075, #14452, May 2025+): some `timedtext` URLs now require a PO token (`pot=…&potc=1` parameters); auto‑translated tracks have been hit hardest, and removing `--sub-langs` filters that pull auto‑translated languages avoids it.
- Known issue (#13831): `HTTP Error 429: Too Many Requests` on auto‑translated subtitle endpoints is now common even at modest rates; using `--write-auto-subs` for an *exact* `en` (rather than `en.*` glob that pulls translated variants) substantially reduces 429s.
- `--sleep-interval` only fires on video download, not metadata extraction — the relevant flags for our use case are **`--sleep-requests`** (between every HTTP request) and **`--sleep-subtitles`** (between subtitle downloads).

### pytube / pytubefix

- **pytube** is essentially abandoned for serious use; it breaks every few months when YouTube changes the player JS.
- **pytubefix** is the actively maintained community fork (currently 10.6.x). It exposes `yt.captions[…].xml_captions` and `.generate_srt_captions()`, and importantly added **automatic PoToken generation via Node.js** (`use_po_token=True` or `YouTube(url, 'WEB')`) using bundled `nodejs-wheel-binaries`. So a fresh pytubefix install can produce its own PO token without Docker, which is uniquely useful for free home use.
- Downsides: requires Node.js at runtime, slower than youtube-transcript-api (it fetches the full watch page and parses streams even when you only want captions), and PoToken generation still doesn't get you past raw IP bans — only past the "Sign in to confirm you're not a bot" gate.

It is reasonable to add pytubefix as a *third* fallback after youtube-transcript-api and yt-dlp, mainly for videos where yt-dlp keeps failing on PO‑token‑gated captions.

### Direct Innertube approaches

The Innertube path is what youtube-transcript-api 1.1+ uses internally. Several other projects ride the same approach:
- **LuanRT/YouTube.js** — Excellent JS Innertube client; widely used in the JS ecosystem (npm `youtube-transcript-plus`, `@danielxceron/youtube-transcript` 1.2.6 use it as a fallback when HTML scraping fails). Not a Python library, but the technique is documented in detail and easy to port.
- **trldvix/youtube-transcript-api** — Java port, also Innertube‑based, and supports playlist/channel bulk fetch natively.
- **`youtube_transcript_api` (Dart)** on pub.dev — Dart port with Innertube integration.

The Innertube approach in JavaScript (often referenced in 2025 tutorials) is essentially:
1. `GET https://www.youtube.com/watch?v=ID` to scrape `INNERTUBE_API_KEY`.
2. `POST https://www.youtube.com/youtubei/v1/player?key=…` with `{"context":{"client":{"clientName":"ANDROID","clientVersion":"20.10.38"}},"videoId":"ID"}` — note the **ANDROID client identity** historically reduced bot challenges (it has been getting tightened in 2025; `IOS` and `TV` clients are now often cited as more reliable).
3. Parse `captions.playerCaptionsTracklistRenderer.captionTracks[].baseUrl`.
4. GET that URL (XML/`fmt=json3`).

Rolling your own Innertube client is a viable last‑resort fallback if both youtube-transcript-api and yt-dlp fail, because you can experiment with different `clientName` values (`ANDROID`, `IOS`, `TVHTML5`, `MWEB`, `WEB_EMBEDDED_PLAYER`) which trigger different anti‑bot pathways. In practice you rarely need to write this yourself.

### Other notable options

- **Hosted/free‑tier APIs** (Supadata, transcriptapi.com, youtube‑transcript.io, ScrapeCreators, Apify, SerpApi). All offer 100–250 free credits per month or per signup; none are remotely sufficient for 10,000 videos unless you stack many accounts (which violates their TOS). Useful only as a tiny last‑resort fallback (~50 videos/day across free tiers). Be aware many of these are simply proxied calls to youtube-transcript-api with residential proxies in front.
- **`@danielxceron/youtube-transcript`** (Node.js, npm 1.2.6) — Notable for documenting an automatic HTML‑scraping → Innertube fallback pattern; same idea you should adopt in your own Python pipeline.
- **Browser-automation scrapers** (Selenium/Playwright clicking the "Show transcript" button) — Reliable but **slow (≈15–30 s/video)** and trivially detected as a headless browser unless you use stealth plugins. Not recommended for 10,000 videos but it's the absolute last resort for videos where every API path fails.
- **`youtube-local`** (user234683) — Browser‑based local YouTube viewer with built‑in Tor integration. Its issue tracker (#134, #121) documents that **YouTube blocks Tor exit nodes essentially completely** for the desktop/web client; android‑user‑agent requests sometimes still got through circa 2022 but the more recent threads say "all Tor requests now blocked." Treat Tor as **not viable** for sustained bulk fetching in 2026.

## 2. How YouTube Detects Bot Traffic on Transcript Endpoints

There is no public documentation of YouTube's bot‑detection rules, but the yt-dlp and youtube-transcript-api issue trackers, combined with the official yt-dlp **PO Token Guide** wiki, give us a reliable picture.

### The four signals that get you blocked

1. **IP reputation.** This is the dominant signal in 2025–2026. Two sub‑signals:
   - **Cloud provider ASN.** Every cloud range (AWS, GCP, Azure, DigitalOcean, OVH, Hetzner, Linode, Vultr, Oracle, etc.) is *pre‑blocked*. The first request from a fresh EC2 instance now returns `RequestBlocked` immediately, no warm‑up. youtube-transcript-api's own README and dozens of GitHub issues confirm this.
   - **Volume from a single residential IP.** Once you cross some unpublished threshold (anecdotally 100–500 requests inside a short window, faster from a single TCP session), the IP enters a "captcha required" state. The library reports it as `RequestBlocked` with the message "YouTube is receiving too many requests from this IP and now requires solving a captcha to continue."

2. **Missing or invalid PO Token (Proof of Origin).**  PO Token is YouTube's anti‑bot attestation. The yt-dlp wiki defines it as a token "generated by either BotGuard (Web), DroidGuard (Android), or iOSGuard (iOS)" that proves the request came from a genuine player on a genuine client device. Each token has a *content binding* (either to a session/visitor ID or to a video ID). Tokens cannot cross client families — a Web PO token will not work for the Android client.
   PO tokens are required in three contexts ("PO Token contexts"):
   - **GVS** — Google Video Server (video stream URLs)
   - **Player** — Innertube `/player` requests (format URLs)
   - **Subs** — `timedtext` subtitle URLs (this is the one that matters for us)
   
   In late 2024 / 2025 YouTube progressively rolled out PO‑token enforcement for `timedtext`. Auto‑translated subtitles have been the first to require it (see yt-dlp issue #13075 and #13831). Native captions in the original language usually do **not** require a PO token as of mid‑2026, which is why youtube-transcript-api still works on most videos from a residential IP without one. But when YouTube tightens enforcement (which can happen overnight), you'll start seeing the `PoTokenRequired` exception that the library added in 1.1.0 specifically to surface this.

3. **Client fingerprint and headers.** YouTube inspects:
   - User‑Agent (a default Python `requests` UA is an instant red flag).
   - The `client.clientName` / `clientVersion` field inside the Innertube context JSON; these are version‑pinned and YouTube increments the minimum acceptable version every few weeks. yt-dlp ships fresh versions in each release.
   - TLS / JA3 fingerprint — yt-dlp now ships with `curl_cffi` to mimic real‑browser TLS handshakes; this matters when raw `requests` from a flagged IP fails but a `curl_cffi`-backed call succeeds.
   - HTTP/2 vs HTTP/1.1 — youtube‑local#134 notes Tor Browser worked but Python requests through Tor didn't, and the only obvious difference was HTTP/2.

4. **Cookie / session state.**
   - No cookies at all is fine for most public videos, but a *consent* cookie is sometimes required in the EU.
   - A real user cookie (logged‑in session) raises the rate limit significantly **but** YouTube will eventually **ban the account itself** if you saturate it, per youtube-transcript-api's own warning. Use a throwaway account if you do this, and treat the account as expendable.

### What "blocked" looks like in practice

- `RequestBlocked` / `IpBlocked` exception (youtube-transcript-api).
- `HTTP Error 429: Too Many Requests` (yt-dlp on `timedtext`).
- `ERROR: [youtube] VIDEO_ID: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies for the authentication.` (yt-dlp).
- An empty response body on the `timedtext` GET (PO token now required but not supplied).
- A 302 redirect to `https://consent.youtube.com/` or `https://www.google.com/sorry/` (common on Tor / VPN exits).

The block is **per‑IP and time‑bound**, not permanent. From residential IPs the block typically lifts in 15 minutes to a few hours; from cloud IPs the block tends to be persistent because the entire ASN is flagged.

### What does NOT meaningfully trigger blocks

- Distinct video IDs (it's volume, not the targets).
- Time of day (no evidence of TOD‑based shaping).
- Mixing Shorts and regular videos (same endpoints).
- Single‑user moderate browsing in parallel from the same household (a few extra requests/hour is invisible).

## 3. Free Anti‑Blocking Strategies — What Actually Works

Within the user's constraints (free only, single home IP, overnight), here is the realistic assessment of each anti‑blocking technique.

### 3.1 Throttling and pacing — the single most important free defense

This is what actually works. Concrete recommendations:

- **Inter‑request delay: 3–8 seconds with jitter.** A reasonable default is `random.uniform(3, 7)` between videos. At 5 s average, you complete 10,000 videos in ~14 hours.
- **Exponential backoff on block.** When `RequestBlocked` / `IpBlocked` / 429 fires, sleep 5 minutes → 15 minutes → 45 minutes → 2 hours → give up for the night. Critically, **flush any in‑memory rate‑limit state** when this happens; do not just retry immediately with a slightly longer delay.
- **Soft daily cap.** Around 7,000–10,000 transcript fetches per 24 hours from a single residential IP is plausible. Beyond that, blocks become frequent enough that throughput inverts (you spend more time backing off than fetching).
- **Avoid bursts**, even small ones. Five fast requests in a row are more dangerous than 20 evenly‑spaced requests.

### 3.2 Cookie‑based authentication

- youtube-transcript-api: cookie support is **disabled** in the current Innertube path (see `_api.py`'s `# Cookie auth has been temporarily disabled` comment). A PR is open (#565) but not merged.
- yt-dlp: full support via `--cookies cookies.txt` (Netscape format) or `--cookies-from-browser firefox`. Logged‑in cookies raise the soft rate limit substantially — anecdotally 2–5×. **But** the maintainer's explicit warning applies: with sustained bulk abuse the account itself can be banned. Use a throwaway Google account, expect to burn it, and never use cookies from your daily‑driver account.
- A useful middle ground is **anonymous consent cookies** (just satisfying the EU consent banner). youtube-transcript-api handles this automatically via `_create_consent_cookie`.

### 3.3 PO Token generation (free, local)

For the small minority of videos that require a PO token for `timedtext`, you can generate tokens locally for free:

- **bgutil-ytdlp-pot-provider** (Brainicism, TypeScript, current 1.3.x) — the reference free PO token provider. Two deployment modes:
  - **HTTP server (recommended)**: `docker run --name bgutil-provider -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider`. yt-dlp auto‑detects it on `127.0.0.1:4416` via the bundled plugin. Caches tokens internally (default 6h TTL).
  - **Script mode**: Node.js script invoked per request.
- **bgutil-ytdlp-pot-provider-rs** (jim60105, Rust port) — faster, single binary, no Docker required.
- **pytubefix's built‑in node.js generator** — easiest free path if you only need one token at a time, no Docker, no separate service. Triggered by `YouTube(url, use_po_token=True)`.

All of these use LuanRT's BotGuard‑interfacing library and produce a real PO token by running the same JavaScript challenge a browser would run. **They do *not* magically bypass IP blocks** — the yt-dlp wiki explicitly states a PO token only makes traffic *look more legitimate* on an already‑flagged IP. Combined with a clean residential IP, they meaningfully reduce the "Sign in to confirm you're not a bot" gate.

For the user's use case (transcripts only, residential IP), **you probably don't need PO tokens at all** for the bulk of fetches. Treat the bgutil provider as something you spin up *only when* `PoTokenRequired` starts appearing.

### 3.4 Tor / free VPN rotation

- **Tor: not viable.** youtube-local issue #134 (2022, never fixed) and many subsequent reports confirm that YouTube has aggressively blocked Tor exit nodes. Most exits return 429 / consent redirect / `google.com/sorry` immediately. Tor Browser sometimes works because of HTTP/2 differences, but Python `requests` over Tor essentially does not. Even if it worked, Tor's bandwidth (~1–5 Mbit/s per circuit) and request latency make 10,000 transcript fetches impractical.
- **Free VPNs (ProtonVPN free, Windscribe free, etc.):** All popular free VPN gateway IPs are *already* flagged by YouTube because everyone else doing this has used them. ProtonVPN's free tier servers in particular are well‑known. You may get 50–500 requests before each gateway IP is blocked, after which you must rotate gateways manually. Free VPNs typically rotate IPs only when you reconnect, and most cap you at a few servers.
- **Phone tethering / cellular:** Underrated free option. Your phone's CGNAT‑pooled cellular IP is shared with many real users, has good reputation, and **toggling airplane mode** typically gives you a fresh IP from the carrier's pool. This is genuinely useful as a manual reset when your wired home IP gets blocked. Watch your data cap — 10,000 transcripts is roughly 2–5 GB of traffic counting the watch‑HTML fetches.
- **Free proxy lists:** Effectively dead for YouTube. Public proxy lists (FreeProxyList, PubProxy, etc.) consist almost entirely of either honeypots or IPs already blacklisted by every major site. Success rate against YouTube is <5%.

### 3.5 Free residential alternatives

The only genuinely *free* residential routing is **your own home IP** plus, optionally, **your phone's cellular IP**. There are no legitimately free rotating‑residential‑proxy services; "free trials" from Webshare, Bright Data, SmartProxy, Oxylabs typically give 100–1,000 MB of bandwidth which evaporates in dozens of transcripts and triggers credit‑card requirements. The other end of the spectrum — peer‑to‑peer "free residential" networks like Hola — sells *your* bandwidth out the back door and you should not install them.

If you have a **friend or family member elsewhere** willing to run a small SOCKS5 proxy (`ssh -D 1080 friend@their-home`) or a WireGuard endpoint, you have a free second residential IP. The ClawHub "YouTube Transcript" skill is essentially this pattern — a WireGuard tunnel to a residential endpoint to route youtube-transcript-api traffic.

### 3.6 Session rotation / UA rotation

- **User‑Agent rotation:** Marginal benefit but very cheap. Pick 3–5 modern desktop and mobile UA strings and rotate every N requests. Don't rotate every request — that itself is a fingerprint.
- **Innertube client rotation:** More impactful. When the WEB client starts being challenged, switching to TV (`TVHTML5`), MWEB, or IOS clients in the Innertube context can buy hours of additional throughput. yt-dlp does this internally via `--extractor-args "youtube:player_client=tv,web_safari,mweb"`.
- **TCP session rotation:** Closing and reopening the `requests.Session` (and the underlying TCP connection) every 100–500 requests can help; long‑lived keepalive connections appear more "scraper‑shaped" than browser‑shaped traffic.

### 3.7 Splitting across multiple devices on a home network

This is one of the most useful free tactics that people overlook. From the perspective of YouTube's bot detection, **all devices behind your residential NAT share one public IPv4 address**. So splitting work across two laptops does *not* double your rate budget — you'd just hit the IP block faster. **However:**
- IPv6 is a different story. If your ISP gives you a /56 or /64 IPv6 block (most residential ISPs do today), each device can have a *distinct* public IPv6 address. YouTube's `timedtext`, `youtubei`, and `www.youtube.com` endpoints all serve over IPv6 (`AAAA` records). In practice rate limits seem to be tracked per‑/64 in many cases, but per‑/128 has been reported intermittently. It is worth testing whether `curl -6` from two different devices on your LAN with different IPv6 addresses gets separately rate‑limited; if so, you can parallelize work across 2–4 devices for a meaningful throughput boost.
- Multiple devices on different physical networks (your home Wi‑Fi + your phone's cellular tethering + a friend's network) genuinely give you N times the budget and are the most reliable parallelization.

### 3.8 Mobile vs desktop endpoints

The `m.youtube.com` host and the mobile Innertube clients (`MWEB`, `ANDROID`, `IOS`) sometimes have looser anti‑bot enforcement on `timedtext`, although this has been tightening. yt-dlp's `--extractor-args "youtube:player_client=mweb"` is a one‑liner to try when the default WEB client starts being challenged. The android/iOS clients additionally avoid some JS challenge requirements but each requires its own client version pinning.

### 3.9 What does NOT help

- Switching DNS resolvers.
- Spoofing `X-Forwarded-For` (YouTube ignores it).
- Adding more `Accept` headers.
- Calling `/watch` with a referer.
- "Stealthy" Selenium plugins — they help against Cloudflare and DataDome, not against YouTube's volume‑based heuristics.

## 4. Practical Implementation Patterns

### 4.1 Recommended request rate from a single home IP

Based on (a) yt-dlp issue threads where users report 429s after ~100 fast subtitle downloads, (b) youtube-transcript-api GitHub issue #66 reporting blocks at ~250 requests in a tight loop, and (c) blog posts on bulk transcript collection (`ahmetky.dev/blog/post-02`, Bright Data scraping guides) recommending residential rotation for any scale:

| Rate (req/min) | Behavior from a home residential IP |
|---|---|
| 30+ (≤2 s avg) | Block within 5–15 minutes, almost guaranteed |
| 10–15 (4–6 s avg) | **Recommended baseline.** Mostly stable for hours; occasional transient blocks |
| 6–10 (6–10 s avg) | Very safe; almost no blocks reported anecdotally |
| <6 (>10 s avg) | Wasteful for an overnight job — you won't finish 10,000 |

The sweet spot for a 10,000‑video overnight job is **roughly 12 requests/min average → 5 s mean delay**, with `random.uniform(3, 7)` jitter. Add a longer "breather" pause every 500 videos (e.g. 60–120 s) to reset any short‑window counter.

Note that each youtube-transcript-api `fetch()` is internally **3 HTTP requests** (watch HTML + Innertube player + timedtext), so 12 fetches/min ≈ 36 HTTP requests/min — still well within what a residential IP can do invisibly.

### 4.2 Detecting that you've been blocked

| Signal | What it means | Reaction |
|---|---|---|
| `youtube_transcript_api._errors.RequestBlocked` | IP currently flagged | Stop, long backoff (15+ min) |
| `youtube_transcript_api._errors.IpBlocked` | Same, more persistent | Stop for several hours |
| `youtube_transcript_api._errors.TooManyRequests` | Captcha required | Stop, long backoff |
| `youtube_transcript_api._errors.YouTubeRequestFailed` / `CouldNotRetrieveTranscript` (generic) | Could be transient | One retry, then skip |
| `PoTokenRequired` (added in v1.1.0) | Specific track needs a PO token | Skip this language, try another, or fall back to yt-dlp + bgutil |
| `TranscriptsDisabled` | Video legitimately has no captions | Skip permanently, mark as `no_captions` |
| `NoTranscriptFound` (in requested language) | Captions exist but not in your language | Try `.translate('en').fetch()` or list available |
| `VideoUnavailable` | Deleted, private, region‑locked, members‑only | Skip permanently, mark as `unavailable` |
| `AgeRestricted` | Need auth | Skip, queue for yt-dlp + cookies fallback |
| HTTP 429 (yt-dlp) | Rate limited | Stop, exponential backoff |
| HTTP 403 with empty body | Likely PO token required | Switch player_client or use bgutil |
| 302 to `consent.youtube.com` or `google.com/sorry` | IP fully flagged | Stop for the night |

A robust loop logs both the exception type and the HTTP status so you can post‑hoc tell legitimate "no transcript" failures from blocking failures.

### 4.3 Resumability and state management

For 10,000+ videos, you **must** make the script resumable. SQLite is the right tool:

```python
# schema
CREATE TABLE transcripts (
    video_id      TEXT PRIMARY KEY,
    status        TEXT NOT NULL,    -- 'pending','ok','no_captions','unavailable',
                                    -- 'age_restricted','blocked','error'
    language      TEXT,
    is_generated  INTEGER,
    transcript    TEXT,             -- raw text or JSON
    error_message TEXT,
    fetched_at    TIMESTAMP,
    attempts      INTEGER DEFAULT 0,
    last_attempt  TIMESTAMP
);
CREATE INDEX idx_status ON transcripts(status);
```

Logic:
1. On startup, INSERT OR IGNORE all video IDs with `status='pending'`.
2. The main loop selects `WHERE status IN ('pending','error','blocked') AND attempts < 5 ORDER BY attempts ASC, RANDOM()`.
3. After each video, UPDATE the row with the new status.
4. Commit every N rows (e.g. every 10) to balance durability and write cost.

A JSONL "append‑only log" is fine too if you don't need re‑queries, but SQLite makes "show me everything still pending" trivial. Either way, **write the transcript content to disk immediately** — don't buffer in memory.

### 4.4 Concurrent vs sequential

The instinct is "use `ThreadPoolExecutor(max_workers=30)`" as in the ahmetky.dev blog. **Don't.** From a single home IP that just trades wall‑clock time for blocked time — YouTube counts concurrent connections from the same IP. The blog's pattern only works with a proxy pool.

The right amount of concurrency from one IP is **1 worker for fetches**, optionally with a tiny amount of pipelining (fetch the next video's HTML while parsing the current transcript). If you have IPv6‑per‑device or a friend's residential proxy, scale up to **1 worker per distinct external IP**.

### 4.5 Handling each failure mode

- **No captions** (`TranscriptsDisabled`, `NoTranscriptFound` and no translation works): mark `no_captions`, never retry.
- **Blocked** (`RequestBlocked`, `IpBlocked`, `TooManyRequests`, 429): mark `blocked`, retry with backoff; if 3 consecutive videos block, stop the run entirely and resume tomorrow.
- **Deleted / private / age‑restricted / members‑only** (`VideoUnavailable`, `AgeRestricted`): mark `unavailable`, never retry on the same path; optionally queue for the yt-dlp+cookies fallback.
- **PO token required** (`PoTokenRequired`): queue for yt-dlp+bgutil fallback.
- **Network error / DNS / timeout:** retry up to 3 times with short backoff; then mark `error`.

### 4.6 Caching

Trivially handled by SQLite — never re‑fetch a row whose status is `ok`, `no_captions`, or `unavailable`. For idempotency across runs, key strictly on `video_id`.

### 4.7 Realistic throughput

Assume:
- 5 s average per video on the happy path.
- 10% of videos take 30 s extra (translation fallback, retries).
- Two block events per night each costing 30 minutes of backoff.

→ Effective throughput ≈ **600–700 videos/hour** from one residential IP at the recommended rate. For 10,000 videos that is **~14–17 hours of wall time**, i.e. one long overnight run starting in the evening and finishing by mid‑morning, or two shorter nights. If you parallelize across two distinct IPs (e.g. home Wi‑Fi + phone tether) you can roughly halve that.

## 5. Specific Recent Developments (2024–2026)

### What happened in 2024–2025

There were two distinct waves of breakage:

1. **Late 2024 — IP / cloud‑provider blocking went global.** Up through mid‑2024, youtube-transcript-api worked fine on Heroku, Vercel, AWS Lambda, etc. Sometime in Q3–Q4 2024 YouTube started rejecting requests from those ranges outright. This is the wave that produced the wall of GitHub issues like #303, #511, #335 and the Medium / Hugging Face Forum posts complaining that "my code worked locally but breaks on deploy." Nothing changed in the library; YouTube changed the IP policy. Workaround: residential proxies (Webshare) or run from home.

2. **Early 2025 — the watch‑page HTML parser broke.** YouTube refactored the JS variable structure on the `/watch` page so the regex used by youtube-transcript-api (and many others) stopped finding the caption track JSON. This caused mass failures even on residential IPs.

### The fix — Innertube migration in v1.1.0 (March 25, 2025)

The maintainer (jdepoix) shipped v1.1.0 which refactored caption track discovery to use the Innertube `/youtubei/v1/player` API instead of regex‑scraping the watch HTML. Key changes:

- New code path: GET watch HTML to extract `INNERTUBE_API_KEY` → POST to `/youtubei/v1/player` → read `captions.playerCaptionsTracklistRenderer.captionTracks` → GET each `baseUrl`.
- New `PoTokenRequired` exception, raised when the returned `timedtext` URL appears to require a PO token (so users get a *clear* error rather than silent empty data, and so the maintainer gets fast feedback when YouTube starts enforcing PO tokens for more tracks).
- Cookie authentication was *disabled* in this release because the old cookie logic was tied to the HTML‑scraping path and didn't translate cleanly to Innertube. As of 1.2.4 it is still disabled (the `_api.py` constructor has the explicit comment).
- v1.1.x and v1.2.x added retries for Webshare proxies (auto‑rotate on `RequestBlocked`), the more robust JS parser, mypy `__all__`, and so on.

### Is there a working free approach in 2026?

**Yes, with caveats.** The Innertube path in youtube-transcript-api 1.1+ is essentially the same path the JS Innertube clients (LuanRT/YouTube.js) and yt-dlp use, and it currently works on a residential IP for the *vast majority* of public videos without any PO token. The caveats:

- It is **rate‑sensitive on the home IP** in a way it wasn't two years ago. The block threshold is lower now.
- A growing fraction of `timedtext` tracks (especially auto‑translated ones) now require PO tokens. This share has been creeping up through 2025. If it ever crosses 50%, the free local workflow becomes painful and you'll need bgutil + yt-dlp for many videos rather than as a fallback.
- The cookie‑auth re‑enable PR (#565) is still open. Until merged, age‑restricted videos require the yt-dlp path.

### Notable forks and alternative libraries

- **`@danielxceron/youtube-transcript` (npm 1.2.6)** — Node fork of `youtube-transcript` with automatic HTML‑scraping → Innertube fallback. Worth studying its pattern even if you use Python.
- **`ericmmartin/youtube-transcript-plus` (Node, v1.1.x)** — switched to Innertube in v1.1.0, similar to the Python flagship.
- **`trldvix/youtube-transcript-api` (Java)** — Innertube‑based, supports bulk over playlists/channels.
- **`pub.dev/youtube_transcript_api` (Dart)** — Dart port.
- **PR #565 to jdepoix's repo** — adds `cookies_from_browser=` à la yt-dlp.

There is **no major Python fork** of youtube-transcript-api with materially different anti‑blocking behavior. The maintainer is responsive and the library tracks the canonical Innertube approach. The right defensive posture is "stay on the latest 1.2.x and add your own throttling/fallback layer," not "switch to fork X."

### The "WatchTimer / Innertube" approach

Some of the JS community libraries (and the Medium / Apify guides) refer to a "WatchTimer" approach — this is essentially: scrape the embedded JSON on `/watch`, extract the timed‑text URL, and hit it directly. This is the *old* method that broke in early 2025. The Innertube approach replaces it; youtube-transcript-api 1.1+ uses Innertube, not WatchTimer. If you read a tutorial that talks about "WatchTimer" or "ytInitialPlayerResponse parsing," it is dated and likely doesn't work anymore.

## 6. Recommended Architecture for a 10,000‑Video Overnight Job

Here is a concrete, opinionated design synthesized from everything above.

### 6.1 Stack and versions

```
python                       >= 3.11
youtube-transcript-api       == 1.2.4   # primary
yt-dlp                       >= 2026.01 (or current stable / nightly)
pytubefix                    == 10.6.x   # optional last-resort
tenacity                     >= 9.0      # retry helpers
sqlalchemy or sqlite3        # progress DB
httpx[http2]                 # optional, for HTTP/2 if you experiment
```

Optional (only spin up if you start seeing `PoTokenRequired`):
```
docker run --name bgutil -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider
```

### 6.2 Fallback chain (per video)

```
1. youtube-transcript-api.fetch(video_id, languages=['en','en-US','en-GB','a.en'])
     └─ on NoTranscriptFound: list() → find_transcript([...]) → .translate('en').fetch()
2. (only if 1 raised PoTokenRequired)
     yt-dlp --skip-download --write-auto-subs --write-subs
            --sub-langs "en.*"  --sub-format "json3"
            --extractor-args "youtube:player_client=tv,mweb"
            [+ bgutil provider auto-discovered on localhost:4416]
            [+ --cookies cookies.txt if age-restricted]
3. (only if 2 fails and you really want this video)
     pytubefix.YouTube(url, use_po_token=True).captions['a.en'].xml_captions
4. (give up) mark status='error', move on.
```

Most videos resolve at step 1 in ≈ 2 s. Step 2 takes 5–15 s. Step 3 takes 15–30 s and is rarely needed.

### 6.3 Architecture sketch

```
                ┌────────────────────────────┐
                │  video_ids.txt  (10,000)   │
                └─────────────┬──────────────┘
                              │ one-shot load
                              ▼
                ┌────────────────────────────┐
                │ progress.sqlite            │
                │  (video_id PK, status,...) │
                └─────────────┬──────────────┘
                              │ SELECT pending LIMIT 1
                              ▼
                ┌────────────────────────────┐
                │  Fetcher loop (1 worker)   │
                │  - random delay 3-7 s      │
                │  - User-Agent pool         │
                │  - exponential backoff     │
                │  - block detection         │
                └─────┬──────────────────┬───┘
                      │                  │
        primary       │                  │   fallback (rare)
                      ▼                  ▼
        youtube-transcript-api      yt-dlp subprocess
        (Innertube path)            (--write-subs --skip-download)
                      │                  │
                      └────────┬─────────┘
                               ▼
                    transcripts/{video_id}.json
                    (also: status update in DB)
```

### 6.4 Critical defaults

| Knob | Recommended value | Rationale |
|---|---|---|
| Inter‑video delay | `random.uniform(3, 7)` s | ~12 req/min average |
| Long pause every N videos | 60–120 s every 500 | Resets short‑window counters |
| Worker count | **1** (per public IP) | Concurrency is counter‑productive on one IP |
| Block backoff | 5 m → 15 m → 45 m → 2 h → abort | Empirical, errs on cautious side |
| Max attempts per video | 5 | After 5 it's almost certainly unrecoverable |
| Languages requested | `['en','en-US','en-GB','a.en']` then translate | Maximizes hit rate |
| User‑Agent pool | 3 modern desktop UAs | Marginal benefit, low cost |
| HTTP session | Recycle every 200–500 requests | Avoid extreme keepalive patterns |
| Cookies | None initially; add throwaway account only if needed | Avoid burning your real account |
| yt-dlp args | `--sleep-requests 1.5 --sleep-subtitles 3 --extractor-args "youtube:player_client=tv,mweb"` | Tracks current best practices |

### 6.5 Estimated wall time

| Scenario | Throughput | 10,000 videos |
|---|---|---|
| Single home IP, recommended pacing, no blocks | ~720 videos/h | **~14 h** |
| Single home IP, occasional blocks (2× 30 m loss) | ~600/h | ~16–17 h |
| Single home IP, heavy blocks (forced multi‑night) | ~300/h effective | 2 nights × 16 h |
| Home IP + phone cellular IP (2 distinct externals) | ~1,200/h | **~8–9 h** |
| Home IP + friend's home SSH SOCKS proxy | ~1,200–1,400/h | ~7–8 h |

Plan as **one long overnight job (≈14–17 hours)**. Start at 18:00 local, expect completion by ~10:00 next morning, with a SQLite checkpoint that lets you resume cleanly if you abort or the script crashes. If you have two IPs available, run two scripts pointing at the same SQLite database (use `BEGIN IMMEDIATE` or row‑level "claim" with `UPDATE … WHERE status='pending' LIMIT 1 RETURNING video_id`) and finish in roughly half the time.

### 6.6 Pseudocode for the inner loop

```python
import random, time, sqlite3, json
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    RequestBlocked, IpBlocked, TooManyRequests, PoTokenRequired,
    TranscriptsDisabled, NoTranscriptFound, VideoUnavailable,
    AgeRestricted, CouldNotRetrieveTranscript,
)

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]
LANGS = ["en", "en-US", "en-GB", "a.en"]

def make_api():
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = random.choice(UA_POOL)
    s.headers["Accept-Language"] = "en-US,en;q=0.9"
    return YouTubeTranscriptApi(http_client=s)

def fetch_one(video_id):
    api = make_api()
    try:
        ft = api.fetch(video_id, languages=LANGS)
        return ("ok", ft.language_code, ft.is_generated, ft.to_raw_data(), None)
    except NoTranscriptFound:
        # try translation
        try:
            tl = api.list(video_id).find_transcript(LANGS).translate("en").fetch()
            return ("ok", "en-translated", True, tl.to_raw_data(), None)
        except Exception as e:
            return ("no_captions", None, None, None, str(e))
    except TranscriptsDisabled as e: return ("no_captions",  None,None,None,str(e))
    except VideoUnavailable    as e: return ("unavailable", None,None,None,str(e))
    except AgeRestricted       as e: return ("age_restricted",None,None,None,str(e))
    except PoTokenRequired     as e: return ("needs_pot",    None,None,None,str(e))
    except (RequestBlocked, IpBlocked, TooManyRequests) as e:
        raise BlockedError(str(e))
    except CouldNotRetrieveTranscript as e:
        return ("error", None, None, None, str(e))

class BlockedError(Exception): pass

BACKOFFS = [300, 900, 2700, 7200]  # 5m, 15m, 45m, 2h

def run(db_path):
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    consecutive_blocks = 0
    while True:
        row = con.execute(
            "SELECT video_id FROM transcripts "
            "WHERE status IN ('pending','blocked','error') AND attempts < 5 "
            "ORDER BY attempts ASC, RANDOM() LIMIT 1"
        ).fetchone()
        if row is None: break
        vid = row["video_id"]
        try:
            status, lang, gen, data, err = fetch_one(vid)
            consecutive_blocks = 0
            con.execute(
                "UPDATE transcripts SET status=?, language=?, is_generated=?, "
                "transcript=?, error_message=?, fetched_at=CURRENT_TIMESTAMP, "
                "attempts=attempts+1 WHERE video_id=?",
                (status, lang, gen, json.dumps(data) if data else None, err, vid),
            )
            con.commit()
        except BlockedError as e:
            consecutive_blocks += 1
            con.execute(
                "UPDATE transcripts SET status='blocked', error_message=?, "
                "attempts=attempts+1, last_attempt=CURRENT_TIMESTAMP "
                "WHERE video_id=?", (str(e), vid))
            con.commit()
            if consecutive_blocks >= len(BACKOFFS):
                print("Aborting for the night."); break
            time.sleep(BACKOFFS[consecutive_blocks-1])
            continue
        # normal inter-video delay
        time.sleep(random.uniform(3, 7))
        # longer pause every 500 successful fetches
        n_ok = con.execute("SELECT COUNT(*) c FROM transcripts WHERE status='ok'").fetchone()["c"]
        if n_ok and n_ok % 500 == 0:
            time.sleep(random.uniform(60, 120))
```

You then add a second pass that handles the `needs_pot` and `age_restricted` rows by shelling out to yt-dlp.

### 6.7 Operational tips

- **Test on 100 videos first.** A 30‑minute pilot will reveal whether your IP is already in YouTube's doghouse (e.g. if you've been doing this experimentally for weeks) or fresh.
- **Run on a wired (Ethernet) connection** for stability — Wi‑Fi reconnects mid‑run cause noisy intermittent failures that look like blocks.
- **Tail the log; don't watch a progress bar.** A noisy stream of `RequestBlocked` is your signal to abort, not "the bar is going slowly."
- **Don't run YouTube playback in the same browser** in parallel while the script runs — it adds requests to the same IP's budget. Watch videos on your phone instead.
- **Save raw transcripts plus normalized text.** Keep the `to_raw_data()` JSON (so you have timings) and also a flat `.txt` for downstream NLP — re‑fetching is expensive.
- **Mind your storage.** 10,000 transcripts is typically 100–500 MB; not large, but plan a directory layout (e.g. `transcripts/{video_id[0]}/{video_id}.json`) so you don't drop 10,000 files in one folder.
- **Update `yt-dlp` right before the run.** YouTube extractor regressions appear and disappear weekly; using last month's `yt-dlp` is the most common cause of "it worked yesterday."
- **Verify legal/TOS context** for your use case. YouTube's TOS prohibits automated access to content outside the official Data API, and the official Data API does not expose transcripts. Personal research, fair use, and small‑scale academic uses are typically tolerated but not licensed. This report is a technical analysis only and not legal advice.

## Summary

For 10,000+ YouTube transcripts on a free, single home IP, overnight: pin **youtube-transcript-api 1.2.4** as the primary fetcher, **yt-dlp (latest) + the free bgutil-ytdlp-pot-provider Docker** as the only‑when‑needed fallback, and **pytubefix** as a last‑ditch option. Throttle to **5 s ± 2 s between videos**, single worker, exponential backoff on `RequestBlocked`/`IpBlocked`/`TooManyRequests`, and use a **SQLite progress database** keyed on `video_id`. Expect roughly **14–17 hours of wall time** for 10,000 videos, with an 85–95% genuine success rate. Tor and free VPNs are not viable for YouTube in 2026; cellular tethering and a friend's residential SOCKS proxy are the only free ways to genuinely add IP diversity. The historical 2024–2025 breakage was solved by the v1.1.0 Innertube migration, but the underlying constraint — YouTube's per‑IP volume detection — has only tightened, so the discipline of slow, jittered, resumable, single‑worker fetching is what makes the job possible at all.