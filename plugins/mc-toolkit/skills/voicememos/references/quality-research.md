# Transcription/diarization quality + evaluation — research (2026-06-09)

Research backing the engine/quality decisions. Read before changing the STT/diarization
engine, adding preprocessing, or building the eval harness. Every nontrivial claim has a URL.

## Headline: route by audio profile (empirical, not aesthetic)

On **clean** Polish, local Whisper-large and the cloud engines are ~tied; on **real
noisy/overlapping** Polish, Whisper collapses while a top cloud engine holds. AGH Kraków
preprint (2026-03-04), real doctor–patient Polish (spontaneous, overlapping):
**Whisper-large normalized WER 42.26% / raw 53.83% vs ElevenLabs Scribe 10.58% / 12.06%**
— ~4× gap on the profile that resembles phone calls. On clean read speech they're within
~1–2 pts. Source: https://www.arxiv.org/pdf/2603.02246 — independently reproduces our own
observation (local garbled the noisy phone start; AssemblyAI held). → **route by profile.**

Polish WER anchors: AMU/Allegro **Polish ASR Leaderboard (PAL/BIGOS)** —
https://huggingface.co/spaces/amu-cai/pl-asr-leaderboard ,
https://huggingface.co/blog/michaljunczyk/introducing-polish-asr-leaderboard . Median WER
~14.5% read (BIGOS) vs ~32.4% conversational (PELCRA) — conversational/phone Polish is ~2×
harder for every system. Free-vs-commercial median gap only 2.5 pts read / 4.2 pts conversational.

## (a) STT for Polish

| Engine | Local/Cloud | Polish WER | Phone/noisy | Cost/hr | Apple-Silicon |
|---|---|---|---|---|---|
| Whisper large-v3 (mlx) | Local | ~5–6% clean; **40–54% noisy/overlap** | poor | free | excellent |
| Whisper large-v3-**turbo** (was our default) | Local | ~same clean; worse on hard audio (≈large-v2) | poor+ | free | excellent, 5× faster |
| **ElevenLabs Scribe** | Cloud | **2.3% FLEURS-pl**; ~10.6% on noisy medical PL | **best in PL noisy** | ~$0.40 | n/a |
| **AssemblyAI Universal-2** | Cloud | ≤10% PL tier; won our phone test | strong | ~$0.12–0.37 | n/a |
| AssemblyAI Universal-3 Pro | Cloud | new Feb-2026; `pl` unconfirmed — verify | strong | ~$0.12–0.37 | n/a |
| Deepgram Nova-3 | Cloud | weak PL (Nova-2 8.8% FLEURS-pl) | good EN only | ~$0.26–1.3 | n/a |
| Google Chirp2/Gemini 2.5 | Cloud | Gemini ~3.8% FLEURS-pl | good | pricey (~$2.16) | n/a |
| Speechmatics Ursa | Cloud | strong non-EN EU | strong | ~$2.64 | n/a |
| NVIDIA Canary/Parakeet (NeMo) | Local | ~just below Whisper-large on PAL | moderate | free | workable, less turnkey |

Sources: turbo caveat (official) https://github.com/openai/whisper/discussions/2363 ;
Scribe FLEURS-pl 2.3% https://elevenlabs.io/speech-to-text/polish ,
https://elevenlabs.io/blog/introducing-scribe-v2 ; AssemblyAI PL tier
https://www.assemblyai.com/docs/supported-languages ; cross-engine WER/cost
https://www.codesota.com/guides/speech-recognition .

**Picks:** solo/meeting-clean → local **large-v3 (non-turbo)**; phone-noisy → **cloud**,
lead PL candidate **ElevenLabs Scribe**, validated alt **AssemblyAI**. Deepgram is NOT a PL leader.

## (b) Preprocessing — don't denoise before ASR

- "When De-noising Hurts" (arXiv 2512.17562, Dec 2025): enhancement degraded ASR in **all 40
  configs**, mean **+7.83% WER**, max +46%. https://arxiv.org/html/2512.17562v1
- Deepgram "Noise Reduction Paradox": feed raw audio. https://deepgram.com/learn/the-noise-reduction-paradox-why-it-may-hurt-speech-to-text-accuracy
- DeepFilterNet maintainers confirm it hurts STT: https://github.com/Rikorose/DeepFilterNet/issues/483

**Do** add only **ffmpeg loudness-normalize (EBU R128 `loudnorm` ~−23 LUFS) + mono**; don't
upsample 8 kHz phone audio. Denoise *can* help VAD/diarization — if ever used, feed it ONLY
to the diarizer, never the ASR.

## (c) Diarization + speaker-ID

Diarization: **pyannote `precision-2` (cloud) is the 2026 DER leader** (~28% lower DER than
community-1; beats AssemblyAI/Deepgram/AWS) — https://www.pyannote.ai/benchmark ,
corroborated by SDBench (Interspeech 2025) https://arxiv.org/html/2507.16136v2 . Local
`community-1` is the same family, free, ~28% worse — fine for clean; gap widens on phone/overlap.
NVIDIA Streaming Sortformer (local, ≤4 spk) is a strong local upgrade option
(https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1). **Pragmatic: on phone calls
use the cloud engine's OWN diarization** (one API call) rather than running local diarization on
degraded audio.

**Measured (2026-06-15) — local community-1 MERGES 2 close-mic speakers on hard audio, and
the count hint is the wrong lever.** On a 180 s gym clip (2 speakers, movement, overlap),
`identify.py` auto-detect collapsed both into ONE cluster. Findings:
- The merge is a **clustering** failure, NOT an ASR one — it happens in AHC before any
  word assignment, so the mc-stt transcription work is orthogonal. Don't touch the ASR to
  fix diarization.
- `--num-speakers 2` DID separate them (2 clusters) — but is **fragile** (you rarely know
  the count for an arbitrary memo). `--min-speakers 1 --max-speakers 4` (bounded auto)
  **still merged to 1** — measured. So **bounds do NOT fix under-merging** (max bound still
  allows 1; auto-count within bounds still picks 1).
- **Root-cause, count-free fix = the clustering threshold** (`pipeline.instantiate({"clustering":
  {"threshold": X}})`): too high → merges, too low → splits. Lowering/calibrating it fixes the
  merge WITHOUT a known count. **Not yet exposed in `diarize.py` — TODO: add the knob + a
  one-time calibration on a few hand-labeled hard clips.** (`--threshold` today is the
  *cosine voiceprint-match* threshold, a different thing.)
- For genuinely hard audio, prefer a **count-estimating model** over threshold tuning: NME-SC
  eigengap (NeMo), or end-to-end **Sortformer**/EEND (predict variable count + handle overlap).
- **UPDATE 2026-06-16 — Sortformer SHIPPED as the Python DEFAULT, not just a Swift idea.**
  It turned out end-to-end Sortformer runs locally in Python on Metal via **`mlx-audio`**
  (model `mlx-community/diar_sortformer_4spk-v1-fp16`) — so `sortformer_diarize.py` is now the
  default diarizer (`VOICEMEMOS_DIAR_ENGINE`, `diarize.py`/pyannote = fallback). On the gym clip
  pyannote merged, Sortformer found 2 (validated 5/5 speaker-count across solo/phone/gym after a
  <2 s phantom filter), named the operator correctly, ~1000× faster. So the "good local diarization is
  Swift-only" framing below is OUTDATED for Python — FluidAudio remains the Swift-rewrite path,
  but Python is no longer stuck with pyannote.
- **Swift voice-mode rewrite → FluidAudio** (`FluidInference/FluidAudio`, ANE, Apache-2.0): on-
  device diarization (pyannote-community-1 Core ML, ~10.6% DER AMI) with `numClusters` auto-
  estimation AND `enrollSpeaker` voiceprints. The Hex dictation app uses it (for Parakeet ASR).
  Python bridge exists (**Senko**, `narcotic-sh/senko`) but its embeddings are English/Mandarin-
  tuned + noise-sensitive — not a fit for hard Polish.
- Real-world note: long multi-speaker memos already route through **cloud diarize**
  (gpt-4o-transcribe-diarize) per the chunking workflow, so they're unaffected — the local
  merge bites only when local diarization runs on hard 2-4-speaker audio.

Speaker-ID — the 0.99→0.75 cosine drop is the documented codec-mismatch failure. SVeritas
(EMNLP 2025) https://aclanthology.org/2025.findings-emnlp.516.pdf : codec+narrowband+noise sharply
raise EER; **ECAPA-TDNN, MFA-Conformer, RedimNet most robust; WavLM worst; wespeaker mid**.
Dominant error = enroll/test mismatch. **Hardening (cheapest-first):**
1. Multi-condition enrollment incl. a phone-codec sample (ffmpeg GSM/Opus-degrade a clean enroll).
2. Per-condition cosine thresholds (~0.7 phone / ~0.9 clean) — a single global threshold is the trap.
3. AS-Norm score normalization (https://arxiv.org/html/2504.04512v1).
4. Embed on highest-SNR segments only.
5. Or outsource to pyannote precision-2 managed voiceprints (€0.015 each).
Consider swapping wespeaker → **ECAPA-TDNN (SpeechBrain)** for codec robustness.

## (d) Eval harness design

**Clips:** 3 profiles × 2 = 6 clips, ~2–4 min each (~400–500 PL words), hand-corrected:
`solo-clean`, `meeting-clean-2spk`, `phone-noisy-2spk`. Span the real distribution.
**References:** hand-correct (start from best engine's output, then fix EVERY word — casing,
punctuation, numbers, diacritics, proper nouns). **Never score one engine against another
engine's raw output** (the #1 pitfall). Diarization refs in RTTM (pyannote.metrics-native).
**Metrics/tools:** WER/CER via **jiwer** (https://github.com/jitsi/jiwer) — report **raw AND
Polish-normalized** WER + CER; keep diacritics (dropping ż/ź/ą IS an error); normalize numbers.
DER/JER via **pyannote.metrics** (with & without 0.25 s collar; track overlap). Owner-ID: cluster
purity + per-condition cosine distribution. Report **bootstrap CIs** (don't over-read 1-pt gaps);
~400 words/condition ranks engines, not publishes. Borrow normalization from BIGOS/PAL tooling
(https://github.com/goodmike31/pl-asr-bigos-tools). Harness ≈ 150-line Python: glob clips → run
engines → jiwer + pyannote.metrics → markdown table + CIs.

## (e) Prioritized changes (cheapest-first)

1. **Local default turbo → large-v3 (non-turbo)** — free quality win. ✅ DONE (v0.4.0).
2. **Build the 6-clip eval harness and run it once** — decides everything else with data. Highest leverage.
3. **Confirm router** clean→local large-v3, phone→cloud; **add ElevenLabs Scribe** as phone candidate (have the key), let eval pick Scribe vs AssemblyAI.
4. **Do NOT denoise before ASR** — only loudness-norm + mono.
5. **Harden owner-ID for phone**: phone-degraded enrollment sample, per-condition thresholds, consider ECAPA-TDNN over wespeaker.
6. (Optional paid) phone diarization via cloud engine's own labels, or pyannote precision-2 (DER leader + managed owner-ID).
