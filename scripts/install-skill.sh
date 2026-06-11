#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: scripts/install-skill.sh <skill-name>" >&2
  exit 2
fi

skill_name="$1"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo_root/skills/$skill_name"
dest="${CODEX_SKILLS_DIR:-$HOME/.codex/skills}/$skill_name"

if [[ ! -d "$src" ]]; then
  echo "Skill not found: $src" >&2
  exit 1
fi

if [[ ! -f "$src/SKILL.md" ]]; then
  echo "Missing SKILL.md: $src" >&2
  exit 1
fi

mkdir -p "$(dirname "$dest")"
rm -rf "$dest"
cp -R "$src" "$dest"

echo "Installed $skill_name -> $dest"
