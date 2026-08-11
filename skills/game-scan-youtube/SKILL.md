---
name: game-scan-youtube
description: >
  Incremental intelligence radar for new mobile 4X SLG gameplay and early product tests.
  Maintains a dynamically weighted YouTube channel pool, falls back from RSS to channel pages,
  independently monitors six early-launch countries, validates structured 4X evidence, and
  delivers only genuinely new core-genre content with first-hand YouTube sources.
  Use this skill whenever the user mentions game scouting, competitive intelligence, mobile game
  monitoring, new game discovery, gameplay video tracking, 竞品监控, 游戏调研, or wants to find
  gameplay videos of competing titles. Also use for "搜一下新游戏", daily game check, or any
  4X SLG market landscape query. This skill tracks channels and games across sessions to avoid
  showing the same content twice.
metadata:
  short-description: Scan fresh mobile 4X SLG gameplay with a strict genre gate
argument-hint: "[today | YYYY-MM-DD]"
user-invocable: true
allowed-tools: Read, Write, Bash, WebSearch
---

# Game Scan YouTube — Dual-Radar Incremental Monitor

Scan for **new mobile 4X SLG** gameplay and early product-test signals since the last successful run. Never show the user something they've already seen, and never substitute broad strategy adjacency for core-genre fit.

## Phase 1 — Resolve Workspace and State

Keep mutable reports and state outside the installed skill directory.

Resolve the working directory in this order:

1. A path explicitly supplied by the user.
2. `GAME_SCAN_WORK_DIR`.
3. Fixed default: `/Users/bobo/Codexspace/tools/game-scan-youtube`.

For this installation, always continue in that fixed default unless the user explicitly overrides it. During an interactive manual scan, the current agent executes the phases directly; do not start a nested `codex exec`. `run_scan.sh` is retained only for an explicitly requested non-interactive runner.

Before the first scan, initialize it from the bundled snapshot:

```bash
scripts/init_workspace.sh
```

Or initialize a custom directory:

```bash
scripts/init_workspace.sh --dir /path/to/workspace
```

The bootstrap contains the last known channel/game snapshots, empty intelligence states, and 18 historical reports from 2026-05-12 through 2026-06-16. Never edit `references/history/`; copy forward into the mutable workspace.

The user's request to run a scan authorizes report and state writes inside the resolved workspace. Before writing anywhere else, ask for approval and show the exact target.

### Persistent State

Five state files in the resolved working directory:

- **`channels.json`** — dynamic channel pool, `intelligence_score`, weight, evidence events, and separate `source_health`.
- **`seen_games.json`** — published games with canonical name, aliases, package IDs, store URLs and seen videos.
- **`candidate_history.json`** — all reviewed Core, Pending, Reject and Product Lead entities, including recheck conditions.
- **`product_radar.json`** — independent six-country product observations and metrics.
- **`scan_state.json`** — last successfully committed evidence window; collection alone never advances it.

All five are read at start and written only after validation. They persist across sessions.

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
      "intelligence_score": 0,
      "intelligence_events": [],
      "source_health": {"status": "unknown"}
    }
  ],
  "discovered_channels": []
}
```

`channel_id` (UC... format) is required for RSS feed access. Find it via: `curl -s "https://m.youtube.com/@handle" | grep -oE 'channel_id=UC[^"&]+' | head -1 | cut -d= -f2`. A transient RSS/page failure updates `source_health` only and never changes intelligence weight. Set `channel_id: null` only after the identifier is independently verified as permanently invalid.

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

## Phase 2 — Collect the Two Independent Radars

### Channel Radar

Run RSS collection in the parent shell before asking an agent to analyze content:

```bash
python3 scripts/collect_rss.py \
  --channels "$GAME_SCAN_WORK_DIR/channels.json" \
  --output "$GAME_SCAN_WORK_DIR/scan-input-YYYY-MM-DD.json" \
  --state "$GAME_SCAN_WORK_DIR/scan_state.json" \
  --channel-page-fallback
```

This local JSON records the gap-aware window, every source result, exact RSS entries, and relative-time page fallback entries. RSS uses bounded retries. A failed RSS source falls back to its `/videos` page; **channel-page fallback** is discovery evidence only and must keep `time_precision: relative` until a watch page provides an exact upload date.

Coverage gates are machine-enforced after fallback. Effective overall or priority-channel coverage below 60% is `BLOCKED`; any fallback, RSS failure, backfill cap, or weak exact-time coverage is `degraded`; fully healthy RSS coverage is `complete`. `BLOCKED` means no report, state commit, push, receipt, or done stamp.

### Product Radar

Prepare the country-specific work manifest:

```bash
python3 scripts/prepare_product_radar.py \
  --date YYYY-MM-DD \
  --output "$GAME_SCAN_WORK_DIR/product-radar-input-YYYY-MM-DD.json"
```

Product Radar monitors exactly **MY / ID / PH / GB / TH / CA**: Malaysia, Indonesia, Philippines, United Kingdom, Thailand, and Canada. Open the manifest's region-qualified Google Play (`gl=COUNTRY`) and App Store (`country=COUNTRY`) endpoints first; generic web-search snippets cannot prove regional availability. For each country, record checked, failed, or not-run status and localized source URLs. Overall `complete` means all six checked, `degraded` means partial/failed coverage, and `not_run` means all six not run. Capture first listing, pre-registration to available, region expansion, removal, package/store ID, developer and observed time.

Store-only results remain internal `Product Lead` entries. Reverse-search exact title, package ID and developer on YouTube. Only candidates with first-hand gameplay and a passing strict 4X gate may appear in the report. Product Radar has its own coverage and yield metrics; **target-country coverage never affects channel weight**.

## Phase 3 — Analyze, Deduplicate, and Draft

Use both radar inputs and all persistent state files.

```
1. Read scan input + product-radar manifest + channels + seen games + candidate history + product radar + scan state
2. Broad discovery pass: sort exact RSS candidates by upload time and keep page-relative candidates explicitly approximate
   - Broad discovery may retain uncertain candidates temporarily, but nothing enters the report before the strict 4X gate
   - Duration check: read video page for candidates, prefer >= 10 min but keep high-signal 6-10 min first-look videos
   - On RSS error/empty recorded in the input: preserve channel state (no health penalty)
3. Product Radar pass for MY / ID / PH / GB / TH / CA:
   - Execute only searches supported by actual network evidence
   - Use exact title / package ID / developer to reverse-search YouTube
   - Store source-less candidates as Product Lead; never publish them
   - Use verified videos to discover candidate channels
4. For each candidate game:
   - Score the four mandatory evidence pillars and assign Core / Pending / Reject
   - YouTube cross-ref (web search): find coverage on other channels
   - Google Play cross-ref: extract downloads, rating, last update
   - Channel discovery: evaluate new channels for discovered_channels
5. Entity dedup: package ID first, then store URL, then canonical name + aliases
   - New game → full entry with all details
   - Known game → only include video URLs not in seen_videos
6. Generate `candidate-ledger-YYYY-MM-DD.json` with every Core, Pending, Reject and Product Lead, structured pillars, videos, aliases, package IDs, country observations and recheck conditions
7. Generate a `4X-SLG-STRICT-v1` markdown report from publishable ledger entries
8. Do not update state or publish yet
```

When country/product search is unavailable, record failed/not-run per country. Never fabricate complete Product Radar coverage.

### Candidate Ledger Gate

The ledger is the machine-readable judgment truth for new runs. `Core` must structurally prove Base / City and World Map / Territory plus Resource Economy or Army / Alliance War. `Pending` names the missing pillar. `Reject` and `Product Lead` remain local. Country observations must store `region_verified: true` and a `region_source_url` localized to the same country; otherwise validation fails. Every published report entry must map to one ledger entity and use a YouTube URL stored on that same entity.

### Strict 4X SLG Genre Gate

The publishing profile is `4X-SLG-STRICT-v1`. Broad discovery is allowed; broad publication is not.

Score gameplay evidence against four pillars:

1. **Base / City** — a persistent player settlement, city, kingdom, shelter, or equivalent that is built and developed over time.
2. **World Map / Territory** — a persistent strategic map with exploration, territorial expansion, occupation, marches, nodes, cities, or other map control.
3. **Resource Economy** — production, gathering, logistics, technology, construction queues, population, or resource conversion that supports expansion.
4. **Army / Alliance War** — controllable armies or marches, conquest, alliance coordination, territory war, or persistent PvP conflict.

Classification:

- **Core** — both `Base / City` and `World Map / Territory` are directly evidenced, plus at least one of the other two pillars. This is the only status allowed in `New Games` and `New Videos for Known Games`.
- **Pending** — there is concrete evidence that the product is probably a persistent 4X SLG, but the available gameplay does not yet prove one required pillar. It may appear only in `Watchlist`, with the missing pillar named.
- **Reject** — either mandatory anchor is absent, fewer than three pillars are evidenced, or the product's primary loop is a different genre. Keep it only in the local exclusion reasoning; never send it as a discovery, supplement, watchlist item, or known-game update.

Hard counterexamples:

- Auto-Battler, Merge battler, deck battler, three-minute lane battler, match-based RTS, or arena PvP without a persistent city and world territory.
- Roguelike TD, hero defense, single-screen tower defense, crowd runner, or action survival even when upgrades or a decorative base exist.
- Idle RPG, hero collector, MMORPG, simulation/tycoon, settlement builder without territorial conflict, or base defense without a strategic world map.
- A Strategy/MMORTS store label is not genre evidence. `kingdom`, `empire`, `war`, `civilization`, `zombie`, package size, video duration, channel count, and high views are discovery clues only.

Evidence strength and genre fit are separate axes. Multiple channels and long videos can increase confidence in a `Core` classification; they can never promote `Pending` or `Reject` into `Core`.

Priority rules:

- `Focus` — Core 4/4, with two independent gameplay sources or one official/source-complete video that visibly demonstrates the persistent loop.
- `Track` — Core 3/4 or Core 4/4 with limited launch evidence.
- `Watchlist` — Pending only. Name the missing pillar and do not count it as a new game.

## Phase 4 — Validate, Commit, and Publish

Advance through these gates in order:

```text
report-only → machine validation → Feishu dry-run → state commit → real send → receipt validation
```

Commands:

```bash
python3 scripts/validate_run.py --date YYYY-MM-DD --dir "$GAME_SCAN_WORK_DIR" --require-intelligence-ledger
python3 scripts/push_feishu.py --date YYYY-MM-DD --dir "$GAME_SCAN_WORK_DIR" --dry-run
python3 scripts/commit_state.py --date YYYY-MM-DD --dir "$GAME_SCAN_WORK_DIR"
python3 scripts/push_feishu.py --date YYYY-MM-DD --dir "$GAME_SCAN_WORK_DIR"
python3 scripts/validate_run.py \
  --date YYYY-MM-DD \
  --dir "$GAME_SCAN_WORK_DIR" \
  --require-intelligence-ledger \
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

## Scan Strategy (gap-aware window)

Start at `scan_state.json.last_successful_scan_end`. If no committed cursor exists, use 24 hours. Cap automatic backfill at 72 hours and mark older gaps `backfill_limited: true`; never call them complete historical coverage. Use a 7-day view only for summaries and a 30/90-day view for channel intelligence.

Daily coverage goal: maximize recall during discovery, then maximize precision at publication. A Quiet Day is better than sending a non-4X game. Missing a broad strategy-adjacent title is acceptable; admitting one as 4X SLG is a classification failure.

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
2. Keep only entries with `<published>` inside the resolved gap-aware evidence window
3. Keep broad mobile-game candidates temporarily: Android, iOS, mobile, gameplay, walkthrough, first look, new game, early access, CBT, soft launch, global launch, Google Play, App Store
4. Search title/description for combinations of persistent city/base, world map/territory, resource economy, armies/marches, alliance war, occupation, gathering, technology, and expansion
5. Apply the four-pillar gate to gameplay and detailed source descriptions; keywords alone never pass the gate
6. Reject genre counterexamples before duration/cross-channel ranking. Then check duration and source strength only for Core or narrowly Pending candidates.

**Adding a new channel:** When a new channel is added to channels.json, find its `channel_id` by reading `https://m.youtube.com/@handle` and extracting the UC... ID from page source (`grep -oP 'channel_id=UC[^"&]+'`).

### Channel Growth Rule

After the daily scan completes, optionally supplement the channel list:

1. Count current channels in `channels.json`
2. If below 50, attempt to add up to **5-10 new high-quality candidate channels** using:
   - User-provided channel names/handles/URLs
   - Cross-ref searches on games found during today's scan
   - Genre-specific YouTube channel searches (mobile 4X SLG, world-map SLG, alliance-war strategy)
   - Top-list / compilation video creator extraction
3. Each new channel must have a **verified `channel_id`** with working RSS feed
4. New channels start at weight 2-3 in `discovered_channels`
5. **Non-blocking:** channel expansion failure must never prevent the daily report or Feishu push. If discovery queries fail (rate limit, timeout), skip expansion and proceed normally.

Channel growth is opportunistic, not a gate. The daily scan always runs regardless of channel count.

### Expansion Search: recent YouTube discovery

After RSS scanning, use the available web search tool for targeted queries that catch games from channels not yet in `channels.json`. Use date terms for today/yesterday when useful.

Minimum daily query set:
```
site:youtube.com/watch mobile 4X SLG gameplay android world map
site:youtube.com/watch new SLG gameplay android alliance territory
site:youtube.com/watch 4X strategy mobile gameplay city world map
site:youtube.com/watch base building world map alliance war mobile gameplay
site:youtube.com/watch soft launch 4X SLG mobile gameplay
site:youtube.com/watch CBT SLG mobile world map gameplay
```

For each credible result:
- Extract video URL, channel, upload date, and game name.
- Dedup against `seen_games.json`.
- If the channel repeatedly appears with target-genre coverage, resolve its `channel_id` and add it to `discovered_channels`.
- Do not let search expansion replace RSS. RSS is the stable backbone; search expansion is the recall booster.

### Product Radar: six-country launch signals

Use `product-radar-input-YYYY-MM-DD.json` to monitor MY / ID / PH / GB / TH / CA separately. Start with its localized store endpoints, use the country-specific web queries only as discovery fallback, then record per-country status and source URLs in `candidate-ledger-YYYY-MM-DD.json`. Use store results to identify exact title, package ID and developer, then reverse-search those identifiers on YouTube. Store-only findings remain `Product Lead` and cannot enter the user-facing report.

### Fallback 1: web search (cross-reference)

When RSS finds a potential new game, use web search to verify coverage on other channels:
```
"[game name]" gameplay mobile android site:youtube.com
```
This also serves as channel discovery — if a new channel appears in results covering multiple games in your genre, add it to `discovered_channels`.

### Fallback 2: Channel page read

The collector automatically uses failed-channel `/videos` pages as a recall fallback. These entries have relative time only. For candidates that pass broad discovery, read the individual watch page to obtain exact upload date, duration and description:
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
- If RSS returns empty/errors for a channel: try the channel-page fallback and update `source_health`; do not change intelligence weight
- Append structured Core / Pending / Reject evidence events, then recompute the 30-day intelligence score; preserve 90 days of raw events

### Store signal enrichment

For candidates discovered by either radar, extract store metrics:
```
"[game name]" site:play.google.com
```
Extract: country, availability, download count, rating, last update date and package ID. Treat a sudden download spike as a scaling-test clue, not proof of 4X fit and not a channel-weight input.

### Channel discovery

If a cross-ref search reveals a new channel covering multiple games in your target genre:
1. Check if it's already in channels.json
2. Find its `channel_id` via `curl -s https://m.youtube.com/@handle | grep -oE 'channel_id=UC[^"&]+' | head -1 | cut -d= -f2`
3. Evaluate: how many relevant videos in its RSS feed? Consistent title pattern?
4. Add to `discovered_channels` with verified `channel_id`, empty intelligence history, `source_health: unknown`, and probation weight 2-3

## Channel Intelligence and Weight System

Dynamic channel maintenance remains the primary discovery mechanism. The score is derived only from the channel's target-intelligence performance:

- **40% strict 4X yield** — `(Core + 0.5 × Pending) / reviewed` over the recent window.
- **25% early discovery** — share of valid candidates where the channel supplied the earliest exact source.
- **20% unique discovery** — share of valid candidates supplied only by this channel in the run.
- **15% evidence quality** — average 1–5 quality of first-hand gameplay evidence.

`intelligence_score` and `source_health` are separate. RSS/page availability updates `source_health` only. Country/store observations are not read by the scoring function. Fewer than three reviewed events remains probation and cannot auto-promote above weight 4. User-confirmed seed channels retain manual weight 10; other channels derive weights 1–9 from score bands.

| Weight | Meaning | Scan frequency |
|--------|---------|---------------|
| 10 | User-confirmed seed | Every run, first batch |
| 8-9 | Proven early strict-4X sources | Every run |
| 6-7 | Repeated useful Core/Pending evidence | Every run |
| 3-5 | Broad but occasionally useful / probation | Every run or rotation |
| 1-2 | Sustained low target yield | Quiet days + full audits only |
| 0 | Bench/substitute | Quiet days + full audits only |

If repeated `429`, `403`, or upstream `5xx` responses prevent verification, stop retrying the same source in the current run. Mark the report as degraded, preserve the previous state for unverified channels, and present recharge, source change, lower frequency, or pause as explicit next options. A started cron job is not evidence of a completed scan.

## Output Format

Save to `WORK_DIR/YYYY-MM-DD.md`.

```markdown
# Game Scout — YYYY-MM-DD（Incremental Update）

> **Scan Profile:** 4X-SLG-STRICT-v1

## New Games

### HH:MM upload | [Game Name] — [hook]
- [Video Title](youtube-url) — Channel · Duration · Views

**Status:** Early Access / CBT / Soft Launch / Just Launched
**Priority:** Focus / Track
**4X Fit:** Core — Base / City + World Map / Territory + [Resource Economy and/or Army / Alliance War]
**4X Evidence:** Base/City=[evidence]; World Map/Territory=[evidence]; Resource Economy=[evidence or missing]; Army/Alliance War=[evidence or missing]
**Package ID:** ...
**Aliases:** ...
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
[**4X Fit:** Core and the update-specific 4X evidence]
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

If no new videos are found in the resolved evidence window, write a Quiet Day report with a **7-day summary** section. The quiet day push becomes an opportunity to deliver a weekly retrospective — this keeps the daily push valuable even when there's nothing new.

```markdown
# Game Scout — YYYY-MM-DD（Incremental Update）

> **Scan Profile:** 4X-SLG-STRICT-v1

## Quiet Day
No new gameplay videos found in the resolved evidence window.

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
3. **为什么看** — lead with the 4X evidence chain; use multi-channel coverage, duration, or store growth only as secondary confidence.
4. **核心机制** — compress the persistent 4X loop into at most 4 nodes joined by `→`.
5. **补充** — one line per weaker finding, with no repeated developer/platform/store field list.
6. **趋势一句话** — keep one cross-game judgment only.
7. **观察与更新** — list every included name with its direct YouTube source. Never replace entries with counts and never include Reject items.

First-hand source links are a publishing gate:

- Every confirmed new game shown in Feishu must carry at least one direct YouTube video URL from its own report entry.
- Focus games use a clearly labeled `YouTube 原视频` button.
- Supplemental games use a clearly labeled inline `YouTube 原视频` link on the same line as the game.
- The compact `post` fallback must preserve the same one-game-one-source mapping.
- If any confirmed new game has no valid YouTube video URL, stop before publishing and name the affected game. Never silently send a source-less discovery or replace the source with only a store page.

Card header semantics:

- `orange`: at least one clear focus game.
- `blue`: confirmed additions but no focus.
- `grey`: Quiet Day.
- `red`: a separate blocked/failure notification only; never a daily report.

Use up to 3 header tags for `必看 / 补充 / 观察`. Keep normal visible card copy within about 500 Chinese characters and 15 text lines. Use buttons or named links instead of bare URLs.

Do not give every game equal space. Do not copy the full `What you'll see`, all three takeaways, or repetitive metadata into chat. The question is: “Is today worth attention, and which game matters most, why?”

## Sorting

- **Primary sort**: exact video upload time inside the resolved evidence window, newest first
- Include upload time (HH:MM) when exact; keep channel-page relative times clearly marked and below exact-time evidence

## What counts

Include only `Core` mobile 4X SLG: persistent base/city development and persistent world-map/territory play must both be visible or independently evidenced, with resource economy and/or army/alliance war supplying at least a third pillar.

Watch only `Pending` candidates that plausibly belong to the same core genre but lack one named piece of evidence. Do not use Watchlist as a home for nearby genres.

Exclude all `Reject` candidates, established titles without a major core-system update, PC-only games, and trailers without gameplay. Theme, store category, package size, popularity, duration, or multiple uploads never substitute for the 4X gate.

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
- **4X precision first**: publication requires `4X-SLG-STRICT-v1`; generic strategy adjacency stays out.
- **Two mandatory anchors**: no persistent Base / City plus World Map / Territory means no confirmed entry.
- **Gap-aware incremental window**: continue from the last successful committed cursor, capped at 72 hours.
- **Channel Radar first**: dynamic weighted sources remain the primary video-discovery mechanism.
- **Product Radar independent**: MY / ID / PH / GB / TH / CA coverage never changes channel weight.
- **RSS exact, page fallback approximate**: relative page time never impersonates an exact upload timestamp.
- **Structured judgment memory**: candidate ledger and Reject history prevent repeated false positives.
- **Batch with pacing**: 5 channels per batch. Cross-reference search may 429 — space queries out and stop repeated failed retries.
- **Automated push**: script auto-reads files and pushes, `--dry-run` for testing.
- **Cross-reference**: same game on 2+ channels in same day = hot new test.
- **First-hand evidence travels with the finding**: every confirmed new game keeps a direct YouTube source link in both the card and fallback.
- **No count-only sections**: every watchlist and known-game update renders at least `game name + direct YouTube source`; aggregate counts may summarize but never replace the entries.
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
