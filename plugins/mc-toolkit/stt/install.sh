#!/bin/bash
# Symlink mc-stt onto PATH (~/.local/bin is already on PATH; matches wacli/todoist).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-$HOME/.local/bin}"
mkdir -p "$DEST"
ln -sf "$HERE/bin/mc-stt" "$DEST/mc-stt"
echo "linked $DEST/mc-stt -> $HERE/bin/mc-stt"
command -v mc-stt >/dev/null && echo "on PATH ✓" || echo "WARN: $DEST not on PATH"
