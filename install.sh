#!/usr/bin/env bash
# B5 — symlink the skill into ~/.claude/skills/undhd (repo edits go live instantly),
# and optionally install an idempotent daily cron entry for a managed directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/skill"
SKILL_DEST="$HOME/.claude/skills/undhd"
UNDHD_PY="$SCRIPT_DIR/skill/scripts/undhd.py"

usage() {
  echo "Usage: $0 [--cron --root DIR]" >&2
  exit 1
}

CRON=0
ROOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cron) CRON=1; shift ;;
    --root) ROOT="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

mkdir -p "$(dirname "$SKILL_DEST")"
if [[ -L "$SKILL_DEST" ]] && [[ "$(readlink "$SKILL_DEST")" == "$SKILL_SRC" ]]; then
  echo "skill already linked: $SKILL_DEST -> $SKILL_SRC"
elif [[ -e "$SKILL_DEST" || -L "$SKILL_DEST" ]]; then
  echo "error: $SKILL_DEST already exists and is not a link to $SKILL_SRC" >&2
  echo "remove it manually if you want install.sh to replace it." >&2
  exit 1
else
  ln -s "$SKILL_SRC" "$SKILL_DEST"
  echo "linked $SKILL_DEST -> $SKILL_SRC"
fi

if [[ "$CRON" -eq 1 ]]; then
  if [[ -z "$ROOT" ]]; then
    echo "error: --cron requires --root DIR" >&2
    exit 1
  fi
  ROOT_ABS="$(cd "$ROOT" && pwd)"
  mkdir -p "$HOME/.undhd"
  CRON_LINE="0 7 * * * python3 $UNDHD_PY maintain --root $ROOT_ABS >> $HOME/.undhd/cron.log 2>&1"
  EXISTING="$(crontab -l 2>/dev/null || true)"
  if printf '%s\n' "$EXISTING" | grep -F -q "$CRON_LINE"; then
    echo "cron entry already present for $ROOT_ABS"
  else
    { printf '%s\n' "$EXISTING"; echo "$CRON_LINE"; } | grep -v '^$' | crontab -
    echo "installed cron entry: $CRON_LINE"
  fi
fi
