# voicememos skill — one-time Full Disk Access setup

Reading Apple Voice Memos' data (`~/Library/Group Containers/group.com.apple.VoiceMemos.shared/`)
requires macOS **Full Disk Access (FDA)**. Claude Code can't hold FDA reliably (its
bundle path changes every version), so this skill uses a dedicated tiny app —
`VoiceMemosSnapshot.app` — that rsyncs the container into the skill's snapshot dir.
FDA is granted to **that** app once and persists across skill code edits.

This is the same FDA-snapshot mechanism used for any other TCC-protected database
(e.g. Messages' `chat.db`); if you've set one of those up, this is familiar.

## Build the app (from the skill root)

```bash
SKILL=~/.claude/skills/mc-toolkit/skills/voicememos
SRC="$SKILL/references/VoiceMemosSnapshot.applescript"
APP="$HOME/Applications/VoiceMemosSnapshot.app"
osacompile -o /tmp/VoiceMemosSnapshot.app "$SRC"
PB=/usr/libexec/PlistBuddy; PL=/tmp/VoiceMemosSnapshot.app/Contents/Info.plist
$PB -c "Add :CFBundleIdentifier string com.maciel.voicememos.snapshot" "$PL"
$PB -c "Add :CFBundleName string VoiceMemosSnapshot" "$PL"
$PB -c "Add :LSUIElement integer 1" "$PL"
codesign --force --deep -s - /tmp/VoiceMemosSnapshot.app
mkdir -p "$HOME/Applications"
rm -rf "$APP" && cp -R /tmp/VoiceMemosSnapshot.app "$APP"
echo "built $APP"
```

## Grant FDA (~30 seconds, one time)

1. Open **System Settings → Privacy & Security → Full Disk Access**
2. Click **+**, press **⌘⇧G**, paste `~/Applications/`
3. Select `VoiceMemosSnapshot.app` → **Open**
4. Toggle the entry **ON**

No reboot, no Claude Code restart needed.

## Why `~/Applications/` (not the skill folder)

macOS TCC won't resolve bundle IDs for apps under dot-prefixed paths. `~/Applications/`
is a canonical user-app location with a stable path that survives Claude Code updates.
Only the FDA-holding `.app` lives there; all skill code stays in the skill folder.

## Verify

```bash
bash ~/.claude/skills/mc-toolkit/skills/voicememos/scripts/snapshot_trigger.sh
```
Expect `Done. Snapshot at …` and a found `CloudRecordings.db`. If you get
`snapshot did not refresh` / `rsync FAILED`, FDA wasn't granted to the right path.

## After rebuilding the app

Re-signing invalidates the FDA grant. In Full Disk Access, **remove (−) and re-add (+)**
the rebuilt app (toggling off/on often doesn't take after a re-signature).

## Note on `--delete`

The snapshot rsync uses `--delete` so the local mirror tracks deletions in Voice Memos.
The per-memo OUTPUT (transcripts under `data/voicememos/<date>-<slug>/`) is written by
sync.py and is NOT inside the snapshot dir, so it is never deleted by the mirror.
