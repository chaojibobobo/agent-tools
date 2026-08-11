# Game Scan Intelligence Radar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `game-scan-youtube` into a reliable channel radar plus an independent six-country product radar, with structured evidence and precise dynamic channel levels.

**Architecture:** Extend the deterministic collector for cursor-aware RSS and channel-page fallback. Add a focused `intelligence_state.py` domain module for ledger validation, entity identity, channel scoring and product-radar state; keep orchestration in existing validator/committer scripts and preserve current report/card delivery.

**Tech Stack:** Python 3.9 standard library, Bash, JSON state, `unittest`, curl, existing Markdown parser and Feishu delivery code.

## Global Constraints

- Mutable runtime state remains in `/Users/bobo/Codexspace/tools/game-scan-youtube`.
- Target countries are exactly `MY`, `ID`, `PH`, `GB`, `TH`, `CA` for v1.
- Target-country coverage never affects channel score or weight.
- Channel score coefficients are yield 40%, lead 25%, unique 20%, evidence quality 15%.
- Source health is separate from intelligence value.
- Page-relative timestamps are discovery evidence only.
- No external Python dependencies.
- No real scan or Feishu send during implementation.
- Interactive manual scans use the current agent directly; the nested runner is reserved for explicit non-interactive execution.
- Country availability requires localized store URLs, not country names embedded in generic search snippets.

---

### Task 1: State Contracts and Channel Scoring

**Files:**
- Create: `skills/game-scan-youtube/scripts/intelligence_state.py`
- Create: `skills/game-scan-youtube/tests/test_intelligence_state.py`

**Interfaces:**
- `new_product_radar_state() -> dict`
- `validate_candidate_ledger(ledger: dict) -> None`
- `candidate_identity(candidate: dict) -> str`
- `update_channel_intelligence(channels: dict, ledger: dict, event_date: str) -> dict`
- `update_product_radar(state: dict, ledger: dict, event_date: str) -> dict`
- `update_candidate_history(state: dict, ledger: dict, event_date: str) -> dict`

- [ ] Write failing tests for the six-country state, Core pillar validation, Product Lead exclusion, alias/package identity, Reject persistence and country-score isolation.
- [ ] Run `python3 -m unittest skills.game-scan-youtube.tests.test_intelligence_state -v` and confirm failures are caused by the missing module.
- [ ] Implement the minimal pure functions and atomic JSON helpers.
- [ ] Re-run the focused tests and confirm all pass.

### Task 2: Cursor-Aware Multi-Source Collection

**Files:**
- Modify: `skills/game-scan-youtube/scripts/collect_rss.py`
- Create: `skills/game-scan-youtube/tests/test_collection_v2.py`

**Interfaces:**
- `resolve_window(now, state, max_backfill_hours=72) -> (start, end, limited)`
- `parse_channel_page(html_bytes, channel) -> list[dict]`
- `coverage_summary(channels, results) -> dict`
- CLI adds `--state`, `--channel-page-fallback`, `--channel-page-dir`, `--max-backfill-hours`.

- [ ] Write fixtures and failing tests for cursor windows, 72-hour cap, page-relative evidence, degraded fallback and blocked priority coverage.
- [ ] Run the focused collection tests and confirm expected failures.
- [ ] Add bounded curl retry, channel-page parsing and coverage gates without changing exact RSS parsing.
- [ ] Re-run focused and legacy collector tests.

### Task 3: Validation and Atomic State Commit

**Files:**
- Modify: `skills/game-scan-youtube/scripts/validate_run.py`
- Modify: `skills/game-scan-youtube/scripts/commit_state.py`
- Modify: `skills/game-scan-youtube/scripts/push_feishu.py`
- Modify: `skills/game-scan-youtube/tests/test_pipeline.py`

**Interfaces:**
- Validator flag: `--require-intelligence-ledger`.
- Ledger path: `candidate-ledger-YYYY-MM-DD.json`.
- Commit updates `seen_games.json`, `candidate_history.json`, `channels.json`, `product_radar.json`, and `scan_state.json` only after report and ledger validation.

- [ ] Add failing tests for fake Core evidence, report-to-ledger source mapping, package/alias merge, Reject memory and cursor advance.
- [ ] Run focused tests and verify red state.
- [ ] Parse `4X Evidence`, package ID and aliases from reports where present; validate the ledger and add compatible atomic state writes.
- [ ] Re-run focused tests and the existing pipeline suite.

### Task 4: Workspace and Runner Integration

**Files:**
- Create: `skills/game-scan-youtube/assets/bootstrap/product_radar.json`
- Create: `skills/game-scan-youtube/assets/bootstrap/candidate_history.json`
- Create: `skills/game-scan-youtube/assets/bootstrap/scan_state.json`
- Modify: `skills/game-scan-youtube/scripts/init_workspace.sh`
- Modify: `skills/game-scan-youtube/scripts/run_scan.sh`
- Modify: `skills/game-scan-youtube/scripts/run_daily_once.sh`

**Interfaces:**
- Runner always passes scan state and channel-page fallback to collection.
- Runner prompt requires a report plus candidate ledger and six-country product observations.
- Runner requires intelligence-ledger validation before commit and publish.

- [ ] Add failing shell/behavior tests for initialization and required ledger gating.
- [ ] Implement non-overwriting state initialization and runner arguments.
- [ ] Run `bash -n` on all shell scripts and the complete unit suite.

### Task 5: Skill Documentation, Installation and Verification

**Files:**
- Modify: `skills/game-scan-youtube/SKILL.md`
- Modify: `skills/game-scan-youtube/README.md`
- Modify: `skills/game-scan-youtube/agents/openai.yaml`
- Modify: `skills/game-scan-youtube/assets/bootstrap/README.md`

- [ ] Document the dual radar, exact country list, channel scoring formula, source-health separation, candidate ledger and new completion gates.
- [ ] Run the skill static checks and all unit tests.
- [ ] Run syntax checks for every Python and Bash script.
- [ ] Install with `scripts/install-skill.sh game-scan-youtube`.
- [ ] Verify source/runtime file parity and rerun the installed test suite.
- [ ] Record the final implementation evidence in the existing better-me handoff.

## Self-review

- Every approved design requirement maps to a task.
- No target-country field is consumed by the scoring interface.
- New-state initialization is non-destructive.
- Legacy reports remain valid unless the new runner explicitly requires a ledger.
- The implementation does not depend on an unavailable store API; it formalizes country observations and exact-title/package reverse lookup as evidence contracts.
