---
name: voicememos
description: |
  DEPRECATED — the voicememos engine moved into the `ingest` skill (10cfi-base plugin), so memos now
  flow through the unified ingest store + routing. This is a thin redirect kept so the old triggers
  still resolve. Fires on "/voicememos", "sync my voice memos", "transcribe my voice memos", "pobierz
  notatki głosowe", "kto to mówił w nagraniu" — then points you at ingest.
version: 2.0.0
date: 2026-07-16
---

# voicememos → moved into `ingest`

The Apple Voice Memos engine is now a source inside the **`ingest`** skill (in the `10cfi-base`
plugin), unified with the other capture sources. Nothing about the local pipeline changed — same
FDA snapshot, local mlx-whisper (via the shared `mc-stt` layer), diarization, and voiceprint naming.

**Sync:**

```bash
python3 ~/.claude/skills/10cfi-base/skills/ingest/voicememos/scripts/sync.py
```

Full detail lives in the ingest skill's `voicememos/GUIDE.md`.

*(This redirect will be removed once the migration settles.)*
