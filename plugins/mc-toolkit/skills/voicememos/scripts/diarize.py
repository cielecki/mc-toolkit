#!/usr/bin/env python3
"""Local speaker diarization (pyannote community-1) + word-speaker assignment.

The diarizer half runs under the dedicated pyannote venv (torch, isolated from
the mlx-whisper env):  ~/.venvs/diarization/bin/python

It is an INDEPENDENT acoustic pass — it only needs the raw audio, NOT Whisper's
words. So the pipeline is: mlx-whisper → words[]  (fast, Apple-Silicon)  ||
pyannote → speaker turns[]  (this file)  → assign each word to the speaker whose
turn overlaps it most (the WhisperX trick). Output matches the existing
AssemblyAI-shaped contract: words[{text,start,end,speaker,confidence}] in MS.

Why community-1 + exclusive diarization:
  - community-1 is the best OPEN diarizer (close to cloud on clean 2–4 speaker
    audio; behind on messy/overlapping → escalate to precision-2/AssemblyAI).
  - `exclusive_speaker_diarization` is backported from the commercial precision-2
    specifically to simplify reconciling diarization timestamps with imprecise
    transcription timestamps — every instant belongs to exactly one speaker, so
    the word→speaker merge has no overlap ambiguity. We default to it.

Apple-Silicon note: run on CPU. torch+MPS with pyannote hits unimplemented
sparse ops (errors or silent-slow CPU fallback). A few-minute clip on M-series
CPU is fine for offline/batch use.

CLI:
  # 1) diarization only → turns JSON (seconds)
  python diarize.py audio.wav
  python diarize.py audio.wav --num-speakers 2
  python diarize.py audio.wav --min-speakers 2 --max-speakers 5

  # 2) merge: assign speakers to existing whisper words (words.json = [{text,start,end}], ms)
  python diarize.py audio.wav --words words.json            # → words+speaker JSON to stdout

  # absolute-accuracy escalation (cloud, one-line swap; needs a pyannoteAI key):
  python diarize.py audio.wav --model pyannote/speaker-diarization-precision-2 --token <pyannoteAI-key>

Diagnostics go to stderr; stdout stays clean JSON for the caller.
"""
import argparse
import json
import os
import sys


def log(*a):
    print(*a, file=sys.stderr)


def resolve_token(explicit=None):
    """HF_TOKEN: explicit flag → env → ~/.claude/.env."""
    if explicit:
        return explicit
    v = os.environ.get("HF_TOKEN")
    if v:
        return v.strip()
    env = os.path.expanduser("~/.claude/.env")
    try:
        with open(env) as f:
            for line in f:
                line = line.strip()
                if line.startswith("HF_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return None


def diarize(audio_path, *, token, model="pyannote/speaker-diarization-community-1",
            num_speakers=None, min_speakers=None, max_speakers=None, exclusive=True):
    """Run pyannote on `audio_path`. Returns turns [{start,end,speaker}] in SECONDS,
    sorted by start. `exclusive=True` uses the non-overlapping exclusive diarization
    (cleaner for reconciling with transcription timestamps)."""
    import torch  # noqa: F401  (heavy import — only when actually diarizing)
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(model, token=token)
    if pipeline is None:
        raise RuntimeError(
            f"Pipeline.from_pretrained returned None for {model!r} — usually means the "
            "gated model terms aren't accepted for this token's account, or the token is wrong.")
    pipeline.to(torch.device("cpu"))  # NOT mps — see module docstring

    kw = {}
    if num_speakers is not None:
        kw["num_speakers"] = num_speakers
    else:
        if min_speakers is not None:
            kw["min_speakers"] = min_speakers
        if max_speakers is not None:
            kw["max_speakers"] = max_speakers

    output = pipeline(audio_path, **kw)

    # pyannote 4.x community-1 returns an object with .speaker_diarization /
    # .exclusive_speaker_diarization (Annotation-like). Older builds returned the
    # Annotation directly. Handle all shapes.
    annotation = None
    if exclusive:
        annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(output, "speaker_diarization", output)

    turns = []
    # itertracks is stable across pyannote versions.
    if hasattr(annotation, "itertracks"):
        for seg, _track, label in annotation.itertracks(yield_label=True):
            turns.append({"start": float(seg.start), "end": float(seg.end), "speaker": str(label)})
    else:
        for seg, label in annotation:  # last-resort: iterable of (segment, label)
            turns.append({"start": float(seg.start), "end": float(seg.end), "speaker": str(label)})

    turns.sort(key=lambda t: t["start"])
    return turns


def assign_speakers(words, turns):
    """Assign each word the speaker of the turn it overlaps most (WhisperX method).

    words: [{start,end,...}] with start/end in MILLISECONDS (whisper_words.py shape).
    turns: [{start,end,speaker}] in SECONDS (diarize() output). Converted to ms here.
    Mutates + returns words with a `speaker` key (None if a word falls in no turn — e.g.
    a gap between speakers). Single-speaker audio → every word gets that one label.
    """
    turns_ms = [(t["start"] * 1000.0, t["end"] * 1000.0, t["speaker"]) for t in turns]
    for w in words:
        ws, we = w["start"], w["end"]
        best, best_ov = None, 0.0
        for ts, te, spk in turns_ms:
            ov = min(we, te) - max(ws, ts)
            if ov > best_ov:
                best_ov, best = ov, spk
        if best is None and turns_ms:
            # word fell in a gap between turns (no overlap) — attach it to the
            # NEAREST turn by time distance instead of leaving it unlabeled ("?"),
            # which otherwise litters noisy/cross-talk audio with orphan words.
            best = min(turns_ms, key=lambda t: 0 if t[0] <= ws <= t[1]
                       else min(abs(ws - t[1]), abs(t[0] - we)))[2]
        w["speaker"] = best
    return words


def n_speakers(turns):
    return len({t["speaker"] for t in turns})


def main():
    ap = argparse.ArgumentParser(description="Local pyannote diarization + word-speaker assignment.")
    ap.add_argument("audio")
    ap.add_argument("--words", help="JSON file of whisper words [{text,start,end}] in MS; "
                                    "if given, output is words+speaker instead of raw turns")
    ap.add_argument("--model", default="pyannote/speaker-diarization-community-1")
    ap.add_argument("--token", help="HF token (community-1) or pyannoteAI key (precision-2). "
                                    "Default: HF_TOKEN env / ~/.claude/.env")
    ap.add_argument("--num-speakers", type=int)
    ap.add_argument("--min-speakers", type=int)
    ap.add_argument("--max-speakers", type=int)
    ap.add_argument("--no-exclusive", action="store_true",
                    help="use the regular (overlap-allowing) diarization instead of exclusive")
    args = ap.parse_args()

    token = resolve_token(args.token)
    if not token:
        sys.exit("No token: set HF_TOKEN in env or ~/.claude/.env, or pass --token.")
    if not os.path.exists(args.audio):
        sys.exit(f"Audio not found: {args.audio}")

    log(f"diarize: model={args.model} exclusive={not args.no_exclusive} "
        f"num={args.num_speakers} min={args.min_speakers} max={args.max_speakers}")
    turns = diarize(args.audio, token=token, model=args.model,
                    num_speakers=args.num_speakers, min_speakers=args.min_speakers,
                    max_speakers=args.max_speakers, exclusive=not args.no_exclusive)
    log(f"diarize: {len(turns)} turns, {n_speakers(turns)} speaker(s)")

    if args.words:
        with open(args.words) as f:
            words = json.load(f)
        if isinstance(words, dict) and "words" in words:  # accept {"words":[...]} too
            payload = words
            payload["words"] = assign_speakers(payload["words"], turns)
        else:
            payload = {"words": assign_speakers(words, turns)}
        payload["speakers"] = sorted({t["speaker"] for t in turns})
        json.dump(payload, sys.stdout, ensure_ascii=False)
    else:
        json.dump({"turns": turns, "speakers": sorted({t["speaker"] for t in turns})},
                  sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
