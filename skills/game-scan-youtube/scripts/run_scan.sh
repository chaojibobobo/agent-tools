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
ENV_FILE="${GAME_SCAN_ENV_FILE:-$HOME/.game-scan-youtube/.env}"
AGENT="${GAME_SCAN_AGENT:-codex}"
MODEL=""
BUDGET="1.0"
DRY_RUN=""
FORCE_PUSH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date)        DATE="$2"; shift 2 ;;
    --dir)         WORK_DIR="$2"; shift 2 ;;
    --env)         ENV_FILE="$2"; shift 2 ;;
    --agent)       AGENT="$2"; shift 2 ;;
    --model)       MODEL="$2"; shift 2 ;;
    --budget)      BUDGET="$2"; shift 2 ;;
    --dry-run)     DRY_RUN="1"; shift ;;
    --force-push)  FORCE_PUSH="1"; shift ;;
    -h|--help)
      echo "Usage: $0 [--date YYYY-MM-DD] [--dir PATH] [--env PATH]"
      echo "          [--agent codex|claude] [--model MODEL] [--budget USD]"
      echo "          [--dry-run] [--force-push]"
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

echo "[$(date +%Y-%m-%dT%H:%M:%S)] game-scan-youtube scan start — date=$DATE dir=$WORK_DIR agent=$AGENT force_push=$FORCE_PUSH"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
  echo "Loaded env from $ENV_FILE"
elif [[ -z "$DRY_RUN" ]]; then
  echo "Warning: .env not found at $ENV_FILE (Feishu push may fail)"
fi

if [[ -n "$FORCE_PUSH" ]]; then
  REPORT="$WORK_DIR/$DATE.md"
  if [[ ! -s "$REPORT" ]]; then
    echo "[$(date +%Y-%m-%dT%H:%M:%S)] --force-push but no report found at $REPORT; cannot push"
    exit 1
  fi
  python3 "$VALIDATOR" --date "$DATE" --dir "$WORK_DIR"
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] --force-push: retrying Feishu push for existing report"
  python3 "$SCRIPT_DIR/push_feishu.py" \
    --date "$DATE" \
    --dir "$WORK_DIR" \
    --env "$ENV_FILE"
  EXIT_CODE=$?
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] push result — exit=$EXIT_CODE"
  exit "$EXIT_CODE"
fi

# Collect RSS in the parent process. A nested agent may not have network access,
# so its input must be a local, inspectable evidence file.
SCAN_INPUT="$WORK_DIR/scan-input-$DATE.json"
PRODUCT_RADAR_INPUT="$WORK_DIR/product-radar-input-$DATE.json"
COLLECT_ARGS=(
  --channels "$WORK_DIR/channels.json"
  --output "$SCAN_INPUT"
  --state "$WORK_DIR/scan_state.json"
  --channel-page-fallback
)
if [[ -n "${GAME_SCAN_FEED_DIR:-}" ]]; then
  COLLECT_ARGS+=(--feed-dir "$GAME_SCAN_FEED_DIR")
fi
if [[ -n "${GAME_SCAN_CHANNEL_PAGE_DIR:-}" ]]; then
  COLLECT_ARGS+=(--channel-page-dir "$GAME_SCAN_CHANNEL_PAGE_DIR")
fi
if [[ "$DATE" != "$(date +%Y-%m-%d)" ]]; then
  COLLECT_ARGS+=(--now "${DATE}T23:59:59+08:00")
fi
python3 "$COLLECTOR" "${COLLECT_ARGS[@]}"
python3 "$PRODUCT_RADAR_PREPARER" --date "$DATE" --output "$PRODUCT_RADAR_INPUT"

LEDGER="$WORK_DIR/candidate-ledger-$DATE.json"
PROMPT="Use the game-scan-youtube skill for report date $DATE with persistent workspace $WORK_DIR. Analyze YouTube evidence in $SCAN_INPUT and the independent six-country work manifest in $PRODUCT_RADAR_INPUT. Read channels.json, seen_games.json, candidate_history.json, product_radar.json, and scan_state.json. Generate both $WORK_DIR/$DATE.md and $LEDGER. The ledger must include every reviewed Core, Pending, Reject, and store-only Product Lead; structured evidence for base_city, world_map_territory, resource_economy, and army_alliance_war; direct YouTube sources; aliases/package IDs; recheck conditions; and product_radar_run entries for exactly MY, ID, PH, GB, TH, CA with checked, failed, or not_run status plus source URLs. Verify a country claim with that country's localized source_endpoints; generic search snippets alone do not prove regional availability. Target-country coverage must never affect channel weight. The local scan input is the YouTube collection boundary: do not claim exact upload time for channel-page relative evidence. Execute country queries only when network evidence is actually available; otherwise record failed/not_run without fabricating coverage. Product Leads are internal only and cannot appear in the report. If source_summary is degraded, disclose it. Generate report and ledger only; do not update state or push. Every non-quiet published game must have its own YouTube link."

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

python3 "$VALIDATOR" --date "$DATE" --dir "$WORK_DIR" --require-intelligence-ledger

DRY_RUN_PUSH_ARGS=(
  --date "$DATE"
  --dir "$WORK_DIR"
  --env "$ENV_FILE"
  --dry-run
)
python3 "$SCRIPT_DIR/push_feishu.py" "${DRY_RUN_PUSH_ARGS[@]}"
python3 "$STATE_COMMITTER" --date "$DATE" --dir "$WORK_DIR"

if [[ -z "$DRY_RUN" ]]; then
  python3 "$SCRIPT_DIR/push_feishu.py" \
    --date "$DATE" \
    --dir "$WORK_DIR" \
    --env "$ENV_FILE"
  python3 "$VALIDATOR" --date "$DATE" --dir "$WORK_DIR" --require-intelligence-ledger --require-push-receipt
fi
