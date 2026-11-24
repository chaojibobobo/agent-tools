#!/usr/bin/env bash
# run_scan.sh — Non-interactive game-scan-youtube runner for Codex or Claude Code.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COLLECTOR="${GAME_SCAN_COLLECTOR:-$SCRIPT_DIR/collect_rss.py}"
VALIDATOR="${GAME_SCAN_VALIDATOR:-$SCRIPT_DIR/validate_run.py}"
STATE_COMMITTER="${GAME_SCAN_STATE_COMMITTER:-$SCRIPT_DIR/commit_state.py}"
PRODUCT_RADAR_PREPARER="${GAME_SCAN_PRODUCT_RADAR_PREPARER:-$SCRIPT_DIR/prepare_product_radar.py}"

DATE="$(date +%Y-%m-%d)"
WORK_DIR="${GAME_SCAN_WORK_DIR:-/Users/bobo/Codexspace/tools/game-scan-youtube}"
ENV_FILE="${GAME_SCAN_ENV_FILE:-}"
HEALTH_THRESHOLD="${GAME_SCAN_HEALTH_THRESHOLD:-}"
AGENT="${GAME_SCAN_AGENT:-codex}"
MODEL=""
BUDGET="1.0"
DRY_RUN=""
FORCE_PUSH=""
MARKETS_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date)        DATE="$2"; shift 2 ;;
    --dir)         WORK_DIR="$2"; shift 2 ;;
    --env)         ENV_FILE="$2"; shift 2 ;;
    --health-threshold) HEALTH_THRESHOLD="$2"; shift 2 ;;
    --markets)     [[ -n "${2:-}" ]] || { echo "--markets requires a non-empty list" >&2; exit 2; }
                   MARKETS_OVERRIDE="$2"; shift 2 ;;
    --agent)       AGENT="$2"; shift 2 ;;
    --model)       MODEL="$2"; shift 2 ;;
    --budget)      BUDGET="$2"; shift 2 ;;
    --dry-run)     DRY_RUN="1"; shift ;;
    --force-push)  FORCE_PUSH="1"; shift ;;
    -h|--help)
      echo "Usage: $0 [--date YYYY-MM-DD] [--dir PATH] [--env PATH]"
      echo "          [--agent codex|claude] [--model MODEL] [--budget USD]"
      echo "          [--dry-run] [--force-push] [--health-threshold 0..1] [--markets MY,PH,JP]"
      echo ""
      echo "  --agent       Agent CLI used for the scan. Default: codex"
      echo "  --budget      Claude-only budget cap. Ignored by Codex."
      echo "  --dry-run     Generate files and state, but do not push to Feishu."
      echo "  --force-push  Skip scanning and retry the Feishu push for an existing report."
      exit 0
      ;;
    *)
      echo "Unknown arg: $1"
      exit 1
      ;;
  esac
done

"$SCRIPT_DIR/init_workspace.sh" --dir "$WORK_DIR"
cd "$WORK_DIR"
WORK_DIR="$PWD"

echo "[$(date +%Y-%m-%dT%H:%M:%S)] game-scan-youtube scan start — date=$DATE dir=$WORK_DIR agent=$AGENT force_push=$FORCE_PUSH"

SCAN_INPUT="$WORK_DIR/scan-input-$DATE.json"
PENDING_DIR="$WORK_DIR/.run-locks/$DATE.needs-collection"
PUSH_ARGS=(--date "$DATE" --dir "$WORK_DIR")
if [[ -n "$ENV_FILE" ]]; then PUSH_ARGS+=(--env "$ENV_FILE"); fi
if [[ -n "$DRY_RUN" ]]; then PUSH_ARGS+=(--dry-run); fi

send_or_skip() {
  local rc=0
  python3 "$SCRIPT_DIR/push_feishu.py" "${PUSH_ARGS[@]}" --if-configured "$@" || rc=$?
  if [[ "$rc" -eq 3 && "${GAME_SCAN_REQUIRE_DELIVERY:-0}" != 1 ]]; then
    echo "Local run only: Feishu unavailable. No delivery receipt created."
  fi
  return "$rc"
}

stop_if_degraded() {
  local status
  status="$(python3 "$SCRIPT_DIR/run_health.py" --status "$SCAN_INPUT")" || exit 1
  if [[ "$status" == "RUN_DEGRADED" ]]; then
    mkdir -p "$PENDING_DIR"
    send_or_skip --ops-alert --write-incident || echo "运维告警未发送，见本地降级记录。" >&2
    exit 75  # EX_TEMPFAIL: no normal report, state commit, receipt or done stamp.
  fi
}

if [[ -n "$FORCE_PUSH" ]]; then
  if [[ -f "$SCAN_INPUT" ]]; then stop_if_degraded; fi
  if [[ -d "$PENDING_DIR" ]]; then
    echo "Interrupted recovery: re-collect before retrying delivery." >&2
    exit 75
  fi
  REPORT="$WORK_DIR/$DATE.md"
  if [[ ! -s "$REPORT" ]]; then
    echo "[$(date +%Y-%m-%dT%H:%M:%S)] --force-push but no report found at $REPORT; cannot push"
    exit 1
  fi
  python3 "$VALIDATOR" --date "$DATE" --dir "$WORK_DIR"
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] --force-push: retrying Feishu push for existing report"
  EXIT_CODE=0
  send_or_skip || EXIT_CODE=$?
  if [[ "$EXIT_CODE" == 3 && "${GAME_SCAN_REQUIRE_DELIVERY:-0}" != 1 ]]; then exit 0; fi
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] push result — exit=$EXIT_CODE"
  exit "$EXIT_CODE"
fi

# Persist recovery intent even if the process is interrupted after writing healthy
# scan-input but before replacing the previous report/ledger or committing the cursor.
mkdir -p "$PENDING_DIR"

# Collect RSS in the parent process. A nested agent may not have network access,
# so its input must be a local, inspectable evidence file.
PRODUCT_RADAR_INPUT="$WORK_DIR/product-radar-input-$DATE.json"
COLLECT_ARGS=(
  --channels "$WORK_DIR/channels.json"
  --output "$SCAN_INPUT"
  --state "$WORK_DIR/scan_state.json"
  --channel-page-fallback
)
if [[ -n "$ENV_FILE" ]]; then COLLECT_ARGS+=(--env "$ENV_FILE"); fi
if [[ -n "$HEALTH_THRESHOLD" ]]; then COLLECT_ARGS+=(--health-threshold "$HEALTH_THRESHOLD"); fi
if [[ -n "${GAME_SCAN_FEED_DIR:-}" ]]; then
  COLLECT_ARGS+=(--feed-dir "$GAME_SCAN_FEED_DIR")
fi
if [[ -n "${GAME_SCAN_CHANNEL_PAGE_DIR:-}" ]]; then
  COLLECT_ARGS+=(--channel-page-dir "$GAME_SCAN_CHANNEL_PAGE_DIR")
fi
if [[ "$DATE" != "$(date +%Y-%m-%d)" ]]; then
  COLLECT_ARGS+=(--now "${DATE}T23:59:59+08:00")
fi
COLLECT_EXIT=0
python3 "$COLLECTOR" "${COLLECT_ARGS[@]}" || COLLECT_EXIT=$?
if [[ -f "$SCAN_INPUT" ]]; then stop_if_degraded; fi
if [[ "$COLLECT_EXIT" != 0 ]]; then exit "$COLLECT_EXIT"; fi
if [[ ! -s "$SCAN_INPUT" ]]; then echo "Missing scan input; refusing to continue" >&2; exit 1; fi
RADAR_ARGS=(--date "$DATE" --output "$PRODUCT_RADAR_INPUT")
if [[ -n "$MARKETS_OVERRIDE" ]]; then RADAR_ARGS+=(--markets "$MARKETS_OVERRIDE"); fi
python3 "$PRODUCT_RADAR_PREPARER" "${RADAR_ARGS[@]}"

LEDGER="$WORK_DIR/candidate-ledger-$DATE.json"
PROMPT="Use the game-scan-youtube skill for report date $DATE with persistent workspace $WORK_DIR. Analyze YouTube evidence in $SCAN_INPUT and the regional store work manifest in $PRODUCT_RADAR_INPUT. Read channels.json, seen_games.json, candidate_history.json, product_radar.json, and scan_state.json. Generate both $WORK_DIR/$DATE.md and $LEDGER. Use candidate-ledger schema_version 2. The ledger must include every reviewed Core, Pending, Reject, and store-only Product Lead; structured evidence for base_city, world_map_territory, resource_economy, and army_alliance_war; evidence_stage with progression_depth, world_map_unlock_status, test_stage_uncertainty, and counterevidence; direct YouTube sources; aliases/package IDs; recheck conditions; and product_radar_run.focus_markets copied from the dated manifest. Do not edit the manifest. Account for exactly those focus markets in product_radar_run.countries with checked, failed, or not_run and source URLs. An out-of-focus listing belongs in the candidate store_observations with its localized first-party product page (or official single-product lookup), never in required coverage; it neither enables the market for future runs nor affects YouTube channel scores. Core requires positive pillar evidence. Reject requires explicit sourced counterevidence; tutorial/early footage that merely does not show a world map stays Pending when a plausible base/resource/army skeleton exists. Verify a country claim with that country's localized source_endpoints; generic search snippets alone do not prove regional availability. Target-country coverage must never affect channel weight. The local scan input is the YouTube collection boundary: do not claim exact upload time for channel-page relative evidence. Execute country queries only when network evidence is actually available; otherwise record failed/not_run without fabricating coverage. Product Leads are internal only and cannot appear in the report. If source_summary is degraded, disclose it. Generate report and ledger only; do not update state or push. Every non-quiet published game must have its own YouTube link."

PROMPT+=" Follow Decision Card v3: include > **Card Headline:**, optional > **Card Trend:** and > **Trend Products:** (two corroborated names separated by ｜); for focus use **Card Evidence:** and **Card Mechanism:**; for each supplemental use **Card Form:** and **Card Signal:**. Keep every time-unverified discovery under ## 待核实发现（时间存疑） and never count it as a confirmed debut. For suspected renames verify developer, icon/UI and seen_games roots; merge under the original and state rename_evidence. Store leads may trigger a targeted first-hand gameplay lookup but require a verified source to be published."

set +e
case "$AGENT" in
  codex)
    CODEX_ARGS=(exec --cd "$WORK_DIR" --skip-git-repo-check)
    if [[ -n "$MODEL" ]]; then
      CODEX_ARGS+=(--model "$MODEL")
    fi
    codex "${CODEX_ARGS[@]}" "$PROMPT"
    ;;
  claude)
    CLAUDE_ARGS=(-p "$PROMPT" --add-dir "$WORK_DIR" --output-format json --max-budget-usd "$BUDGET")
    if [[ -n "$MODEL" ]]; then
      CLAUDE_ARGS+=(--model "$MODEL")
    fi
    claude "${CLAUDE_ARGS[@]}"
    ;;
  *)
    echo "Unsupported agent: $AGENT (expected codex or claude)"
    exit 2
    ;;
esac

EXIT_CODE=$?
set -e
echo "[$(date +%Y-%m-%dT%H:%M:%S)] game-scan-youtube scan end — exit=$EXIT_CODE"
if [[ $EXIT_CODE -ne 0 ]]; then
  exit "$EXIT_CODE"
fi

if [[ "$SCAN_INPUT" -nt "$WORK_DIR/$DATE.md" || "$SCAN_INPUT" -nt "$LEDGER" ]]; then
  echo "Current scan input has no fresh report and ledger; recovery is still pending." >&2
  exit 1
fi
python3 "$VALIDATOR" --date "$DATE" --dir "$WORK_DIR" --require-intelligence-ledger

python3 "$SCRIPT_DIR/push_feishu.py" --date "$DATE" --dir "$WORK_DIR" --dry-run
python3 "$STATE_COMMITTER" --date "$DATE" --dir "$WORK_DIR"
rmdir "$PENDING_DIR"

if [[ -z "$DRY_RUN" ]]; then
  PUSH_EXIT=0
  send_or_skip || PUSH_EXIT=$?
  if [[ "$PUSH_EXIT" == 3 && "${GAME_SCAN_REQUIRE_DELIVERY:-0}" != 1 ]]; then exit 0; fi
  if [[ "$PUSH_EXIT" != 0 ]]; then exit "$PUSH_EXIT"; fi
  python3 "$VALIDATOR" --date "$DATE" --dir "$WORK_DIR" --require-intelligence-ledger --require-push-receipt
fi
