#!/usr/bin/env python3
"""Cloud transcription + diarization via AssemblyAI — an alternative engine for
messy / phone-quality audio where the local whisper+pyannote pipeline struggles
(noisy, overlapping, far-field). universal-2 model, speaker_labels (diarization)
+ language_code. ~$0.37 per audio-hour. Key from ~/.claude/.env (ASSEMBLYAI_API_KEY).

This is the "messy → cloud" escalation tier. Runs under plain python3 (no venv).

Usage:
  python3 assemblyai.py <audio> [--language pl] [--label] [--out transcript_aai.md]
    --label : map AssemblyAI's Speaker A/B/... to enrolled voiceprints (a known speaker / the unknown label)
              via speaker_id (needs the pyannote venv importable + a voiceprint).
    --out   : write to file; default prints to stdout.
Reads sibling meta.json (if present) for the real title/date.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _config import cfg

AAI = "https://api.assemblyai.com/v2"


def log(*a):
    print(*a, file=sys.stderr)


def key():
    v = os.environ.get("ASSEMBLYAI_API_KEY")
    if v:
        return v.strip()
    for line in open(os.path.expanduser("~/.claude/.env")):
        if line.startswith("ASSEMBLYAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("ASSEMBLYAI_API_KEY not in env or ~/.claude/.env")


def _get(req):
    return json.loads(urllib.request.urlopen(req).read())


def upload(k, path):
    req = urllib.request.Request(f"{AAI}/upload", data=open(path, "rb").read(),
                                 headers={"authorization": k,
                                          "content-type": "application/octet-stream"})
    return _get(req)["upload_url"]


def create(k, url, lang):
    body = json.dumps({"audio_url": url, "speech_models": ["universal-2"],
                       "speaker_labels": True, "language_code": lang}).encode()
    req = urllib.request.Request(f"{AAI}/transcript", data=body,
                                 headers={"authorization": k, "content-type": "application/json"})
    try:
        return _get(req)["id"]
    except urllib.error.HTTPError as e:
        sys.exit(f"AssemblyAI create {e.code}: {e.read().decode('utf-8','replace')}")


def poll(k, tid):
    while True:
        d = _get(urllib.request.Request(f"{AAI}/transcript/{tid}", headers={"authorization": k}))
        if d["status"] == "completed":
            return d
        if d["status"] == "error":
            sys.exit(f"AssemblyAI error: {d.get('error')}")
        time.sleep(4)


def delete(k, tid):
    """Permanently delete a transcript from AssemblyAI (DELETE /v2/transcript/{id})."""
    req = urllib.request.Request(f"{AAI}/transcript/{tid}", method="DELETE",
                                 headers={"authorization": k})
    urllib.request.urlopen(req).read()


def clock(seconds):
    s = int(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--language", default=cfg("VOICEMEMOS_LANG", "en"))
    ap.add_argument("--label", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--keep-cloud", action="store_true",
                    help="do NOT auto-delete the transcript from AssemblyAI afterwards "
                         "(default deletes it for privacy — audio is auto-deleted regardless)")
    ap.add_argument("--loudnorm", action="store_true",
                    help="loudness-normalize (ffmpeg loudnorm) before upload — "
                         "AssemblyAI's VAD silently DROPS quiet/narrowband phone "
                         "segments (measured: skipped the first 20s of a call; "
                         "loudnorm cut WER 46.7→34.9). Use for phone recordings.")
    args = ap.parse_args()
    if not os.path.exists(args.audio):
        sys.exit(f"audio not found: {args.audio}")

    log("⚠️  uploads audio to AssemblyAI (cloud) — NOT for sensitive recordings. "
        "See references/privacy-research.md (default retention/training; delete after).")
    audio = args.audio
    if args.loudnorm:
        import subprocess
        import tempfile
        audio = tempfile.NamedTemporaryFile(suffix=".m4a", delete=False).name
        log("AssemblyAI: loudness-normalizing…")
        r = subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-i", args.audio,
                            "-af", "loudnorm=I=-16:TP=-1.5",
                            "-c:a", "aac", "-b:a", "128k", audio])
        if r.returncode != 0:
            sys.exit("ffmpeg loudnorm failed")
    k = key()
    log("AssemblyAI: uploading…")
    url = upload(k, audio)
    log("AssemblyAI: transcribing (universal-2, speaker_labels, "
        f"{args.language})…")
    tid = create(k, url, args.language)
    data = poll(k, tid)
    utts = data.get("utterances") or []
    log(f"AssemblyAI: {len(utts)} utterances, "
        f"{len({u['speaker'] for u in utts})} speakers")

    namemap = {}
    if args.label:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            import speaker_id as sid
            turns = [{"start": u["start"] / 1000.0, "end": u["end"] / 1000.0,
                      "speaker": u["speaker"]} for u in utts]
            namemap = sid.identify_turns(args.audio, turns)
        except Exception as e:
            log(f"AssemblyAI: --label skipped ({e})")

    # title/date from sibling meta.json if present
    meta = {}
    mp = os.path.join(os.path.dirname(os.path.abspath(args.audio)), "meta.json")
    if os.path.exists(mp):
        meta = json.load(open(mp))
    title = meta.get("title") or os.path.splitext(os.path.basename(args.audio))[0]
    spk = sorted({namemap.get(u["speaker"], f"Speaker {u['speaker']}") for u in utts})

    lines = [f"# {title} (AssemblyAI)", ""]
    if meta.get("date"):
        lines.append(f"- **Date**: {meta['date'][:10]}")
    lines += [f"- **Duration**: {clock(data.get('audio_duration') or 0)}",
              f"- **Speakers**: {' / '.join(spk)}",
              "- **Engine**: AssemblyAI universal-2 (cloud)", "", "---", ""]
    for u in utts:
        who = namemap.get(u["speaker"], f"Speaker {u['speaker']}")
        lines.append(f"**{who}** [{clock(u['start'] / 1000)}]")
        lines.append(u["text"])
        lines.append("")
    out = "\n".join(lines).rstrip() + "\n"
    if args.out:
        open(args.out, "w").write(out)
        log(f"wrote {args.out}")
    else:
        sys.stdout.write(out)

    # privacy default: delete the transcript from AssemblyAI now that we have it
    # locally (audio is already auto-deleted post-transcription). So nothing lingers
    # on their servers and there's no per-use email/cleanup. --keep-cloud to opt out.
    if not args.keep_cloud:
        try:
            delete(k, tid)
            log(f"AssemblyAI: deleted transcript {tid} from the cloud (privacy default)")
        except Exception as e:
            log(f"AssemblyAI: WARNING could not delete {tid} — delete it manually: {e}")


if __name__ == "__main__":
    main()
