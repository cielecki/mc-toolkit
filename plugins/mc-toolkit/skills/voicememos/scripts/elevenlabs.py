#!/usr/bin/env python3
"""Cloud transcription + diarization via ElevenLabs Scribe — the second cloud engine
(alongside assemblyai.py) for messy/phone Polish audio. Per a 2026 AGH Kraków study,
Scribe held ~10.6% WER on noisy/overlapping Polish where Whisper-large hit 42-54% and
claims 2.3% on FLEURS-pl (best published Polish number) — so it's the lead candidate for
the phone-call tier. ~$0.40/audio-hr. Key from ~/.claude/.env (ELEVENLABS_API_KEY).

Uses the /v1/speech-to-text endpoint (model scribe_v1, diarize=true, word timestamps).
Uploads via curl (multipart). Runs under plain python3; use the venv for --label.

Usage:
  python3 elevenlabs.py <audio> [--language pol] [--label] [--out transcript_el.md]
    --label : map Scribe's speaker_0/1/... to enrolled voiceprints (a known speaker / the unknown label) via
              speaker_id (needs the pyannote venv importable + a voiceprint).
Reads sibling meta.json (if present) for the real title/date.
"""
import argparse
import json
import os
import subprocess
import sys

ENDPOINT = "https://api.elevenlabs.io/v1/speech-to-text"


def log(*a):
    print(*a, file=sys.stderr)


def key():
    v = os.environ.get("ELEVENLABS_API_KEY")
    if v:
        return v.strip()
    for line in open(os.path.expanduser("~/.claude/.env")):
        if line.startswith("ELEVENLABS_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("ELEVENLABS_API_KEY not in env or ~/.claude/.env")


def transcribe(audio, lang):
    """POST the audio to Scribe (multipart via curl) → parsed JSON response."""
    out = subprocess.run(
        ["curl", "-s", "-X", "POST", ENDPOINT,
         "-H", f"xi-api-key: {key()}",
         "-F", f"file=@{audio}",
         "-F", "model_id=scribe_v1",
         "-F", "diarize=true",
         "-F", f"language_code={lang}",
         "-F", "timestamps_granularity=word"],
        capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"curl failed: {out.stderr[-400:]}")
    data = json.loads(out.stdout)
    if isinstance(data, dict) and (data.get("detail") or data.get("error")):
        sys.exit(f"ElevenLabs error: {json.dumps(data)[:400]}")
    return data


def to_turns(words):
    """Group consecutive same-speaker word tokens into turns [{start,end,speaker,text}]
    (seconds). Skips 'spacing'/'audio_event' tokens for the text but keeps timing."""
    turns = []
    for w in words:
        if w.get("type") != "word":
            continue
        spk = w.get("speaker_id") or "speaker_0"
        if turns and turns[-1]["speaker"] == spk:
            turns[-1]["end"] = w["end"]
            turns[-1]["text"].append(w["text"])
        else:
            turns.append({"start": w["start"], "end": w["end"],
                          "speaker": spk, "text": [w["text"]]})
    for t in turns:
        t["text"] = " ".join(t["text"]).replace("  ", " ").strip()
    return turns


def clock(seconds):
    s = int(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--language", default="pol")
    ap.add_argument("--label", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    if not os.path.exists(args.audio):
        sys.exit(f"audio not found: {args.audio}")

    log("⚠️  uploads audio to ElevenLabs (cloud) — NOT for sensitive recordings, and "
        "STT data is NOT API-deletable on a standard account. See references/privacy-research.md.")
    log("ElevenLabs Scribe: transcribing (scribe_v1, diarize, "
        f"{args.language})…")
    data = transcribe(args.audio, args.language)
    turns = to_turns(data.get("words") or [])
    log(f"ElevenLabs: {len(turns)} turns, "
        f"{len({t['speaker'] for t in turns})} speakers")

    namemap = {}
    if args.label and turns:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            import speaker_id as sid
            namemap = sid.identify_turns(
                args.audio, [{"start": t["start"], "end": t["end"],
                              "speaker": t["speaker"]} for t in turns])
        except Exception as e:
            log(f"ElevenLabs: --label skipped ({e})")

    meta = {}
    mp = os.path.join(os.path.dirname(os.path.abspath(args.audio)), "meta.json")
    if os.path.exists(mp):
        meta = json.load(open(mp))
    title = meta.get("title") or os.path.splitext(os.path.basename(args.audio))[0]
    dur = turns[-1]["end"] if turns else 0
    spk = sorted({namemap.get(t["speaker"], t["speaker"]) for t in turns})

    lines = [f"# {title} (ElevenLabs Scribe)", ""]
    if meta.get("date"):
        lines.append(f"- **Date**: {meta['date'][:10]}")
    lines += [f"- **Duration**: {clock(dur)}",
              f"- **Speakers**: {' / '.join(spk)}",
              "- **Engine**: ElevenLabs Scribe v1 (cloud)", "", "---", ""]
    for t in turns:
        who = namemap.get(t["speaker"], t["speaker"])
        lines.append(f"**{who}** [{clock(t['start'])}]")
        lines.append(t["text"])
        lines.append("")
    out = "\n".join(lines).rstrip() + "\n"
    if args.out:
        open(args.out, "w").write(out)
        log(f"wrote {args.out}")
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
