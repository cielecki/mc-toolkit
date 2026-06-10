#!/bin/bash
# Trigger the FDA-holding VoiceMemosSnapshot.app to mirror Apple Voice Memos'
# protected group container into the skill's snapshot dir. Uses the same
# FDA-snapshot freshness-gating pattern (don't trust a stale "success" marker).
#
# Usage: bash snapshot_trigger.sh
# Setup (one time): references/fda-setup.md

set -u
DATA="${VOICEMEMOS_DATA:-$HOME/voicememos}/snapshot"
SNAPSHOT_APP="$HOME/Applications/VoiceMemosSnapshot.app"
SIDECAR="$HOME/Applications/VoiceMemosSnapshot.dest"
LOG="$DATA/trigger.log"

mkdir -p "$DATA"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') voicememos snapshot =====" >> "$LOG"

if [ ! -d "$SNAPSHOT_APP" ]; then
  echo "  ERROR: $SNAPSHOT_APP missing — run the build in references/fda-setup.md" | tee -a "$LOG" >&2
  exit 2
fi

# Point the app at our snapshot dir (decouples app location from skill location).
printf '%s\n' "$DATA" > "$SIDECAR" 2>/dev/null || true

LOG_BEFORE=$(stat -f%m "$DATA/snapshot.log" 2>/dev/null || echo 0)
open -a "$SNAPSHOT_APP"

# Wait up to 60s for a fresh "snapshot done" block (rsync of audio can take a bit).
FRESH=0
for i in $(seq 1 120); do
  LOG_NOW=$(stat -f%m "$DATA/snapshot.log" 2>/dev/null || echo 0)
  if [ "$LOG_NOW" -gt "$LOG_BEFORE" ] && tail -8 "$DATA/snapshot.log" | grep -q "snapshot done"; then
    FRESH=1; break
  fi
  sleep 0.5
done

if [ "$FRESH" -eq 0 ]; then
  echo "  ERROR: snapshot did not refresh in 60s — FDA likely not granted." | tee -a "$LOG" >&2
  echo "  Fix: $(dirname "$0")/../references/fda-setup.md" | tee -a "$LOG" >&2
  exit 1
fi

LAST=$(awk '/snapshot start/{buf=""} {buf=buf"\n"$0} END{print buf}' "$DATA/snapshot.log")
if echo "$LAST" | grep -q "FAILED"; then
  echo "  ERROR: snapshot app ran but rsync FAILED (likely FDA not granted)." | tee -a "$LOG" >&2
  echo "$LAST" | sed 's/^/    /' >&2
  exit 1
fi
if ! echo "$LAST" | grep -q "rsync OK"; then
  echo "  ERROR: fresh block has no 'rsync OK' marker." | tee -a "$LOG" >&2
  echo "$LAST" | sed 's/^/    /' >&2
  exit 1
fi

DB=$(find "$DATA" -name "CloudRecordings.db" 2>/dev/null | head -1)
echo "  snapshot ok. CloudRecordings.db: ${DB:-NOT FOUND}" | tee -a "$LOG"
echo "Done. Snapshot at $DATA"
