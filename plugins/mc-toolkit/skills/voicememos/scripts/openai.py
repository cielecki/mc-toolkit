#!/usr/bin/env python3
"""Cloud transcription via OpenAI (gpt-4o-transcribe / gpt-4o-mini-transcribe /
whisper-1) — the third cloud engine. Privacy posture is the best of the cloud tier:
OpenAI does NOT train on API data by default and retains it max 30 days (abuse
monitoring), then deletes — no opt-out email, no manual cleanup. ~$0.006/min
(gpt-4o-transcribe) / $0.003/min (mini). Key from ~/.claude/.env (OPENAI_API_KEY).

Limitation vs assemblyai/elevenlabs: NO diarization and no word timestamps on the
gpt-4o-transcribe models — text only. Speaker labels would have to come from the
local pyannote pipeline. Audio file limit 25 MB.

Usage:
  python3 openai.py <audio> [--language pl] [--model gpt-4o-transcribe] [--out file]
With --out *.md writes a metadata-block transcript (no speakers); otherwise prints
the plain transcript text to stdout (what eval.py consumes).
Reads sibling meta.json (if present) for the real title/date.
"""
import argparse
import json
import os
import subprocess
import sys

ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"


def log(*a):
    print(*a, file=sys.stderr)


def key():
    v = os.environ.get("OPENAI_API_KEY")
    if v:
        return v.strip()
    for line in open(os.path.expanduser("~/.claude/.env")):
        if line.startswith("OPENAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("OPENAI_API_KEY not in env or ~/.claude/.env")


def transcribe(audio, model, lang):
    """POST the audio (multipart via curl) → parsed response dict.
    gpt-4o-transcribe-diarize: response_format=diarized_json (segments with
    speaker/start/end) + required chunking_strategy. Others: plain json."""
    args = ["curl", "-s", "-X", "POST", ENDPOINT,
            "-H", f"Authorization: Bearer {key()}",
            "-F", f"file=@{audio}",
            "-F", f"model={model}",
            "-F", f"language={lang}"]
    if "diarize" in model:
        args += ["-F", "response_format=diarized_json",
                 "-F", "chunking_strategy=auto"]
    else:
        args += ["-F", "response_format=json"]
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"curl failed: {out.stderr[-400:]}")
    data = json.loads(out.stdout)
    if isinstance(data, dict) and data.get("error"):
        sys.exit(f"OpenAI error: {json.dumps(data)[:400]}")
    return data


def to_turns(segments):
    """Merge consecutive same-speaker diarized segments into turns."""
    turns = []
    for s in segments:
        spk = s.get("speaker") or "?"
        txt = (s.get("text") or "").strip()
        if not txt:
            continue
        if turns and turns[-1]["speaker"] == spk:
            turns[-1]["end"] = s.get("end", turns[-1]["end"])
            turns[-1]["text"] += " " + txt
        else:
            turns.append({"speaker": spk, "start": s.get("start", 0),
                          "end": s.get("end", 0), "text": txt})
    return turns


def clock(seconds):
    s = int(seconds or 0)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--language", default="pl")
    ap.add_argument("--model", default="gpt-4o-transcribe",
                    help="gpt-4o-transcribe | gpt-4o-mini-transcribe | whisper-1 | "
                         "gpt-4o-transcribe-diarize (segments with speakers)")
    ap.add_argument("--out")
    args = ap.parse_args()
    if not os.path.exists(args.audio):
        sys.exit(f"audio not found: {args.audio}")

    log("ℹ️  uploads audio to OpenAI (cloud) — no training on API data by default, "
        "~30-day retention then deletion. Still: prefer LOCAL for sensitive recordings.")
    log(f"OpenAI: transcribing ({args.model}, {args.language})…")
    data = transcribe(args.audio, args.model, args.language)
    turns = to_turns(data.get("segments") or []) if "diarize" in args.model else []
    text = " ".join(t["text"] for t in turns) if turns else (data.get("text") or "").strip()
    log(f"OpenAI: {len(text.split())} words"
        + (f", {len({t['speaker'] for t in turns})} speakers" if turns else ""))

    if args.out:
        meta = {}
        mp = os.path.join(os.path.dirname(os.path.abspath(args.audio)), "meta.json")
        if os.path.exists(mp):
            meta = json.load(open(mp))
        title = meta.get("title") or os.path.splitext(os.path.basename(args.audio))[0]
        lines = [f"# {title} (OpenAI {args.model})", ""]
        if meta.get("date"):
            lines.append(f"- **Date**: {meta['date'][:10]}")
        lines += [f"- **Engine**: OpenAI {args.model} (cloud)", "", "---", ""]
        if turns:
            for t in turns:
                lines += [f"**Speaker {t['speaker']}** [{clock(t['start'])}]",
                          t["text"], ""]
        else:
            lines += [text, ""]
        open(args.out, "w").write("\n".join(lines).rstrip() + "\n")
        log(f"wrote {args.out}")
    else:
        sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
