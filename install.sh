#!/bin/bash
# Explicit opt-in installer. Not run during the repair or against ~/.claude.
# New bundle is activated atomically; settings/backups are recoverable.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
CONF="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
PYTHON="${CACHE_HISTORY_PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || { printf 'install: Python 3.9+ is required.\n' >&2; exit 1; }
command -v bash >/dev/null 2>&1 || { printf 'install: bash is required.\n' >&2; exit 1; }
# Validate runtime, including SQLite, before any writes.
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -c 'import sys, sqlite3; sys.exit(0 if sys.version_info >= (3, 9) else "install: Python 3.9+ is required")'
exec env PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$here/lib/install_support.py" "$here" "$CONF"
