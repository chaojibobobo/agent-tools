# Game Scan 双雷达与频道智能评分设计

## 日期

2026-08-11

## Context

`game-scan-youtube` 已经具备严格 4X 发布门禁和逐游戏 YouTube 一手链接，但发现层仍以固定 24 小时、单次 RSS 和粗粒度频道活跃度为主。2026-08-11 实跑中 RSS 仅 9/50 成功，频道页 50/50 可读，证明需要确定性的降级采集。用户同时确认：已知频道池及动态权重仍是主雷达；六国早期上架监测是独立的产品雷达；目标国家覆盖不能影响频道权重。

## Approved requirements

- 固定可变工作区为 `/Users/bobo/Codexspace/tools/game-scan-youtube`。
- 保留动态频道池，并精细维护等级。
- 频道情报分只使用严格 4X 命中、领先发现、独有发现和一手视频证据质量。
- `source_health` 与 `intelligence_score` 分离；RSS 临时失败不降低频道情报价值。
- 六国雷达固定覆盖马来西亚 `MY`、印度尼西亚 `ID`、菲律宾 `PH`、英国 `GB`、泰国 `TH`、加拿大 `CA`。
- 国家覆盖、国家产品产出和国家领先发现只属于产品雷达，不进入频道分数。
- 商店候选没有一手 YouTube 实机时只保留为内部 `Product Lead`，不能作为正式游戏情报发布。
- 所有正式结果继续经过 `4X-SLG-STRICT-v1`，并携带自己的 YouTube 原视频。
- 本次升级不执行真实扫描、不推送飞书、不改历史报告。

## Architecture

```text
Channel Radar
RSS exact timestamps
  -> bounded retry
  -> failed-channel /videos fallback (relative-time discovery only)

Product Radar
MY / ID / PH / GB / TH / CA store observations
  -> exact name / package ID / developer
  -> exact YouTube reverse lookup

Both radars
  -> candidate-ledger-YYYY-MM-DD.json
  -> structured four-pillar validation
  -> report + direct YouTube evidence
  -> state commit
       - seen_games.json
       - candidate_history.json
       - channels.json intelligence metrics
       - product_radar.json country metrics
       - scan_state.json successful window cursor
```

## Collection model

`collect_rss.py` remains the parent-process collector but becomes a multi-source YouTube collector:

- Default window starts at `scan_state.json.last_successful_scan_end`; first run uses 24 hours.
- Backfill is capped at 72 hours. When the cursor is older, the output marks `backfill_limited: true`.
- RSS keeps exact timestamps and uses bounded curl retries.
- A failed RSS channel falls back to its `/videos` page. Page entries retain `relative_published` and `time_precision: relative`; they never impersonate exact 24-hour evidence.
- Coverage is evaluated after fallback. Overall and priority-channel effective coverage below 60% is `blocked`; otherwise any fallback, RSS failure, or low exact-time coverage is `degraded`; fully healthy RSS coverage is `complete`.
- A blocked collector exits non-zero. A degraded collector may continue but must be disclosed.

## Candidate ledger

Every new run creates `candidate-ledger-YYYY-MM-DD.json` with:

- canonical game name, aliases, package IDs and store URLs;
- classification `Core / Pending / Reject / Product Lead`;
- structured `base_city`, `world_map_territory`, `resource_economy`, `army_alliance_war` evidence;
- YouTube videos with channel ID/name, upload time precision and evidence quality 1–5;
- store observations with country, availability state and observed time;
- missing pillars and explicit recheck condition.

Machine validation enforces:

- Core: Base and World Map present, plus Resource or Army present;
- Pending: plausible target genre but at least one named required gap;
- Reject: never appears in the published report;
- Product Lead: may have store observations but never appears in the report without YouTube evidence and a passing genre classification;
- every published game maps to a ledger candidate and at least one matching YouTube URL.

## Channel intelligence model

The 90-day event history is stored per channel in `channels.json`. Metrics use the latest 30 days where applicable.

```text
intelligence_score =
    40% strict_4x_yield
  + 25% early_discovery
  + 20% unique_discovery
  + 15% evidence_quality
```

- `strict_4x_yield`: `(Core + 0.5 * Pending) / reviewed`.
- `early_discovery`: share of valid candidates for which the channel supplied the earliest exact source.
- `unique_discovery`: share of valid candidates supplied by only this channel in the run.
- `evidence_quality`: average agent-reviewed source quality normalized from 1–5.
- Country/store fields are never read by the scoring function.
- Fewer than three reviewed events remains probation and cannot auto-promote above weight 4.
- User-confirmed seed channels retain manual weight 10. Other channels derive weight 1–9 from score bands.
- RSS/page success only updates `source_health`; it never changes `intelligence_score`.

## Product radar model

`product_radar.json` stores six mandatory country definitions, scan cursor, product entities and country metrics. Initial country priority is equal. Metrics include coverage completion, new listings, Product Leads, Core/Pending conversions and successful YouTube reverse lookups. These metrics control product-radar search depth only and never mutate channel weight.

Country availability is accepted only from localized store evidence: Google Play URLs carry `gl=COUNTRY`, while App Store lookup URLs carry `country=COUNTRY` or a matching country path. Generic search snippets may discover a lead but cannot mark a country checked or create a verified country observation. Repeated identical observations and repeated commits of the same radar run are idempotent.

## Backward compatibility

- Existing `channels.json` and `seen_games.json` remain readable; new fields are added lazily.
- Historical reports do not require candidate ledgers.
- `validate_run.py` gains an explicit `--require-intelligence-ledger` gate used by the upgraded runner; manual legacy validation remains possible.
- `commit_state.py` uses the ledger when present and falls back to legacy exact-name behavior for historical reports.
- `init_workspace.sh` adds missing new state files without overwriting existing state.

## Tests and acceptance

- Country/store observations cannot change a channel score or weight.
- Core/Pending/Reject events change channel scores according to the four approved components.
- Source failures change only `source_health`.
- The six target countries are always present and no unsupported country is silently accepted.
- Alias/package identity merges updates into one game entity.
- Reject candidates persist locally and are not repeatedly treated as unseen.
- Fake `Core` labels without structured pillars fail validation.
- RSS failure plus page success is degraded, not blocked and not complete.
- Low effective priority coverage is blocked.
- A successful commit advances the scan cursor; collection alone does not.
- Existing report/card/receipt tests remain green.
- Source and installed runtime are synchronized after verification.

## Final judgment

`APPROVED DESIGN`

The user reviewed and corrected the design in conversation, specifically preserving dynamic channel weighting, defining the six early-test countries, and removing target-country coverage from channel weight. The instruction to upgrade the skill is approval to implement this written form of that design.

## Reuse

Future tuning must change scoring coefficients only after reviewing real 30-day channel events. Country strategy may change product-radar depth, but must never be introduced as a channel-quality feature.
