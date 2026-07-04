---
name: voicememos
description: Sync Apple Voice Memos into local, speaker-labeled transcripts. Reads the TCC-protected Voice Memos container via a Full-Disk-Access snapshot app, transcribes each recording locally with mlx-whisper + silero-VAD, diarizes with pyannote, and labels a known speaker from an enrolled voiceprint — one folder per memo. All local/offline. Use for "/voicememos", "sync my voice memos", "transcribe my voice memos", "pobierz notatki głosowe", "zsynchronizuj voice memos", "kto to mówił w nagraniu", or to enroll/identify a voice. Do NOT use for live mic recording, audio files that aren't Apple Voice Memos.
version: 1.1.0
date: 2026-06-11
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
3. **pyannote venv** (if missing): `python3.10 -m venv ~/.venvs/diarization && ~/.venvs/diarization/bin/pip install "pyannote.audio>=4.0"`. (Used for the voiceprint-naming step always, and for diarization when `VOICEMEMOS_DIAR_ENGINE=pyannote`.)
4. **mlx-audio** (for the default Sortformer diarizer): `/opt/homebrew/bin/python3.14 -m pip install --break-system-packages mlx-audio`. The MLX Sortformer model auto-downloads on first run.

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

## Post-sync routing flow (in-session)

After `sync.py` finishes, the skill (this session) processes every memo whose
`meta.json` has `status: needs-routing`, one at a time. Never batch the decisions.

### Step 0 — Quality gate + escalation
For each memo, read `meta.json` `transcript_health`:
- `healthy` → proceed to Step A.
- `empty` → already `status: archived`; confirm with the user before finalizing (v1 ZAPYTAJ)
  — this is where trust in the empty-detector is earned; only graduate to silent-auto later.
- `suspect` → judge SENSITIVITY from the (partial) local transcript. Pick the highest rung the
  sensitivity allows (sensitive → max OpenAI; non-sensitive quality-critical → up to ElevenLabs).
  ALWAYS ask before sending: "słaby transkrypt, temat wygląda na X, proponuję <engine>
  (<privacy one-liner>) — ok?". On approval run `escalate.py`, then re-run the quality gate;
  if still `suspect`, set `status: needs-attention` and surface it.
Never send sensitive audio (health/therapy/intimacy/finance/family) past OpenAI.

### Step A — Auto-title (content, not name)
For each memo, read the FULL `transcript.md` and produce a short descriptive
Polish title (≤ 6 words, names the topic/people, e.g. "Adam — projekt i inwestycje").
Then:
1. `python3 scripts/route.py`-backed rename: call `route.rename_memo(memo_dir, route.safe_slug(title))`.
2. Write both titles into the (possibly moved) `meta.json`: set `generated_title` = the new title,
   keep `original_title` as-is.
Skip renaming for `status: archived` (empty) memos — they keep their date-slug.

### Step B — Route (rules from the text file)
1. Load `<data-dir>/routing-rules.md` (the private criterion→action table).
2. For each `needs-routing` memo, read the FULL `transcript.md`, match it against the
   criteria, and pick the applicable action(s). If none match, propose a best-guess disposition.
3. If the matched action says `NIE pytaj` → execute it. If `ZAPYTAJ` (v1 default: all) →
   present the proposal (category read from content, the concrete action, target paths) and
   wait for approve / edit / execute.
4. Execute the action: simple inline (archive, create Todoist task, file a short note),
   or hand off to the domain skill for complex work (e.g. specjalista → intimacy flow,
   projekt → skill-korporacyjny). Record what you did.
5. Call `route.write_disposition(memo_dir, "routed", "<free-text: what was done and where>")`.
   The free text is the durable "co zrobiono i dokąd" — be specific (targets, task links).

## Diarization accuracy tiers

**Default local diarizer = NVIDIA Sortformer (end-to-end, via mlx-audio)** since 2026-06-16
(`VOICEMEMOS_DIAR_ENGINE=sortformer`). It predicts the speaker count NATIVELY (no
`--num-speakers` hint, no clustering threshold), handles overlap, runs on Metal in
~0.1–0.2 s/clip (vs pyannote-on-CPU's minutes), and **separates close-mic speakers that
pyannote MERGED** — on a 2-speaker gym clip pyannote collapsed to 1, Sortformer correctly
found 2 (validated 5/5 speaker-count across solo/phone/gym clips after a <2 s phantom
filter), and the cleaner separation also let the voiceprint step correctly name the operator
where pyannote's merged clusters had failed. Caps at 4 speakers (fine for memos). Set
`VOICEMEMOS_DIAR_ENGINE=pyannote` to fall back to the clustering pipeline. `sortformer_diarize.py`
runs in the mlx env and emits turns; `identify.py --turns` (venv) does naming + word assignment.
**Long files (>600 s) auto-switch to `generate_stream` (180 s chunks, bounded spkcache)** —
the one-shot `model.generate` path runs O(seq_len²) self-attention over the WHOLE file and
its peak MLX memory grows quadratically (measured: 60 s=1.1 GB, 600 s=9.5 GB, 900 s=18.8 GB →
~520 GB projected for a 78-min memo), which OOMs even a 128 GB Mac (`libc++abi: terminating`).
Streaming holds ~5 GB flat regardless of length and keeps speaker identity across chunks. The
600/180 s thresholds are `STREAM_OVER_S`/`STREAM_CHUNK_S` constants at the top of
`sortformer_diarize.py` (edit there to tune). Distinct unnamed
speakers stay distinct: unmatched clusters get suffixed labels (`inny 1`, `inny 2`, …), NOT one
collapsed `unknown_label` (which made `render.py` merge several real voices into one block).

- **Solo memo** → either backend is trivially correct (Sortformer returns 1; pyannote `--num-speakers 1`).
- **2–4 speakers** → Sortformer (default) — best on close-mic/overlap; pyannote `community-1` is the fallback.
- **Messy / phone / overlapping / far-field** → cloud engine. Measured on Polish
  phone-call + solo clips (eval 2026-06-11, small n): on phone clips ElevenLabs ~10
  mean WER, OpenAI ~17, local whisper ~21, AssemblyAI ~29 (its VAD silently drops
  quiet/narrowband segments — use `--loudnorm`, which cut one clip 46.7→34.9 WER); on
  clean solo clips AssemblyAI ≈ ElevenLabs (~6) ≈ openai-mini (~8) beat local whisper
  (~13–19). Caveat: gpt-4o-transcribe CLEANS speech (drops fillers/false starts),
  which inflates its WER vs verbatim references. So cloud beats local on quality, but
  **privacy decides the engine** (next section).
- **NOTE (2026-06-14): the local-whisper numbers above are PRE-FIX.** They were measured
  with the old per-VAD-fragment pipeline, which starved whisper of context and roughly
  DOUBLED Polish WER. `transcribe.py` now transcribes VAD-grouped ~28 s contiguous windows
  ("vadwin"): mean WER on the gold clips dropped **24.4 → 11.7**, and on hard real-world
  audio (gym/car) divergence-vs-gpt-4o **44 → 34**. Same model (large-v3) — pure config.
  So local is now meaningfully closer to cloud, but on genuinely hard audio cloud still
  wins. Tested & rejected: `initial_prompt`/glossary (HURTS — repetition loops),
  whisper-turbo and Parakeet-TDT-0.6b-v3 (both worse on PL; Parakeet emitted NOTHING on
  the noisy car clip). Detail: `eval/results-2026-06-14-local-quality.md`. AssemblyAI usage:
  `~/.venvs/diarization/bin/python assemblyai.py audio.m4a --label --out transcript_aai.md`
  (universal-2, speaker_labels, ~$0.37/audio-hr; venv needed for `--label`
  voiceprint → names).

## OpenAI engine — duration limits + chunking (long Voice Memos)

`openai.py` handles one file, but `sync.py` uses LOCAL whisper — to transcribe a long
memo via OpenAI you drive `openai.py` per chunk yourself. Hard limits hit live
(2026-06-14, 62-min trainer session + 30-min lecture):
- **`gpt-4o-transcribe` / `-mini`: max 1400 s/file** (error `audio duration … longer than 1400 seconds`). Chunk to ≤1200 s.
- **`gpt-4o-transcribe-diarize`: 1400 s nominal, but on chunks ≳600 s the curl returns EMPTY (server-side timeout) — looks like a corrupt-file / JSON-decode error, not a clear message.** Chunk diarize to **≤600 s** and add a curl retry (`--max-time` + 2-3 attempts on empty stdout).
- **All endpoints: 25 MB upload cap.** Re-encode first — `ffmpeg -i in.qta -ac 1 -ar 16000 -b:a 32k out.mp3` (mono 16 kHz ~32 kbps) takes a 250 MB `.qta` to ~15 MB with zero ASR loss.
- **`language` is a HINT, not a hard force** — `gpt-4o-transcribe` with `language=pl` still transcribes English passages correctly (bilingual memos survive). Probe a 60 s slice to detect language before committing.
- **Diarize cross-chunk labels are NOT stable** (each chunk re-assigns A/B/C…; also over-splits 2 speakers into 4-5). For a 2-person memo, attribute by CONTENT, not the letter labels.
- **`.qta`** is the Voice Memos container extension for edited/long recordings — ffmpeg reads it fine.

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
  and ElevenLabs retains by default → only use Scribe for a one-off where you accept
  the retention (it is, however, the measured quality leader on phone audio).
- **`openai.py` has the best cloud privacy defaults**: OpenAI does NOT train on API
  data and auto-deletes after ≤30 days (abuse monitoring) — no opt-out, no cleanup.
  Trade-off: the quality models (gpt-4o-transcribe/-mini) return text only — speakers
  need the local pyannote pass. `--model gpt-4o-transcribe-diarize` adds built-in
  speakers but measurably worse text (phone ~30 vs mini ~17 mean WER) — convenience,
  not a quality pick.
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
   output as ground truth — it biases the eval). References may be speaker-labeled
   (`MC: ` / `ANNA: ` turn-per-paragraph; WER strips the labels, and they double as
   speaker-attribution ground truth). Set `profile` AND `language` in
   `<data-dir>/eval/clips.json` — if you record in multiple languages, forcing the
   wrong one mangles the transcript (an English memo whisper'd with `pl` lost half its
   words). sync.py defaults to per-memo auto-detection (`VOICEMEMOS_LANG=auto`,
   detected once on the longest speech segment; stored in meta.json).
3. Repeat for ~6 clips: `solo-clean` / `meeting-clean` / `phone-noisy` × 2.
4. `eval.py` → markdown table per engine × profile: WER strict + WER lenient (ignores
   filler/repeat style) + CER (all Polish-aware normalized, keeps diacritics).
   Hypotheses cache under `<data-dir>/eval/hyps/` — re-scoring is free and uploads
   nothing; `--fresh` re-runs engines. **Cloud engines are stochastic**: run-to-run
   swings up to ~20 WER pts on a single clip (measured) — treat single-run single-clip
   differences <~10 pts as noise; only local whisper is deterministic. Average several
   `--fresh` runs before trusting a close call.

Engines compared: `whisper-large-v3` / `whisper-turbo` (local), `assemblyai`,
`elevenlabs`, `openai` / `openai-mini` / `openai-diarize`.
Methodology + the research behind it: `references/quality-research.md`.

## Clean transcript (optional, derived)

On request ("make a clean version") — or when a memo feeds a downstream deliverable —
generate `transcript_clean.md` next to `transcript.md` via a subagent (zero marginal
cost). Rules for the subagent:
- Fix obvious ASR mishears from context; use names from `meta.json` (`speakers`,
  title) and the user's domain glossary.
- MAY drop fillers and false starts, fix punctuation, merge into readable paragraphs
  per speaker turn. May NOT summarize, drop substantive content, or reorder. Mark
  guesses `[?]`.
- `transcript.md` (verbatim) stays the source of truth — the clean file is a derived
  rendering. Measured: LLM post-editing improves WER only ~1 pt (it can't recover
  what ASR never heard) — this step is for READABILITY, not accuracy.

## Output contract

```
<data-dir>/<YYYY-MM-DD>-<slug>/
├── transcript.md         # metadata block + ---, then **Speaker** [MM:SS] turns
├── transcript_clean.md   # optional, derived — LLM-cleaned readable version (see above)
├── meta.json             # title, date, duration, language (auto-detected), source path, speaker map
├── data.json             # labeled words — lets render.py restyle transcript.md instantly
└── audio.m4a             # the recording (copied from the snapshot)
```

## Files

- `scripts/sync.py` — orchestrator: snapshot → enumerate → transcribe → diarize → identify → per-memo folder (incremental state)
- `scripts/render.py` — re-render transcript.md from data.json (instant restyle, no re-pipeline)
- `scripts/snapshot_trigger.sh` — triggers the FDA app, freshness-gated
- `scripts/transcribe.py` — local STT: mlx-whisper large-v3 + silero-VAD (python3.14 env)
- `scripts/assemblyai.py` — cloud engine (AssemblyAI) for messy/phone audio → transcript_aai.md
- `scripts/elevenlabs.py` — cloud engine (ElevenLabs Scribe), measured phone-quality leader → transcript_el.md
- `scripts/openai.py` — cloud engine (OpenAI gpt-4o-transcribe family), best cloud privacy defaults
- `scripts/eval.py` — WER/CER harness: hand-corrected references, strict+lenient WER, hypothesis cache
- `scripts/sortformer_diarize.py` — DEFAULT diarizer: end-to-end NVIDIA Sortformer (mlx-audio, mlx env) → turns; native speaker count, fast on Metal
- `scripts/diarize.py` — fallback diarizer: pyannote clustering + word→speaker overlap (`VOICEMEMOS_DIAR_ENGINE=pyannote`)
- `scripts/speaker_id.py` — voiceprint embeddings + match; narrowband detection + per-condition threshold
- `scripts/enroll.py` (`--phone-aug` multi-condition) / `scripts/identify.py` — CLIs over `speaker_id`
- `references/fda-setup.md`, `references/quality-research.md`, `references/VoiceMemosSnapshot.applescript`
