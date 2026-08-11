#!/usr/bin/env bash
# Initialize a mutable scan workspace from the skill's read-only bootstrap assets.
set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WORK_DIR="${GAME_SCAN_WORK_DIR:-/Users/bobo/Codexspace/tools/game-scan-youtube}"
COPY_HISTORY="1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)         WORK_DIR="$2"; shift 2 ;;
    --no-history)  COPY_HISTORY=""; shift ;;
    -h|--help)
      echo "Usage: $0 [--dir PATH] [--no-history]"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1"
      exit 1
      ;;
  esac
done

mkdir -p "$WORK_DIR"

for state_name in channels.json seen_games.json candidate_history.json product_radar.json scan_state.json; do
  if [[ ! -e "$WORK_DIR/$state_name" ]]; then
    cp "$SKILL_DIR/assets/bootstrap/$state_name" "$WORK_DIR/$state_name"
    echo "Initialized $WORK_DIR/$state_name"
  fi
done

if [[ -n "$COPY_HISTORY" ]]; then
  for report_path in "$SKILL_DIR"/references/history/reports/*.md; do
    report_name="$(basename "$report_path")"
    if [[ ! -e "$WORK_DIR/$report_name" ]]; then
      cp "$report_path" "$WORK_DIR/$report_name"
    fi
  done

  mkdir -p "$WORK_DIR/history/investigations" "$WORK_DIR/history/operations"
  for history_group in investigations operations; do
    for history_path in "$SKILL_DIR"/references/history/"$history_group"/*; do
      history_name="$(basename "$history_path")"
      if [[ ! -e "$WORK_DIR/history/$history_group/$history_name" ]]; then
        cp "$history_path" "$WORK_DIR/history/$history_group/$history_name"
      fi
    done
  done
fi

echo "Workspace ready: $WORK_DIR"
