#!/bin/bash
# Cockpit status line for Claude Code — installer.
#
# Copies the script into your Claude config directory and points settings.json
# at it. Idempotent: re-run it any time to upgrade or repair. It touches only
# the `statusLine` key and never modifies anything else in settings.json.
#
#   bash install.sh
#
# Uninstall: see the bottom of this file, or README.md.

set -euo pipefail

here=$(cd -- "$(dirname -- "$0")" && pwd)
SRC="$here/statusline-command.sh"
CONF="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
DEST="$CONF/statusline-command.sh"
SETTINGS="$CONF/settings.json"

die() { printf 'install: %s\n' "$1" >&2; exit 1; }

[ -f "$SRC" ] || die "statusline-command.sh not found next to this installer."
command -v jq >/dev/null 2>&1 || die "jq is required — install it first (brew install jq, or apt-get install jq)."
command -v bash >/dev/null 2>&1 || die "bash is required."

mkdir -p "$CONF"

# --- back up whatever we are about to replace -------------------------------
stamp=$(date +%Y%m%d-%H%M%S)

if [ -f "$DEST" ]; then
  cp "$DEST" "$DEST.bak-$stamp"
  printf 'backed up  %s -> %s\n' "$DEST" "$DEST.bak-$stamp"
fi

if [ -f "$SETTINGS" ]; then
  jq empty "$SETTINGS" 2>/dev/null || die "$SETTINGS is not valid JSON — fix or move it, then re-run."
  cp "$SETTINGS" "$SETTINGS.bak-$stamp"
  printf 'backed up  %s -> %s\n' "$SETTINGS" "$SETTINGS.bak-$stamp"
else
  printf '{}\n' > "$SETTINGS"
fi

# --- install the script ----------------------------------------------------
cp "$SRC" "$DEST"
chmod +x "$DEST"
printf 'installed  %s\n' "$DEST"

# --- point statusLine at it ------------------------------------------------
# An absolute, quoted path, because the command runs through a shell and $HOME
# can contain spaces. `command` is replaced wholesale, so a stale path from an
# earlier install is overwritten rather than left behind.
cmd="bash \"$DEST\""
tmp=$(mktemp)
jq --arg cmd "$cmd" '.statusLine = {type: "command", command: $cmd}' "$SETTINGS" > "$tmp"
mv "$tmp" "$SETTINGS"
printf 'configured statusLine -> %s\n' "$cmd"

cat <<'EOF'

Done. Open a NEW Claude Code session to see it — settings load at startup.

  Opus 5.5  | medium  tok 290k/1M (29%) ■■■■■■■················ $9.54
  ~/AI/CODEX · main*

Note: the first launch in a folder asks you to accept the workspace trust
dialog. Until you do, the status line stays blank. That is Claude Code
behaviour for all status line commands, not something this script controls.

Uninstall:
  jq 'del(.statusLine)' ~/.claude/settings.json > /tmp/s.json \
    && mv /tmp/s.json ~/.claude/settings.json
EOF
