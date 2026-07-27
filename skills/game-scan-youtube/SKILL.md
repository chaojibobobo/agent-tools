---
name: game-scan-youtube
description: >
  Daily YouTube monitor for new mobile game gameplay videos in a target genre. Scans weighted
  channels via RSS feeds, finds videos uploaded in the last 24 hours, deduplicates against
  previously seen games, and delivers only genuinely new content. Pushes reports to Feishu/Lark.
  Use this skill whenever the user mentions game scouting, competitive intelligence, mobile game
  monitoring, new game discovery, gameplay video tracking, 竞品监控, 游戏调研, or wants to find
  gameplay videos of competing titles. Also use for "搜一下新游戏", daily game check, or any
  game market landscape query. This skill tracks channels and games across sessions to avoid
  showing the same content twice.
metadata:
  short-description: Scan and deduplicate fresh mobile-game gameplay videos
allowed-tools: Read, Write, Bash, WebSearch
---

# Game Scan YouTube — 24h Incremental Monitor

Daily scan for **new** gameplay videos uploaded in the last 24 hours. Never show the user something they've already seen.

## Phase 1 — Resolve Workspace and State

Keep mutable reports and state outside the installed skill directory.

Resolve the working directory in this order:

1. A path explicitly supplied by the user.
2. `GAME_SCAN_WORK_DIR`.
3. `~/.game-scan-youtube/workspace`.

Before the first scan, initialize it from the bundled snapshot:

```bash
scripts/init_workspace.sh
```

Or initialize a custom directory:

```bash
scripts/init_workspace.sh --dir /path/to/workspace
```

The bootstrap contains the last known `channels.json`, `seen_games.json`, and 18 historical reports from 2026-05-12 through 2026-06-16. Never edit `references/history/`; copy forward into the mutable workspace.

The user's request to run a scan authorizes report and state writes inside the resolved workspace. Before writing anywhere else, ask for approval and show the exact target.

### Persistent State

Two files in the resolved working directory:

- **`channels.json`** — weighted channel list (seed + discovered), each entry has `relevant_videos_7d` (int) tracking relevant videos published in the last 7 days, updated each scan
- **`seen_games.json`** — games already reported, with `first_seen`, `last_seen`, and `seen_videos`

Both are read at start, written at end. They persist across sessions.

### channels.json

```json
{
  "last_updated": "YYYY-MM-DD",
  "seed_channels": [
    {
      "id": "@channel-handle",
      "channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx",
      "name": "Channel Display Name",
      "url": "https://www.youtube.com/@channel-handle",
      "title_pattern": "Game Name: Genre Gameplay Mobile Android",
      "weight": 10,
      "tags": ["genre-tag-1", "genre-tag-2"],
      "note": "why this channel is valuable",
      "relevant_videos_7d": 0
    }
  ],
  "discovered_channels": []
}
```

`channel_id` (UC... format) is required for RSS feed access. Find it via: `curl -s "https://m.youtube.com/@handle" | grep -oE 'channel_id=UC[^"&]+' | head -1 | cut -d= -f2`. If a channel returns 404, set `channel_id: null` and demote to weight 1.

### seen_games.json

```json
{
  "last_updated": "YYYY-MM-DD",
  "games": {
    "Game Name": {
      "first_seen": "YYYY-MM-DD",
      "last_seen": "YYYY-MM-DD",
      "seen_videos": ["youtube-url-1"],
      "developer": "...",
      "status": "Early Access|CBT|Soft Launch|Launched|New",
      "tags": ["..."]
    }
  }
}
```

## Phase 2 — Collect Deterministic RSS Evidence

Run RSS collection in the parent shell before asking an agent to analyze content:

```bash
python3 scripts/collect_rss.py \
  --channels "$GAME_SCAN_WORK_DIR/channels.json" \
  --output "$GAME_SCAN_WORK_DIR/scan-input-YYYY-MM-DD.json"
```

This local JSON is the evidence boundary for the report. It records the exact 24-hour window, every successful or failed channel, and all feed entries in that window. A nested Codex or Claude process must not be expected to have network access.

If every RSS channel fails, stop with `BLOCKED` and do not create a report, update state, push to Feishu, or write a done stamp. Partial failure is `CONCERNS`: preserve failed-channel state and mark the report as degraded.

## Phase 3 — Analyze, Deduplicate, and Draft

Use `scan-input-YYYY-MM-DD.json`, `channels.json`, and `seen_games.json` as local inputs.

```
1. Read scan-input-YYYY-MM-DD.json + channels.json + seen_games.json
2. Broad daily analysis: sort local RSS candidates by upload time
   - Apply a broad mobile-game candidate filter before the stricter genre ranking
   - Duration check: read video page for candidates, prefer >= 10 min but keep high-signal 6-10 min first-look videos
   - On RSS error/empty recorded in the input: preserve channel state (no health penalty)
3. Expand discovery beyond existing channels:
   - Run targeted YouTube searches for fresh mobile gameplay queries
   - Search recent Google Play / App Store launch and early-access signals
   - Use cross-ref results to find new relevant channels
4. For each new game found:
   - YouTube cross-ref (web search): find coverage on other channels
   - Google Play cross-ref: extract downloads, rating, last update
   - Channel discovery: evaluate new channels for discovered_channels
5. Dedup: filter out games/videos already in seen_games.json
   - New game → full entry with all details
   - Known game → only include video URLs not in seen_videos
6. Generate markdown report (newest upload first)
7. Do not update state or publish yet
```

Expansion search is optional in headless automation because the nested agent may have no network. When it is unavailable, clearly label the run RSS-only rather than fabricating search coverage.

## Phase 4 — Validate, Commit, and Publish

Advance through these gates in order:

```text
report-only → machine validation → Feishu dry-run → state commit → real send → receipt validation
```

Commands:

```bash
python3 scripts/validate_run.py --date YYYY-MM-DD --dir "$GAME_SCAN_WORK_DIR"
python3 scripts/push_feishu.py --date YYYY-MM-DD --dir "$GAME_SCAN_WORK_DIR" --dry-run
python3 scripts/commit_state.py --date YYYY-MM-DD --dir "$GAME_SCAN_WORK_DIR"
python3 scripts/push_feishu.py --date YYYY-MM-DD --dir "$GAME_SCAN_WORK_DIR"
python3 scripts/validate_run.py \
  --date YYYY-MM-DD \
  --dir "$GAME_SCAN_WORK_DIR" \
  --require-push-receipt
```

Only the final command returning `COMPLETE` permits `.run-stamps/YYYY-MM-DD.done`. A child process exit code, report file existence, or Feishu API request alone is not completion evidence. The real push writes `receipts/YYYY-MM-DD.feishu.json` with the returned message ID.

## Dedup Rules

- **Game already in seen_games.json?**
  - Check its `seen_videos` list
  - Only include video URLs NOT already in that list
  - Mark as "update" not "new discovery"
- **Game NOT in seen_games.json?**
  - This is a genuinely new find — full entry with all details
- **Video URL already in seen_games?** Skip it entirely

## Scan Strategy (24h window)

Only look for videos uploaded **today or yesterday** for the daily report. Use a 7-day window only for channel health, cross-reference strength, and quiet-day summaries.

Daily coverage goal: maximize useful recall first, then rank by relevance. Missing a new mobile strategy game is worse than listing a borderline candidate with a clear note.

Before doing network work, check whether `WORK_DIR/YYYY-MM-DD.md` already exists and is non-empty for the requested date. If it exists and the user did not explicitly ask to rerun, summarize that the daily report already exists — but still ensure the Feishu push has been attempted (the cron fallback handles push retries via `--force-push`).

### Primary: YouTube RSS feeds

Each channel in channels.json has a `channel_id` (UC... format). Fetch the RSS feed to get structured video data with precise timestamps — no JS rendering, no rate limits, no cookie walls.

**URL format:** `https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}`

Each RSS feed returns the latest 15 videos as XML with:
- `<published>` — exact upload timestamp (ISO 8601)
- `<title>` — video title
- `<yt:videoId>` — video ID for URL construction
- `<media:statistics views="...">` — view count
- `<media:description>` — full description (for genre/size extraction)

**Fetch method:** Use `curl` via Bash (not webReader) to avoid IncompleteRead errors from chunked transfer encoding. Python's `urllib` can also fail on large feeds — `curl -s --max-time 30` is the most reliable.

**Filter pipeline:**
1. Parse XML, extract all `<entry>` elements
2. Keep only entries with `<published>` within last 24h
3. Keep broad mobile-game candidates first: Android, iOS, mobile, gameplay, walkthrough, first look, new game, early access, CBT, soft launch, global launch, Google Play, App Store
4. Rank higher when title/description includes strategy, SLG, 4X, RTS, survival, base-building, alliance, PvP, war, kingdom, empire, zombie, post-apocalyptic, tower defense, city builder, civilization, troops, heroes + base/map language
5. Exclude only obvious non-target content: PC/console-only, shorts, trailers without gameplay, racing, sports, dress-up, match-3, pure idle, pure anime RPG/gacha with no strategy/base layer
6. For remaining videos, check duration via video page read. Prefer >= 10 min. Keep 6-10 min videos if the title says first look/new game/CBT/soft launch or the game has strong store/cross-channel signal.

**Adding a new channel:** When a new channel is added to channels.json, find its `channel_id` by reading `https://m.youtube.com/@handle` and extracting the UC... ID from page source (`grep -oP 'channel_id=UC[^"&]+'`).

### Channel Growth Rule

After the daily scan completes, optionally supplement the channel list:

1. Count current channels in `channels.json`
2. If below 50, attempt to add up to **5-10 new high-quality candidate channels** using:
   - User-provided channel names/handles/URLs
   - Cross-ref searches on games found during today's scan
   - Genre-specific YouTube channel searches (SLG, RTS, 4X, survival strategy)
   - Top-list / compilation video creator extraction
3. Each new channel must have a **verified `channel_id`** with working RSS feed
4. New channels start at weight 2-3 in `discovered_channels`
5. **Non-blocking:** channel expansion failure must never prevent the daily report or Feishu push. If discovery queries fail (rate limit, timeout), skip expansion and proceed normally.

Channel growth is opportunistic, not a gate. The daily scan always runs regardless of channel count.

### Expansion Search: recent YouTube discovery

After RSS scanning, use the available web search tool for targeted queries that catch games from channels not yet in `channels.json`. Use date terms for today/yesterday when useful.

Minimum daily query set:
```
site:youtube.com/watch mobile strategy game gameplay android
site:youtube.com/watch new mobile game gameplay android
site:youtube.com/watch SLG gameplay android
site:youtube.com/watch 4X strategy mobile gameplay
site:youtube.com/watch base building survival strategy android gameplay
site:youtube.com/watch soft launch mobile strategy gameplay
site:youtube.com/watch CBT mobile strategy gameplay
```

For each credible result:
- Extract video URL, channel, upload date, and game name.
- Dedup against `seen_games.json`.
- If the channel repeatedly appears with target-genre coverage, resolve its `channel_id` and add it to `discovered_channels`.
- Do not let search expansion replace RSS. RSS is the stable backbone; search expansion is the recall booster.

### Store Expansion: launch signals

For candidate games and quiet days, search store signals:
```
site:play.google.com/store/apps/details strategy mobile game early access
site:play.google.com/store/apps/details 4X strategy android
site:apps.apple.com strategy game soft launch
```

Use store results to enrich entries and to discover exact game names that can be searched back on YouTube.

### Fallback 1: web search (cross-reference)

When RSS finds a potential new game, use web search to verify coverage on other channels:
```
"[game name]" gameplay mobile android site:youtube.com
```
This also serves as channel discovery — if a new channel appears in results covering multiple games in your genre, add it to `discovered_channels`.

### Fallback 2: Channel page read (duration check)

RSS feeds don't include video duration. For candidate videos that pass the genre keyword filter, read the individual video page to check duration ≥ 10 min:
- Open the mobile YouTube URL with the available browser/web tool
- Or curl + grep for `"lengthSeconds":"..."` in page source

### Batch processing

- Sort channels by weight descending, process in batches of 5
- Seed channels (weight 9-10) always in first batch
- Normal day: process all channels with weight >= 3. Skip weight 1-2 unless time/budget remains or the channel is newly discovered and needs validation. Skip weight 0 (bench).
- **Quiet Day full-channel scan (two triggers):**
  1. **Same-day re-scan:** if today's normal scan (weight ≥ 3) produces zero new games (Quiet Day), immediately run a second pass over **all channels including weight 1-2 and weight 0 (bench)**. This catches anything the normal scan missed.
  2. **Next-day pre-scan:** if the previous day was a Quiet Day, today's initial scan already covers **all channels** (weight 0-10) instead of just weight ≥ 3. No second pass needed unless today also turns out quiet.
  - In both cases, merge results from both passes into a single report. Do not produce two reports.
- No pacing needed between RSS requests (no rate limiting)
- If web search is used heavily, cap expansion queries first, then cross-ref only the strongest candidates to avoid spending the budget on weak leads.
- If RSS returns empty/errors for a channel: skip it, no health penalty
- Update `relevant_videos_7d` for each channel after scanning

### Google Play signal

For new games, extract store metrics:
```
"[game name]" site:play.google.com
```
Extract: download count, rating, last update date. A sudden spike in downloads = scaling test signal.

### Channel discovery

If a cross-ref search reveals a new channel covering multiple games in your target genre:
1. Check if it's already in channels.json
2. Find its `channel_id` via `curl -s https://m.youtube.com/@handle | grep -oE 'channel_id=UC[^"&]+' | head -1 | cut -d= -f2`
3. Evaluate: how many relevant videos in its RSS feed? Consistent title pattern?
4. Add to `discovered_channels` with `channel_id`, `relevant_videos_7d: 0` and appropriate initial weight

## Weight System

| Weight | Meaning | Scan frequency |
|--------|---------|---------------|
| 9-10 | Seed channels (user-confirmed) | Every run, first batch |
| 7-8 | High-quality, frequent content | Every run |
| 5-6 | Regular content | Every run |
| 3-4 | Occasional | Every run (light) |
| 1-2 | Needs validation | Quiet days + full audits only |
| 0 | Bench/substitute | Quiet days + full audits only |

Weight adjustments per run:
- Channel has ≥1 new relevant video this run → weight +1 (max 8)
- User explicitly approves a channel → promote to seed, weight 10

`relevant_videos_7d` is recalculated each scan: count how many of the channel's videos found in this run fall within the rolling 7-day window (not cumulative — reset and recount every run). This means a channel that was active last week but silent this week will correctly decay to 0.

Channel health (based on `relevant_videos_7d`, updated each run):
- `relevant_videos_7d` ≥ 5 → healthy, maintain weight
- `relevant_videos_7d` 1-4 → watching
- `relevant_videos_7d` = 0 AND weight > 5 → demote -1 (min 1), flag for user review

**Important:** channels skipped by batch processing (429/empty) do NOT update `relevant_videos_7d` and do NOT participate in health calculations. Only channels whose pages were actually read get updated.

If repeated `429`, `403`, or upstream `5xx` responses prevent verification, stop retrying the same source in the current run. Mark the report as degraded, preserve the previous state for unverified channels, and present recharge, source change, lower frequency, or pause as explicit next options. A started cron job is not evidence of a completed scan.

## Output Format

Save to `WORK_DIR/YYYY-MM-DD.md`.

```markdown
# Game Scout — YYYY-MM-DD（24h Update）

## New Games

### HH:MM upload | [Game Name] — [hook]
- [Video Title](youtube-url) — Channel · Duration · Views

**Status:** Early Access / CBT / Soft Launch / Just Launched
**Priority:** Focus / Track
**Developer:** ...
**Platform:** Android / iOS / Both
**What you'll see:** [2-3 sentences gameplay description]
**Download:** Google Play / App Store URL (if available)
**Store signal:** X downloads · ★Y.Z · updated MM-DD
**Cross-ref:** X channels covered this today

---

## New Videos for Known Games

### [Game Name] — new video
- [Video Title](youtube-url) — Channel · Duration · Views · uploaded HH:MM
[Brief note on what's new in this video compared to previous ones]

---

## What They Are Playing
1. [Today’s most important shared mechanic or product pattern]
2. [Second pattern, backed by named games]
3. [Optional decision rule: what deserves continued attention]

## Channel Updates
[New channels discovered, weight changes]

## Quiet Day
[If nothing new found — say so. Don't pad the report.]
```

### Empty day handling

If no new videos found in 24h, write a Quiet Day report with a **7-day summary** section. The quiet day push becomes an opportunity to deliver a weekly retrospective — this keeps the daily push valuable even when there's nothing new.

```markdown
# Game Scout — YYYY-MM-DD（24h Update）

## Quiet Day
No new gameplay videos found in the last 24 hours.

### 7-Day Summary (MM-DD ~ MM-DD)

#### Overview
- X new games tracked
- Y videos monitored
- Z channels scanned

#### Hot Games (sorted by video count)
| Game | Developer | Videos | Signal |
|------|-----------|--------|--------|
| Game Name | Dev | N | 🔴/🟡 |

Signal: 🔴 = 3+ videos (strong testing/launch signal), 🟡 = 1-2 videos

#### Trend Themes
- [Observable trend 1, e.g. "survival+strategy hybrid concentration (Game A, B, C)"]
- [Observable trend 2]
- ...

#### By Discovery Date
- MM-DD: [list of games first seen that day]
- MM-DD: [list of games]

### Channels scanned today
[List channels checked]
```

**How to build the 7-day summary:** Read seen_games.json, filter games with `first_seen` in the last 7 days, sort by `len(seen_videos)` descending. Identify trends by grouping games by shared themes (developer region, genre tags, gameplay mechanics).

Don't fabricate content. A quiet day is honest data — the 7-day summary is derived from what was actually tracked.

### Output expression

The local Markdown report preserves full evidence. Feishu uses **Decision Card v3** as a one-screen decision surface:

1. **价值结论** — answer whether today deserves attention in no more than 2 short sentences / 80 Chinese characters.
2. **必看** — expand 1 high-signal game by default; expand at most 2 only when both are explicitly `Priority: Focus`.
3. **为什么看** — show one strongest evidence line: multi-channel coverage, long-form gameplay, test/launch status, or store growth.
4. **核心机制** — compress gameplay into at most 3 mechanism nodes joined by `→`.
5. **补充** — one line per weaker finding, with no repeated developer/platform/store field list.
6. **趋势一句话** — keep one cross-game judgment only.
7. **观察与更新** — show counts, or at most 2 names when necessary; never inflate the confirmed-new count.

Card header semantics:

- `orange`: at least one clear focus game.
- `blue`: confirmed additions but no focus.
- `grey`: Quiet Day.
- `red`: a separate blocked/failure notification only; never a daily report.

Use up to 3 header tags for `必看 / 补充 / 观察`. Keep normal visible card copy within about 500 Chinese characters and 15 text lines. Use buttons or named links instead of bare URLs.

Do not give every game equal space. Do not copy the full `What you'll see`, all three takeaways, or repetitive metadata into chat. The question is: “Is today worth attention, and which game matters most, why?”

## Sorting

- **Primary sort**: video upload time within 24h, newest first
- Include upload time (HH:MM) when available

## What counts

Include: CBT/soft launch/early access/just launched, actual gameplay footage, survival-strategy hybrids, RTS-strategy hybrids, 4X, SLG, war/empire/kingdom/civilization builders, base building, map, alliance, resource mgmt, troops, PvP. Size > 500MB = more likely real game in your genre.

Exclude: established titles unless major update, pure RPG/survival without strategy layer, PC-only, trailers without gameplay.

**Duration filter:** prefer videos >= 10 minutes. Do not automatically discard 6-10 minute videos when they are first-look, CBT, early-access, soft-launch, or cross-channel signals. Skip shorts and thin clips.

## IM Push

The push script auto-reads the day's report + `seen_games.json` to build one Feishu interactive decision card. No manual data entry is needed.

```bash
# Normal push
python3 scripts/push_feishu.py --date YYYY-MM-DD --dir "$GAME_SCAN_WORK_DIR"

# Dry run (print to stdout, don't send)
python3 scripts/push_feishu.py --date YYYY-MM-DD --dir "$GAME_SCAN_WORK_DIR" --dry-run
```

The normal send uses `interactive`. If Feishu rejects the card before returning any `message_id`, the script may retry once with a compact `post` carrying the same hierarchy. After any `message_id` is returned, fallback is forbidden to prevent duplicates. The receipt records `message_format` and `fallback_used`.

Credentials are read from environment variables, not hardcoded:
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_USER_OPEN_ID`

Store them in `~/.game-scan-youtube/.env` for auto-loading:
```
FEISHU_APP_ID=your_app_id
FEISHU_APP_SECRET=your_app_secret
FEISHU_USER_OPEN_ID=your_open_id
```

Set the credential file to owner-only permissions:

```bash
chmod 600 ~/.game-scan-youtube/.env
```

## Daily Scheduling

When the user asks for scheduling, use the current environment's automation mechanism. Recommended time: 11:00 local time. The task should call `scripts/run_daily_once.sh`, which uses a done stamp and lock to prevent duplicate pushes.

For a manual or cron-compatible run:

```bash
scripts/run_daily_once.sh
```

Do not infer that the historical schedule is still active. Verify the current automation state before claiming that daily scanning is enabled.

## Key Principles

- **Incremental only**: never show previously seen content. seen_games.json is the truth.
- **24h window**: focus on what's new RIGHT NOW, not what was new last week.
- **RSS-first**: fetch RSS feeds for structured video data, web search for cross-ref only.
- **Batch with pacing**: 5 channels per batch. Cross-reference search may 429 — space queries out and stop repeated failed retries.
- **Automated push**: script auto-reads files and pushes, `--dry-run` for testing.
- **Cross-reference**: same game on 2+ channels in same day = hot new test.
- **Honest**: quiet day = quiet day. Don't pad.
- **Judgment first**: answer “what matters today” before listing discoveries.
- **Signal weighted**: expand strong findings, compress weak ones, and never let a watchlist item inflate the new-game count.
- **Concise**: keep full evidence in Markdown; Feishu is a daily decision digest, not a research report.

## Historical Evidence

Read historical material only when it helps establish continuity, prior candidates, or known failure modes:

- `references/history/reports/` — 18 daily reports, 2026-05-12 through 2026-06-16.
- `references/history/investigations/` — developer investigation and batch data.
- `references/history/operations/` — project memory and budget/failure routing.
- `references/history/README.md` — provenance, inventory, and migration boundary.

The absence of a dated report after 2026-06-16 means there is no local report evidence for that date; never fill that gap from cron logs or assumptions.

## Recommended Next Step

After a successful manual run, inspect the report, `scan-input-YYYY-MM-DD.json`, and the Feishu receipt together. Only then enable or resume daily scheduling.
