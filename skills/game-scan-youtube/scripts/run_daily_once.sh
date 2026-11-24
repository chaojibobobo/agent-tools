#!/usr/bin/env bash
# run_daily_once.sh - cron-safe daily wrapper for game-scan-youtube
# Uses done stamps for completion (not just report file existence).
# Includes concurrency protection via mkdir-based lock.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK_DIR="${GAME_SCAN_WORK_DIR:-/Users/bobo/Codexspace/tools/game-scan-youtube}"
DATE="$(date +%Y-%m-%d)"
RUNNER="${GAME_SCAN_RUNNER:-$SCRIPT_DIR/run_scan.sh}"
VALIDATOR="${GAME_SCAN_VALIDATOR:-$SCRIPT_DIR/validate_run.py}"
RUN_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date) DATE="$2"; shift 2 ;;
    --dir)  WORK_DIR="$2"; shift 2 ;;
    --markets) RUN_ARGS+=(--markets "$2"); shift 2 ;;
    --health-threshold) RUN_ARGS+=(--health-threshold "$2"); shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--date YYYY-MM-DD] [--dir PATH] [--markets MY,PH,JP] [--health-threshold 0..1]"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1"
      exit 1
      ;;
  esac
done

"$SCRIPT_DIR/init_workspace.sh" --dir "$WORK_DIR"
WORK_DIR="$(cd "$WORK_DIR" && pwd)"
RUN_ARGS+=(--date "$DATE" --dir "$WORK_DIR")
export GAME_SCAN_REQUIRE_DELIVERY=1

REPORT="$WORK_DIR/$DATE.md"
STAMP_DIR="$WORK_DIR/.run-stamps"
STAMP="$STAMP_DIR/$DATE.done"
LOCK_DIR="$WORK_DIR/.run-locks"
LOCK="$LOCK_DIR/$DATE.lock"
LOG_DIR="$WORK_DIR/logs"
SCAN_INPUT="$WORK_DIR/scan-input-$DATE.json"

mkdir -p "$STAMP_DIR" "$LOCK_DIR" "$LOG_DIR"

# --- Concurrency protection (atomic mkdir) ---
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] another scan is running for $DATE (lock exists); exiting"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

scan_status() {
  if [[ -f "$SCAN_INPUT" ]]; then
    python3 "$SCRIPT_DIR/run_health.py" --status "$SCAN_INPUT"
  else
    echo "LEGACY"
  fi
}
PREVIOUS_STATUS="$(scan_status)" || PREVIOUS_STATUS="INVALID"
if [[ -d "$LOCK_DIR/$DATE.needs-collection" ]]; then PREVIOUS_STATUS="INVALID"; fi
# A previous/aborted incident must run collection again, even with an old report or receipt.
if [[ -f "$STAMP" && "$PREVIOUS_STATUS" != "RUN_DEGRADED" && "$PREVIOUS_STATUS" != "INVALID" ]]; then
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] daily scan already completed for $DATE (done stamp exists); skipping"
  exit 0
fi

# --- Report exists but no stamp = scan ran but push may have failed ---
if [[ -s "$REPORT" && "$PREVIOUS_STATUS" != "RUN_DEGRADED" && "$PREVIOUS_STATUS" != "INVALID" ]]; then
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] report exists for $DATE but no done stamp — retrying push only"
  set +e
  "$RUNNER" "${RUN_ARGS[@]}" --force-push
  PUSH_EXIT=$?
  set -e
  VALIDATE_ARGS=(--date "$DATE" --dir "$WORK_DIR" --require-push-receipt)
  if [[ -s "$WORK_DIR/candidate-ledger-$DATE.json" ]]; then
    VALIDATE_ARGS+=(--require-intelligence-ledger)
  fi
  POST_STATUS="$(scan_status)" || exit 1
  if [[ "$POST_STATUS" == "RUN_DEGRADED" ]]; then exit 75; fi
  if [[ $PUSH_EXIT -eq 0 ]] && python3 "$VALIDATOR" "${VALIDATE_ARGS[@]}"; then
    touch "$STAMP"
    echo "[$(date +%Y-%m-%dT%H:%M:%S)] push retry succeeded for $DATE"
  else
    echo "[$(date +%Y-%m-%dT%H:%M:%S)] push retry failed (exit=$PUSH_EXIT); not writing done stamp"
    if [[ $PUSH_EXIT -eq 0 ]]; then exit 1; fi
  fi
  exit $PUSH_EXIT
fi

# --- No report and no stamp — run full scan ---
cd "$WORK_DIR"
set +e
"$RUNNER" "${RUN_ARGS[@]}"
SCAN_EXIT=$?
set -e

if [[ $SCAN_EXIT -ne 0 ]]; then
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] scan failed (exit=$SCAN_EXIT); not writing done stamp"
  exit $SCAN_EXIT
fi
POST_STATUS="$(scan_status)" || exit 1
if [[ "$POST_STATUS" == "RUN_DEGRADED" ]]; then exit 75; fi

# --- Exit 0 is not completion: require a parseable report and a Feishu receipt. ---
if ! python3 "$VALIDATOR" --date "$DATE" --dir "$WORK_DIR" --require-intelligence-ledger --require-push-receipt; then
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] completion evidence missing or invalid; not writing done stamp"
  exit 1
fi

# --- Write done stamp only after machine-verifiable completion. ---
touch "$STAMP"
echo "[$(date +%Y-%m-%dT%H:%M:%S)] daily scan completed for $DATE"
