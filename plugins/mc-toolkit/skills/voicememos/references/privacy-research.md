# Cloud STT privacy & retention — research (2026-06-09)

What happens to audio + transcripts sent to the cloud engines, and how to minimize
exposure. **Read before sending ANY sensitive recording to a cloud engine.** Your
memos may be private legal/corporate calls — for those the rule is **local-only**.

## The rule

**Sensitive recording → local pipeline ONLY (whisper + pyannote + wespeaker).** Confirmed
fully on-device: model weights are downloaded once (the HF token only authorizes a gated
*download*); inference (`pipeline("audio.wav")`, whisper, wespeaker embeddings) runs locally
with no audio uploaded anywhere. Zero retention, zero training, zero sub-processor, no
breach surface. Cloud engines (`assemblyai.py`, `elevenlabs.py`) upload the audio — never
point them at sensitive material.

## Comparison — AssemblyAI vs ElevenLabs Scribe

| Dimension | AssemblyAI | ElevenLabs Scribe |
|---|---|---|
| Default audio retention | Audio **deleted right after transcription**; untranscribed uploads ≤24–48h | **Retained by default**, no published TTL |
| Default transcript retention | **Indefinite** unless TTL set / BAA / deleted | Retained until deleted; backups ≤30 days |
| Trains on your data by default? | **Yes**, opt-out by email (data-opt-out@assemblyai.com); **paid plans only**, forward-looking | **Yes**, opt-out = self-serve toggle (Profile → Terms and privacy → Data use → "Improve the models for everyone" OFF); forward-looking |
| Zero-retention | **Yes on pay-as-you-go** — set TTL as low as 1h | **Enterprise-only** (`enable_logging=false`) |
| Delete API | **Yes** — `DELETE /v2/transcript/{id}` | STT **not** in `/v1/history` (TTS only); not API-deletable on standard account → GDPR request |
| Data residency | US default; **EU servers** available (also exempts from training) | US default; EU/India **enterprise-only** (`api.eu.residency.elevenlabs.io`) |
| GDPR DPA / SOC2 / HIPAA | DPA (processor); SOC2 Type 1&2; HIPAA via BAA; EU-US DPF | DPA (processor, enterprise); SOC2; HIPAA via BAA |

Sources: AssemblyAI [data-retention-and-model-training](https://www.assemblyai.com/docs/data-retention-and-model-training),
[zero-retention FAQ](https://support.assemblyai.com/articles/2240096256-does-assemblyai-offer-zero-data-retention),
[delete docs](https://www.assemblyai.com/docs/pre-recorded-audio/delete-transcripts),
[privacy policy](https://www.assemblyai.com/legal/privacy-policy) (eff. 2026-01-06).
ElevenLabs [ZRM doc](https://elevenlabs.io/docs/eleven-api/resources/zero-retention-mode),
[model-training help](https://help.elevenlabs.io/hc/en-us/articles/29952728805393-Is-my-data-used-to-improve-ElevenLabs-AI-models),
[data residency](https://elevenlabs.io/docs/overview/administration/data-residency),
[privacy policy](https://elevenlabs.io/privacy-policy) (upd. 2026-05-20).

## Verdict (privacy posture)

**For a non-enterprise account, AssemblyAI > ElevenLabs — and it's not close:** AssemblyAI
auto-deletes audio post-transcription and reaches near-zero-retention on pay-as-you-go
(opt-out email + 1h TTL + explicit `DELETE`). ElevenLabs locks true zero-retention + EU
residency behind enterprise and otherwise stores by default with no TTL; its STT data isn't
even deletable via the standard API (not in `/v1/history`). ElevenLabs' only win is the
self-serve training toggle. So if cloud is unavoidable for a NON-sensitive clip, prefer
AssemblyAI with opt-out + TTL + delete. **For sensitive: neither — local only.**

⚠️ Both opt-outs are **forward-looking** — opt out BEFORE the first upload, not after.

## Minimize-exposure steps (if cloud is used for non-sensitive audio)

- **AssemblyAI:** (1) email data-opt-out@assemblyai.com (paid plan); (2) set 1h TTL;
  (3) `DELETE /v2/transcript/{id}` right after fetching; (4) optionally EU servers.
- **ElevenLabs:** (1) toggle OFF "Improve the models for everyone"; (2) delete via API where
  possible (STT not in history → GDPR deletion request to ElevenLabs); (3) ZRM/EU = enterprise.

## Incident note

It is easy to upload sensitive calls to BOTH AssemblyAI and ElevenLabs during an
engine-quality comparison before privacy is considered. **AssemblyAI:** transcripts are
deletable via API (audio is already auto-deleted). **ElevenLabs:** STT data is NOT
API-deletable on the standard account → requires a GDPR deletion request + the training
toggle. Lesson encoded above: gate cloud behind explicit non-sensitive confirmation;
default sensitive → local-only.
