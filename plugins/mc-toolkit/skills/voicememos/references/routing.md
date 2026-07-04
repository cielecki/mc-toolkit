# Voice Memos — routing phase (process)

How the post-transcription phase works: quality gate, engine escalation, auto-title,
and routing. **Generic and path-free** — no private domain paths here. The private
table of routing rules (with real domain paths) lives outside this repo, in the
skill's local data dir (gitignored), not in this file.

## Pipeline order

```
transcribe (local) → quality gate (+ escalation if needed) → auto-title → routing → write routing_note
```

1. **Transcribe (local).** `sync.py` always runs the local whisper + diarization
   pipeline first — free, private, on-device. This step also persists the quality
   signals the next stage needs: `speech_seconds` / `vad_ratio` (from silero-VAD) and
   per-word `confidence`.
2. **Quality gate.** Classify the local transcript's health from objective signals
   (not content) into `healthy` / `empty` / `suspect`. See below.
3. **Engine escalation.** Only for `suspect` transcripts — walk the privacy-ranked
   escalation ladder, gated by content sensitivity. See below.
4. **Auto-title.** One LLM pass over the full transcript produces a short descriptive
   title; the folder is renamed to `YYYY-MM-DD-<slug>`. The original Voice-Memos-app
   title is preserved in `meta.json` as `original_title`.
5. **Routing.** The LLM reads the full transcript and matches it against the rule
   table (`Kryterium:` / `Akcja:` pairs). A `NIE pytaj` rule executes inline; a
   `ZAPYTAJ` rule proposes the action and waits for approve/adjust/execute.
6. **Write `routing_note`.** The outcome ("what was done and where") is written back
   to that memo's `meta.json`, plus `status`.

All of this runs in one session per memo, not as a separate pass — sync produces
`status: needs-routing`, and the routing phase (steps 2-6) closes it out to `routed`,
`archived`, or `needs-attention`.

## Quality gate

Decided from **objective signals**, never from reading the transcript's content:

- **healthy** — there is text → proceed to normal routing.
- **empty** — VAD ≈ silence and no text → candidate for archive (genuinely empty
  recording).
- **suspect** — VAD detected speech but the text is empty / looping (a known Whisper
  failure mode) / very low confidence → escalate to the next engine.

The empty/suspect distinction matters because an empty transcript alone is ambiguous
(true silence vs. STT failure) — **the VAD signal, not the text, makes the call.**

## Escalation ladder

Only `suspect` transcripts escalate. The local pass already ran (free), so escalation
is a deliberate second step, decided from the local transcript — which supplies both
a quality signal and the content needed to judge sensitivity (below).

The ladder is ranked by privacy posture, cheapest/most-private first:

| rung | engine | privacy profile |
|---|---|---|
| 0 | **local whisper** (default, always first) | 100% on-device |
| 1 | **OpenAI** gpt-4o-transcribe | does not train on data; auto-delete ≤30 days; best cloud privacy of the three; text only (speaker labels stay from local pyannote) |
| 2 | **AssemblyAI** | auto-deletes audio right after transcription; trains on data by default (one-time email opt-out) |
| 3 | **ElevenLabs** | does not auto-delete; trains on data by default; default retention with no published TTL; best quality on phone-call-like audio |

**Źródła profili prywatności:** AssemblyAI i ElevenLabs — `references/privacy-research.md` (zweryfikowane). OpenAI — design spec `docs/superpowers/specs/2026-07-04-voicememos-routing-design.md` (privacy-research.md nie pokrywa jeszcze OpenAI; do rozszerzenia).

Per-engine detail (verbatim from `references/privacy-research.md` — that file is the
source of truth for these facts; do not let this table drift from it):

- **AssemblyAI** — audio deleted right after transcription (untranscribed uploads
  ≤24–48h); transcript retained indefinitely unless a TTL is set / BAA / manually
  deleted; trains on your data by default (opt-out by emailing
  data-opt-out@assemblyai.com, paid plans only, forward-looking only); zero-retention
  available on pay-as-you-go (TTL as low as 1h); has a delete API
  (`DELETE /v2/transcript/{id}`); US default with EU servers available.
- **ElevenLabs Scribe** — audio retained by default with no published TTL; transcript
  retained until deleted (backups ≤30 days); trains on your data by default (opt-out
  via a self-serve toggle: Profile → Terms and privacy → Data use → "Improve the
  models for everyone" OFF, forward-looking only); zero-retention is enterprise-only
  (`enable_logging=false`); STT data is **not** deletable via the standard API (not in
  `/v1/history`) — deletion requires a GDPR request; US default, EU/India residency is
  enterprise-only.

Both opt-outs are **forward-looking only** — they must be set before the first
upload, not after.

## Sensitivity gate

Content sensitivity caps how far up the ladder a transcript is allowed to go:

- **Sensitive content — health / therapy / intimacy / finance / family** — never
  escalates past rung 1 (OpenAI). ElevenLabs (rung 3) is reserved for non-sensitive,
  quality-critical cases only. Rung 2 (AssemblyAI) is likewise off-limits for
  sensitive content.
- **v1 always asks** before any off-device send, regardless of rung. The question
  names the situation concretely: transcript quality is poor, the topic looks like
  X, proposed engine is Y with its privacy profile (e.g. "does not train, auto-delete")
  — the human approves or overrides before anything leaves the Mac.

This is a deliberately conservative starting point (trust is earned via the rule file
below, not assumed). The intent is to eventually let well-understood, low-risk cases
skip the question — but that's an explicit future step, not the default.

## Rule-file format

The routing table itself — the private, path-bearing list of `Kryterium:` /
`Akcja:` rules — lives in the skill's local data directory (gitignored), **not** in
this file. This section documents the *format* only.

Each rule is a criterion evaluated against the memo's full transcript, paired with an
action:

```markdown
- Kryterium: <description, judged against the WHOLE transcript>
  Akcja: <what to do, where; ZAPYTAJ | NIE pytaj>
```

- `Kryterium:` is prose, matched by the LLM against the full transcript — not
  keywords, not a folder name.
- `Akcja:` says what to do and where, and ends with either:
  - **`ZAPYTAJ`** — propose the action, wait for approve / adjust / execute.
  - **`NIE pytaj`** — execute inline, no confirmation.
- Rules are meant to start conservative (mostly `ZAPYTAJ`) and get promoted to
  `NIE pytaj` over time, by hand-editing the rule file — that promotion is the
  trust trajectory this system is built around.

The engine used for a given memo, and the outcome of applying these rules, get
recorded back onto that memo (`engine`, `status`, `routing_note` in its `meta.json`) —
never in a shared/central file.
