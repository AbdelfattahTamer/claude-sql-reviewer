#!/usr/bin/env bash
#
# Install the sql-reviewer skill into ~/.claude/skills/ so it is available as
# /sql-reviewer in every project on this machine.
#
# Usage:
#   ./install.sh                 install for the current user
#   ./install.sh --project DIR   install into DIR/.claude/skills instead
#   ./install.sh --force         overwrite an existing install without asking
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skills/sql-reviewer"
DEST_ROOT="${HOME}/.claude/skills"
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --force)   FORCE=1; shift ;;
    --project) DEST_ROOT="${2:?--project needs a directory}/.claude/skills"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

DEST="${DEST_ROOT}/sql-reviewer"

if [ ! -f "${SRC_DIR}/SKILL.md" ]; then
  echo "error: ${SRC_DIR}/SKILL.md not found." >&2
  echo "Run this script from inside a clone of the claude-sql-reviewer repository." >&2
  exit 1
fi

if [ -e "$DEST" ] && [ "$FORCE" -ne 1 ]; then
  echo "A skill is already installed at:"
  echo "  $DEST"
  printf 'Overwrite it? [y/N] '
  read -r reply </dev/tty || reply=""
  case "$reply" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Aborted. Nothing was changed."; exit 0 ;;
  esac
fi

mkdir -p "$DEST_ROOT"
rm -rf "$DEST"
cp -R "$SRC_DIR" "$DEST"

echo "Installed to: $DEST"
echo
echo "Start a new Claude Code session and run:  /sql-reviewer"
