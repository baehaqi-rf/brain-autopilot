#!/usr/bin/env bash
# ==============================================================================
# Brain Autopilot - Installer (Linux / macOS / Cloud Shell)
# ==============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_SKILL="$REPO_DIR/skills/brain-autopilot"
CONFIG_DIR="$HOME/.gemini/config"
TGT_SKILL="$CONFIG_DIR/skills/brain-autopilot"
TGT_AGENTS="$CONFIG_DIR/agents"
ARCHIVE="$HOME/.gemini/archived_skills"
STAMP="$(date +%Y%m%d-%H%M%S)"

archive() { mkdir -p "$ARCHIVE"; mv "$1" "$ARCHIVE/$2-$STAMP"; echo "  archived: $1"; }

[ -f "$SRC_SKILL/SKILL.md" ] || { echo "SKILL.md not found under $SRC_SKILL" >&2; exit 1; }
echo "Installing Brain Autopilot from $REPO_DIR"

mkdir -p "$(dirname "$TGT_SKILL")"
if [ -L "$TGT_SKILL" ]; then
  rm "$TGT_SKILL"
elif [ -d "$TGT_SKILL" ]; then
  archive "$TGT_SKILL" "brain-autopilot"
fi
ln -s "$SRC_SKILL" "$TGT_SKILL"
echo "Linked skill: $TGT_SKILL -> $SRC_SKILL"

mkdir -p "$TGT_AGENTS"
cp "$REPO_DIR/agents/brain-autopilot.md" "$TGT_AGENTS/"
echo "Installed agent: $TGT_AGENTS/brain-autopilot.md"

python3 -m pip install -e "$REPO_DIR" --quiet || true

[ -f "$TGT_SKILL/SKILL.md" ] || { echo "Self-check failed" >&2; exit 1; }
echo "Done. Type /brain-autopilot in Google Antigravity."
