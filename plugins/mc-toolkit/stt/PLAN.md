# mc-stt — shared local-first speech-to-text

One transcription tool, many callers. Replaces the 5 copy-pasted mlx-whisper/cloud
implementations scattered across the setup with a single CLI on `PATH`, so a fix lands
once and a model/hallucination-filter choice lives in one place.

## Why it lives here

`mc-toolkit` is the STT center of gravity — the local mlx-whisper pipeline, the cloud
engines, and the eval harness already lived in `skills/voicememos/scripts/`. This dir
promotes them one level so they're shared, not voicememos-private. It sits **beside**
`skills/` (not inside a skill) because it's plumbing, not a model-invoked skill. Shipping
inside the plugin keeps the public `voicememos` skill self-contained (a marketplace user
gets the engine with the skill — no dangling PATH dependency).

## Boundary (what mc-stt is / isn't)

mc-stt is the **raw transcription layer**: `audio → {text} or {words[], text, language}`,
per engine, plus the shared hallucination/boilerplate filter. It does NOT do voicememos
-specific work — meta.json titles, `.md` formatting, `--label` voiceprint mapping, or
pyannote diarization. Those stay in the `voicememos` skill, which becomes a CONSUMER of
mc-stt. Clean split: mc-stt = "what was said"; voicememos = "whose memo, who said it,
how to render it."

## Interface

```
mc-stt <audio> [--engine local|openai|openai-mini|openai-diarize|elevenlabs|assemblyai]
               [--language pl|en|auto]   # default auto
               [--format text|json]      # text (default) | json {words[],text,language}
               [--model <override>]      # local: mlx repo id; openai: model name
```
- `--format json` returns the AssemblyAI-shaped contract voicememos diarization consumes:
  `words:[{text,start,end(ms),speaker:null,confidence}]`. Only `local` produces word
  timestamps today; cloud engines return `words:[]` + `text` unless they expose timings.
- Local engine = the VAD-windowed mlx-whisper large-v3 pipeline (the 2026-06-14 quality
  fix: ~2× fewer Polish errors vs per-fragment). No `initial_prompt` (measured: hurts).

## Install

`stt/install.sh` symlinks `stt/bin/mc-stt` → `~/.local/bin/mc-stt` (already on PATH,
matches the `wacli`/`todoist` precedent). Shebang pins the interpreter that has
mlx-whisper (`/opt/homebrew/bin/python3.14`), so cross-env callers (miniconda) get the
right runtime for free by just shelling out to `mc-stt`.

## Callers (migration)

| Caller | Repo | Status |
|---|---|---|
| voicememos local (transcribe.py → shim) | mc-toolkit | ✅ done — eval WER identical, sync/eval untouched |
| voicememos cloud scripts + eval cloud engines | mc-toolkit | ⏳ still call own openai/elevenlabs/assemblyai.py (not duplicated elsewhere → low pressure) |
| summarize-anything | mc-setup (`mc`) | ✅ done — recipe → `mc-stt`, manual chunking dropped |
| messaging/whatsapp transcribe-audio | mc-setup (`mc`) | ✅ done — recipe → `mc-stt` |
| space-survivor-ios whisper_words.py | apps/space-survivor-ios | ✅ done — thin shim over `mc-stt --model turbo` (couples that repo to PATH `mc-stt`) |
| **voice-mode** | mc-setup (`mc`) | **EXCLUDED** — Swift rewrite in progress (do not touch) |

All LOCAL-whisper callers now route through one engine. Remaining is optional: cloud
engines in mc-stt (no current duplication to remove — single copy in voicememos) + the
warm-server Phase 2.

## Build order (additive, regression-gated)

1. `engines/_filter.py` — shared boilerplate/hallucination drop (one copy). ✅
2. `engines/local.py` — VAD-windowed mlx-whisper, importable `transcribe()`. ✅
3. `bin/mc-stt` — CLI dispatcher. ✅
4. Cloud engines (`openai`/`elevenlabs`/`assemblyai`) — raw `transcribe()` only.
5. `install.sh`.
6. Verify: `mc-stt clip --format json` byte-identical to voicememos `transcribe.py`; eval WER unchanged.
7. Wire voicememos to mc-stt (sync.py, eval.py, cloud scripts become thin consumers).

Phase 2 (later): a warm `ModelHolder` server so the model stops reloading per call
(~2-5s cold each). Not needed for the dedup win.
