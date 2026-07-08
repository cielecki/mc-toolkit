# voicememos — editor notes

Maintenance-mode notes (auto-loaded only when editing files in this dir). Runtime
contract is in `SKILL.md`. No changelog here — history lives in `git log`.

## Why everything is local

Cloud-backed transcript sources expose a REST API, so a sync needs no local file
access. Apple Voice Memos is the inverse: it has **no cloud/REST API** — the data is
purely on-device in a TCC-protected group container
(`~/Library/Group Containers/group.com.apple.VoiceMemos.shared/`, `CloudRecordings.db`
+ `.m4a` files), and the audio has **no transcript** (Apple's on-device transcription
isn't exposed as a readable file). So everything is local: read the protected DB, copy
the audio, transcribe + diarize + identify ourselves.

## Pipeline

```
snapshot_trigger.sh  → VoiceMemosSnapshot.app (FDA) rsyncs the container locally
   │
CloudRecordings.db   → enumerate recordings (title, folder, date, duration, m4a path)
   │  per new/changed recording:
   ├─ transcribe.py        (mlx-whisper + silero-VAD, mlx env)    → words[] (ms)
   ├─ sortformer_diarize.py (Sortformer end-to-end, mlx env)      → speaker turns[]  [DEFAULT]
   │   (or diarize.py — pyannote community-1, CPU venv — when VOICEMEMOS_DIAR_ENGINE=pyannote)
   └─ identify.py          (match turns to enrolled voiceprints)  → known speaker / unknown
   │
write <data-dir>/<date>-<slug>/{transcript.md, meta.json, audio.m4a}
```

`<data-dir>` resolves via `_config.cfg("VOICEMEMOS_DATA", …)` (env / `~/.claude/.env`
/ `config.local.json` / default `~/voicememos`).

## Two Python environments (kept separate on purpose)

- **mlx env** = `/opt/homebrew/bin/python3.14` (`VOICEMEMOS_MLX_PYTHON`) — `mlx-whisper`,
  `silero-vad`, `torch`, **`mlx-audio`**. Runs `transcribe.py` AND `sortformer_diarize.py`
  (both Metal/MLX). MLX beats CPU-only faster-whisper on Apple Silicon, and Sortformer-on-MLX
  beats pyannote-on-CPU on speed (~1000×) and on close-mic speaker separation — so the default
  diarizer now lives HERE, not in the venv.
- **pyannote venv** = `~/.venvs/diarization` (`VOICEMEMOS_VENV_PYTHON`; py3.10,
  `pyannote.audio` 4.0.4). Runs enroll/identify (voiceprint naming, ALWAYS) + the FALLBACK
  diarizer `diarize.py` (when `VOICEMEMOS_DIAR_ENGINE=pyannote`). **CPU, not MPS** — torch+MPS
  is broken for pyannote (unimplemented sparse ops; errors or silent-slow CPU fallback).
  Pinned in `diarize.py`. Kept in its own venv so torch never collides with the mlx env;
  big (~torch), lives outside git. NOTE: Sortformer's win is partly WHY we no longer depend on
  this slow CPU path by default — `identify.py --turns` consumes Sortformer's turns and only the
  (fast) voiceprint embedding runs in the venv.

## Data + venv live OUTSIDE git (by design)

The skill folder (code) is version-controlled. Personal/biometric data is not, and
must never be committed (even to a private repo): `<data-dir>/snapshot/` (container
mirror), `<data-dir>/<date>-<slug>/` (per-memo output), `<data-dir>/voiceprints/<name>.npy`
(enrolled voiceprints — biometric data of real people). The pyannote venv is a build
artifact, not source. The FDA-holding `.app` lives in `~/Applications/`, not the repo.

## Speaker identification design

Diarization gives anonymous clusters; identification names them. We embed each
cluster (pyannote/wespeaker-voxceleb-resnet34-LM, 256-d — same family community-1
uses, ungated) and cosine-match against enrolled voiceprints. Threshold 0.5.
Default scope: enroll one known voice → label that name vs the unknown label
(`VOICEMEMOS_UNKNOWN_LABEL`). Extensible to more people by enrolling more voiceprints
(identify.py already handles N).

## Room-mic group meetings are un-diarizable (verified 2026-07)

A multi-person meeting recorded on a single phone (room-mic) captures only the person
NEAR the mic. NO diarizer separates the far-field others: verified on a 12-person
workshop with Sortformer, AssemblyAI + `--loudnorm`, an aggressive `dynaudnorm` boost,
AND raw AssemblyAI (no `--label`) — all returned one dominant speaker + a handful of
1–2 word "other" turns that were mostly the near-mic speaker's OWN utterances mis-split
("Po" → "pierwsze", "Czyli" → "mamy decyzję"). Far-field speech below the noise floor
can't be recovered by boosting — physics wins, don't burn API calls chasing it.
- `--label` with a SINGLE enrolled voiceprint collapses every cluster to that one name
  (looks like "everyone is the operator"). On a room-mic monologue that's cosmetically wrong but
  harmless — just know it isn't real multi-speaker separation; keep unmatched clusters as
  `inny N`, never force them onto the one enrolled name.
- The real multi-voice record of such a meeting lives in the **video-call platform**
  (Fathom = per-participant audio → clean speaker names), NOT the room-mic. Before trying
  to diarize a group memo, check Fathom (`fathom-sync`). If there's no Fathom recap email,
  nothing in the sync, and nothing in Gmail from any attendee → it was an IN-PERSON
  room-mic recording → treat it as the near-mic person's monologue; don't chase a
  multi-voice version that doesn't exist.

## Verified-test record (audio core)

A controlled 2-speaker clip (macOS `say`: two distinct voices, alternating, with known
ground-truth boundaries) validated the pipeline:
- **Diarization**: boundaries within ~50 ms of ground truth; 2 speakers, correctly
  attributed, consistently labeled; also correct with auto-detect (no `--num-speakers`).
- **Embedding discrimination**: same-speaker cosine ~0.90, different-speaker ~0.10.
- **Full join**: enrolled the known voice → `SPEAKER_00→<name>` (0.99) / `SPEAKER_01→unknown`
  (0.13); mlx-whisper words relabeled to names correctly.
Any synthetic stand-in voiceprint should be removed after testing — real enrollment is
the user's own voice.

## CloudRecordings.db schema

Table `ZCLOUDRECORDING` (Core Data). Columns sync.py uses:
- `ZENCRYPTEDTITLE` — the visible title, **plaintext** here despite the name. `ZCUSTOMLABEL`
  holds the default ISO-timestamp name → fallback.
- `ZDATE` — Core Data epoch (seconds since 2001-01-01 UTC; +978307200 → unix).
- `ZDURATION` / `ZLOCALDURATION` — seconds.
- `ZPATH` — m4a filename under `Recordings/`; **null when the audio isn't local**.
- `ZUNIQUEID` — stable id (state key). `ZEVICTIONDATE` — set ≠ audio absent (don't
  use as the local-audio test; check `ZPATH` + file existence, which sync.py does).

Recordings are stored as `Recordings/<stamp>.m4a` (and `.composition` bundles for
edited ones). An iCloud sync edge case surfaces here: iPhone/iPad recordings can sync
metadata (row present) but not audio (`ZPATH` null).

## Active vs deleted — `ZEVICTIONDATE` IS the signal

**`ZEVICTIONDATE` = the Recently-Deleted auto-purge date, i.e. the recording was
DELETED** (not "offloaded"). Confirmed empirically: evicted rows that still have a local
`.m4a` correspond exactly to the recordings visible in the app's "Recently Deleted". They
retain audio because they're inside the ~30-day retention window; once purged the row goes
audio-less.

So:
- **Active library = `ZEVICTIONDATE IS NULL`** — matches what's in the main library. sync
  defaults here.
- **Deleted = `ZEVICTIONDATE` set** — reliably detectable. `--include-evicted` reaches the
  Recently-Deleted archive (audio still present until purged).

Earlier dead-end (don't repeat the mis-conclusion): the CloudKit change log (`ACHANGE`)
holds only hard-delete tombstones and `ZNEEDS{LOCAL,CLOUD}DELETE` track CloudKit
propagation — a DIFFERENT mechanism from Recently-Deleted soft-delete. Deletion lives in
`ZEVICTIONDATE`, not the tombstone log. (The `ZFOLDER` table is empty because
Recently-Deleted is a flag, not a folder row.)

`ZFLAGS` observations (not load-bearing, recorded for future): 0 ↔ cloud-only (ZPATH
null), 4 ↔ has local audio, 1540/1548 ↔ edited/`.composition` recordings.

## FDA-snapshot mechanism (shared gotchas)

The FDA-snapshot mechanism (`VoiceMemosSnapshot.app`) is the standard pattern for reading
any TCC-protected database (e.g. Messages' `chat.db`): a dedicated app in `~/Applications/`
(TCC won't resolve bundle IDs under dot-paths), a sidecar `.dest` file that decouples the
app from the skill location, and re-granting FDA (remove+re-add) after any
rebuild/re-signature. See `references/fda-setup.md`.

## transcribe.py provenance

`transcribe.py` is a proven mlx-whisper + VAD + hallucination-filter pipeline. `diarize.py`
originated as a standalone prototype; the skill copy is canonical.
