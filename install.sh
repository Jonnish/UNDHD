#!/usr/bin/env bash
# B5 — symlink the skill into ~/.claude/skills/undhd (repo edits go live instantly),
# and optionally install an idempotent daily cron entry for a managed directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/skill"
SKILL_DEST="$HOME/.claude/skills/undhd"
UNDHD_PY="$SCRIPT_DIR/skill/scripts/undhd.py"

usage() {
  echo "Usage: $0 [--cron DIR]" >&2
  exit 1
}

CRON=0
ROOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cron)
      CRON=1
      shift
      if [[ $# -eq 0 || "$1" == --* ]]; then
        echo "error: --cron requires a path to the managed directory" >&2
        usage
      fi
      ROOT="$1"
      shift
      ;;
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
  if ! command -v crontab >/dev/null 2>&1; then
    cat >&2 <<MSG
error: 'crontab' isn't available on this system, so automatic scheduling can't be installed
here. This is expected on native Windows (outside WSL), which has no cron daemon. Options:
  - Run this installer from WSL (Windows Subsystem for Linux) instead — it has crontab.
  - Add a Windows Task Scheduler entry yourself, e.g.:
      schtasks /create /sc daily /st 07:00 /tn undhd-maintain ^
        /tr "python $UNDHD_PY maintain --root $ROOT"
  - Skip scheduling and run 'undhd.py maintain --root $ROOT' manually, or from any
    scheduler you already use.
The skill symlink above was still installed; only the cron entry was skipped.
MSG
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
