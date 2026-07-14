#!/usr/bin/env python3
"""Cloud transcription via OpenAI (gpt-4o-transcribe / gpt-4o-mini-transcribe /
whisper-1) — the third cloud engine. Privacy posture is the best of the cloud tier:
OpenAI does NOT train on API data by default and retains it max 30 days (abuse
monitoring), then deletes — no opt-out email, no manual cleanup. ~$0.006/min
(gpt-4o-transcribe) / $0.003/min (mini). Key from ~/.claude/.env (OPENAI_API_KEY).

Note: the plain gpt-4o-transcribe / -mini / whisper-1 models are text-only (NO
diarization, no word timestamps). For speaker turns use the gpt-4o-transcribe-diarize
variant (diarized_json → to_turns); escalate.py defaults the openai engine to it.

Audio prep is automatic (verified 2026-07-14). Apple Voice Memos records a MULTI-STREAM
m4a (AAC voice + a 4-channel spatial track + data streams); the raw file makes OpenAI
reject it as "corrupted or unsupported". So any input is first re-encoded to a clean
mono 16 kHz mp3 (one audio stream, spatial/data tracks dropped). Long recordings exceed
the per-model duration limit (and the 25 MB upload cap); OpenAI's server-side
chunking_strategy=auto does NOT save them, so we segment CLIENT-side, transcribe each
chunk with a retry (the endpoint intermittently returns an empty body), and stitch the
bodies back together. Requires ffmpeg/ffprobe on PATH.

Usage:
  python3 openai.py <audio> [--language pl] [--model gpt-4o-transcribe] [--out file]
                    [--no-prep] [--chunk-seconds N] [--keep-temp]
With --out *.md writes a metadata-block transcript (speakers for the -diarize model);
otherwise prints the plain transcript text to stdout (what eval.py consumes).
Reads sibling meta.json (if present) for the real title/date.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _config import cfg

ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"

# Per-model duration ceilings (see SKILL.md "OpenAI engine — duration limits"):
# plain gpt-4o-transcribe/-mini hard-cap at 1400 s/file → stay under with 1200 s;
# the -diarize variant times out (empty body) above ~600 s → chunk to 600 s.
PLAIN_MAX_S = 1200
DIARIZE_MAX_S = 600
UPLOAD_CAP_BYTES = 24 * 1024 * 1024  # 25 MB API cap, kept under with margin


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


def _ff(args, what):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error"] + args,
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg {what} failed:\n{r.stderr[-600:]}")


def probe_duration(path):
    """Duration in seconds via ffprobe (0.0 if unreadable)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def prep_clean(audio, tmpdir):
    """Re-encode ANY input to a single-stream mono 16 kHz mp3.

    -map 0:a:0 keeps only the first audio stream (the AAC voice track), dropping the
    spatial + data streams that make OpenAI reject a raw Voice Memos m4a. Mono 16 kHz is
    speech-fine and tiny (a 56-min recording → ~13 MB), so size rarely binds — duration
    does. Returns the clean mp3 path."""
    out = os.path.join(tmpdir, "clean.mp3")
    log("OpenAI: prepping audio → mono 16 kHz mp3 (drops spatial/data streams)…")
    _ff(["-i", audio, "-map", "0:a:0", "-ac", "1", "-ar", "16000",
         "-c:a", "libmp3lame", "-q:a", "5", out], "prep")
    return out


def chunk_seconds(clean, model_max):
    """Choose a per-chunk length that respects BOTH the model duration ceiling and the
    25 MB upload cap (derived from the clean file's actual bitrate)."""
    size = os.path.getsize(clean)
    dur = probe_duration(clean)
    if dur <= 0:
        return model_max
    bytes_per_s = size / dur
    size_max = int(UPLOAD_CAP_BYTES / bytes_per_s) if bytes_per_s else model_max
    return max(30, min(model_max, size_max))


def segment(clean, seconds, tmpdir):
    """Split clean.mp3 into ≤`seconds` chunks (lossless -c copy). Returns sorted paths."""
    pat = os.path.join(tmpdir, "chunk_%03d.mp3")
    _ff(["-i", clean, "-f", "segment", "-segment_time", str(seconds),
         "-c", "copy", pat], "segment")
    return sorted(glob.glob(os.path.join(tmpdir, "chunk_*.mp3")))


def transcribe(audio, model, lang, attempts=3, max_time=240):
    """POST the audio (multipart via curl) → parsed response dict.
    gpt-4o-transcribe-diarize: response_format=diarized_json (segments with
    speaker/start/end) + required chunking_strategy. Others: plain json.

    Retries on an EMPTY body / non-zero curl exit — the endpoint intermittently returns
    nothing (server-side timeout) which looks like a corrupt-file error but succeeds on a
    plain re-POST of the same upload (SKILL.md). A real API error dict is NOT retried."""
    args = ["curl", "-s", "--max-time", str(max_time), "-X", "POST", ENDPOINT,
            "-H", f"Authorization: Bearer {key()}",
            "-F", f"file=@{audio}",
            "-F", f"model={model}",
            "-F", f"language={lang}"]
    if "diarize" in model:
        args += ["-F", "response_format=diarized_json",
                 "-F", "chunking_strategy=auto"]
    else:
        args += ["-F", "response_format=json"]
    last = ""
    for attempt in range(1, attempts + 1):
        out = subprocess.run(args, capture_output=True, text=True)
        if out.returncode != 0 or not out.stdout.strip():
            last = (out.stderr or "empty response").strip()[-300:]
            log(f"OpenAI: attempt {attempt}/{attempts} empty/failed ({last}); retrying…")
            continue
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            last = out.stdout.strip()[:300]
            log(f"OpenAI: attempt {attempt}/{attempts} non-JSON ({last}); retrying…")
            continue
        if isinstance(data, dict) and data.get("error"):
            sys.exit(f"OpenAI error: {json.dumps(data)[:400]}")
        return data
    sys.exit(f"OpenAI: no valid response after {attempts} attempts ({last})")


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
    ap.add_argument("--language", default=cfg("VOICEMEMOS_LANG", "auto"))
    ap.add_argument("--model", default="gpt-4o-transcribe",
                    help="gpt-4o-transcribe | gpt-4o-mini-transcribe | whisper-1 | "
                         "gpt-4o-transcribe-diarize (segments with speakers)")
    ap.add_argument("--out")
    ap.add_argument("--no-prep", action="store_true",
                    help="skip the ffmpeg re-encode (input is already a clean mp3/wav)")
    ap.add_argument("--chunk-seconds", type=int, default=0,
                    help="force a per-chunk length (0 = auto from model/size limits)")
    ap.add_argument("--keep-temp", action="store_true",
                    help="keep the temp clean.mp3/chunks (debugging)")
    args = ap.parse_args()
    if not os.path.exists(args.audio):
        sys.exit(f"audio not found: {args.audio}")

    log("ℹ️  uploads audio to OpenAI (cloud) — no training on API data by default, "
        "~30-day retention then deletion. Still: prefer LOCAL for sensitive recordings.")

    diarize = "diarize" in args.model
    tmpdir = tempfile.mkdtemp(prefix="vm-openai-")
    try:
        clean = args.audio if args.no_prep else prep_clean(args.audio, tmpdir)
        model_max = DIARIZE_MAX_S if diarize else PLAIN_MAX_S
        limit = args.chunk_seconds or chunk_seconds(clean, model_max)
        dur = probe_duration(clean)
        if dur > limit + 5:
            chunks = segment(clean, limit, tmpdir)
            log(f"OpenAI: {clock(dur)} > {clock(limit)}/chunk → {len(chunks)} chunks")
        else:
            chunks = [clean]

        # results: list of (offset_seconds, payload). payload = turns list (diarize) or text.
        results, offset = [], 0.0
        for i, ch in enumerate(chunks):
            if len(chunks) > 1:
                log(f"OpenAI: transcribing chunk {i + 1}/{len(chunks)} "
                    f"({args.model}, {args.language})…")
            else:
                log(f"OpenAI: transcribing ({args.model}, {args.language})…")
            data = transcribe(ch, args.model, args.language)
            if diarize:
                turns = to_turns(data.get("segments") or [])
                for t in turns:
                    t["start"] += offset
                    t["end"] = (t.get("end") or 0) + offset
                results.append((offset, turns))
            else:
                results.append((offset, (data.get("text") or "").strip()))
            offset += probe_duration(ch)
    finally:
        if not args.keep_temp:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # Plain flowing text (stdout + word count) — join all chunk bodies.
    if diarize:
        full_text = " ".join(t["text"] for _, turns in results for t in turns)
        n_spk = len({t["speaker"] for _, turns in results for t in turns})
    else:
        full_text = " ".join(txt for _, txt in results if txt)
        n_spk = 0
    log(f"OpenAI: {len(full_text.split())} words"
        + (f", {n_spk} speakers/chunk-labels" if diarize else "")
        + (f", stitched {len(results)} chunks" if len(results) > 1 else ""))

    if args.out:
        meta = {}
        mp = os.path.join(os.path.dirname(os.path.abspath(args.audio)), "meta.json")
        if os.path.exists(mp):
            meta = json.load(open(mp))
        title = meta.get("title") or os.path.splitext(os.path.basename(args.audio))[0]
        lines = [f"# {title} (OpenAI {args.model})", ""]
        if meta.get("date"):
            lines.append(f"- **Date**: {meta['date'][:10]}")
        lines += [f"- **Engine**: OpenAI {args.model} (cloud)"]
        if len(results) > 1:
            lines.append(f"- **Note**: stitched from {len(results)} client-side chunks"
                         + (" — speaker labels reset per chunk (attribute by content)"
                            if diarize else ""))
        lines += ["", "---", ""]
        multi = len(results) > 1
        if diarize:
            for off, turns in results:
                if multi:
                    lines += [f"## [~{clock(off)}]", ""]
                for t in turns:
                    lines += [f"**Speaker {t['speaker']}** [{clock(t['start'])}]",
                              t["text"], ""]
        else:
            for off, txt in results:
                if multi:
                    lines += [f"## [~{clock(off)}]", ""]
                lines += [txt, ""]
        open(args.out, "w").write("\n".join(lines).rstrip() + "\n")
        log(f"wrote {args.out}")
    else:
        sys.stdout.write(full_text + "\n")


if __name__ == "__main__":
    main()
