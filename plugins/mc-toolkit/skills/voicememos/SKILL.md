---
name: voicememos
description: Sync Apple Voice Memos into local, speaker-labeled transcripts. Reads the TCC-protected Voice Memos container via a Full-Disk-Access snapshot app, transcribes each recording locally with mlx-whisper + silero-VAD, diarizes with pyannote, and labels a known speaker from an enrolled voiceprint — one folder per memo. All local/offline. Use for "/voicememos", "sync my voice memos", "transcribe my voice memos", "pobierz notatki głosowe", "zsynchronizuj voice memos", "kto to mówił w nagraniu", or to enroll/identify a voice. Do NOT use for live mic recording, audio files that aren't Apple Voice Memos.
version: 1.0.0
date: 2026-06-10
allowed-tools: Bash, Read, Write, Edit
---

# voicememos

Pulls Apple Voice Memos off this Mac into local, speaker-labeled transcripts —
one folder per memo, fully **local/offline** (Voice Memos has no cloud API).
Architecture, design rationale, and the verified-test record live in `CLAUDE.md`
(this dir).

## Status — working end-to-end

`sync.py` reads `CloudRecordings.db`, transcribes + diarizes + identifies, and writes
per-memo folders. Once a voiceprint is enrolled, clusters are labeled (known speaker vs
the unknown label). Requires FDA granted to the snapshot app (`references/fda-setup.md`).

**Default = active library only.** sync processes only `ZEVICTIONDATE IS NULL` rows
(what's actually in the Voice Memos library). Evicted rows — a mix of deleted and old
offloaded recordings whose `.m4a` may still linger in the container — are skipped so we
don't resurrect deleted memos. `--include-evicted` opts into the archive.

**Known limitation (iCloud sync):** recordings made on iPhone/iPad can sync their
*metadata* to the Mac but not their *audio* (`ZPATH` null / cloud-only). The
audio-pending ones get a metadata stub and retry once their audio downloads (open the
memo in Voice Memos.app on the Mac, or fix the underlying sync). **Deleted recordings ARE
detectable**: `ZEVICTIONDATE` set = in Recently Deleted (the evicted rows that still
have local audio correspond to what's in Recently Deleted). `--include-evicted` reaches
that archive (audio retained ~30 days). See `CLAUDE.md`.

## Setup (one time)

1. **FDA snapshot app** — follow `references/fda-setup.md` (build the app → grant
   Full Disk Access). This is what lets the FDA-less sync read the protected container.
2. **HF gated model** — `HF_TOKEN` in `~/.claude/.env`, and accept terms once for
   [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1).
3. **pyannote venv** (if missing): `python3.10 -m venv ~/.venvs/diarization && ~/.venvs/diarization/bin/pip install "pyannote.audio>=4.0"`.

## Commands

The two heavy steps run in separate Python environments (see `CLAUDE.md` for why):
`~/.venvs/diarization/bin/python` for diarize/enroll/identify, `/opt/homebrew/bin/python3.14` for transcribe.

```bash
# full sync (after FDA granted)
python3 scripts/sync.py

# enroll your voice once (clean single-speaker samples; your solo memos are ideal)
~/.venvs/diarization/bin/python scripts/enroll.py <your-name> clip1.wav clip2.wav

# diarize + name an arbitrary clip (add --words words.json to relabel whisper words)
~/.venvs/diarization/bin/python scripts/identify.py audio.wav --num-speakers 2
```

With no voiceprints enrolled, speakers stay anonymous (`SPEAKER_00/01`) — it
degrades gracefully to plain diarization.

## Diarization accuracy tiers

- **Solo memo** → `--num-speakers 1` (no diarization needed).
- **2–4 clean speakers** → local `community-1` (default; near-cloud on clean audio).
- **Messy / phone / overlapping / far-field** → cloud engine `assemblyai.py` (universal-2,
  speaker_labels, ~$0.37/audio-hr). On long noisy phone calls it clearly beat the local
  pipeline on BOTH transcription and diarization. Run under the venv for `--label`
  (voiceprint → known speaker / unknown label): `~/.venvs/diarization/bin/python assemblyai.py audio.m4a --label --out transcript_aai.md`.
  (pyannoteAI `precision-2` is an alternative local-API swap in `diarize.py` if ever needed.)

## Privacy — sensitive recordings stay LOCAL

The local pipeline (whisper + pyannote + wespeaker) is **fully on-device** — audio never
leaves the Mac (the HF token only authorizes a model *download*, not an upload). The cloud
engines (`assemblyai.py`, `elevenlabs.py`) **upload the audio**, both **train on it by
default**, and retain it (AssemblyAI: transcript stored indefinitely unless deleted;
ElevenLabs: no published TTL, and STT data is **not** API-deletable on a standard account).
**Rule: never point a cloud engine at a sensitive recording — use local only.** For
non-sensitive audio, the cloud is self-cleaning so there's no per-use chore:
- **`assemblyai.py` auto-deletes the transcript from AssemblyAI right after fetching**
  (default; audio is auto-deleted post-transcription anyway). `--keep-cloud` opts out.
  So nothing lingers on their servers — no manual delete, no email.
- **`elevenlabs.py` canNOT auto-delete** (STT isn't API-deletable on a standard account)
  and ElevenLabs retains by default → **prefer AssemblyAI for the cloud tier**; only use
  Scribe for a one-off where you accept the retention.
- **One-time** (not per-use): opt out of training on each service (AssemblyAI email
  data-opt-out@…; ElevenLabs toggle or a GDPR request to legal@elevenlabs.io). Forward-looking.

Full detail, comparison table, and opt-out steps: `references/privacy-research.md`.

## Evaluation (measure, don't guess)

Engine choice should be a number, not a vibe — eyeballing two clips gave contradictory
results (cloud won one call, tied the other). `scripts/eval.py` (run under the venv)
computes WER/CER per engine vs hand-corrected references:
1. `eval.py --prepare <clip.m4a> --engine elevenlabs [--language en]` → drafts
   `<clip>.ref.txt` + registers it.
2. **Hand-correct** the draft against the audio, every word (never trust an engine's raw
   output as ground truth — it biases the eval). Set `profile` AND `language` in
   `<data-dir>/eval/clips.json` — if you record in multiple languages, forcing the
   wrong one mangles the transcript (an English memo whisper'd with `pl` lost half its
   words). **Same caveat for sync**: sync.py uses `VOICEMEMOS_LANG` (default `en`).
3. Repeat for ~6 clips: `solo-clean` / `meeting-clean` / `phone-noisy` × 2.
4. `eval.py` → markdown WER/CER table (Polish-normalized, keeps diacritics) per engine × profile.

Engines compared: `whisper-large-v3` / `whisper-turbo` (local), `assemblyai`, `elevenlabs`.
Methodology + the research behind it: `references/quality-research.md`.

## Output contract

```
<data-dir>/<YYYY-MM-DD>-<slug>/
├── transcript.md     # metadata block + ---, then **Speaker** [MM:SS] turns
├── meta.json         # title, date, duration, source path, speaker map
├── data.json         # labeled words — lets render.py restyle transcript.md instantly
└── audio.m4a         # the recording (copied from the snapshot)
```

## Files

- `scripts/sync.py` — orchestrator: snapshot → enumerate → transcribe → diarize → identify → per-memo folder (incremental state)
- `scripts/render.py` — re-render transcript.md from data.json (instant restyle, no re-pipeline)
- `scripts/snapshot_trigger.sh` — triggers the FDA app, freshness-gated
- `scripts/transcribe.py` — local STT: mlx-whisper large-v3 + silero-VAD (python3.14 env)
- `scripts/assemblyai.py` — cloud engine (AssemblyAI) for messy/phone audio → transcript_aai.md
- `scripts/elevenlabs.py` — cloud engine (ElevenLabs Scribe), lead Polish/phone engine → transcript_el.md
- `scripts/diarize.py` — pyannote diarization + word→speaker overlap (nearest-turn fallback)
- `scripts/speaker_id.py` — voiceprint embeddings + match; narrowband detection + per-condition threshold
- `scripts/enroll.py` (`--phone-aug` multi-condition) / `scripts/identify.py` — CLIs over `speaker_id`
- `references/fda-setup.md`, `references/quality-research.md`, `references/VoiceMemosSnapshot.applescript`
