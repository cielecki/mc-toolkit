#!/usr/bin/env python3
"""Diarize + identify: turn audio into named-speaker turns (and optionally
named-speaker words). Run under the pyannote venv:

    ~/.venvs/diarization/bin/python identify.py <audio> [options]

Pipeline: diarize.py (who-spoke-when, anonymous) → speaker_id.py (match each
cluster to enrolled voiceprints) → relabel SPEAKER_00/01 with real names
(a known speaker), unmatched clusters → the unknown label.

Options:
  --num-speakers N / --min-speakers / --max-speakers   passed to diarization
  --threshold T          cosine match threshold (default 0.5)
  --words words.json     also relabel whisper words [{text,start,end}] (ms) and
                         emit the full {words:[...]} payload with named speakers
  --no-exclusive         use overlap-allowing diarization

Output (stdout JSON):
  with --words:  {"words":[{...,"speaker":"<name>"}], "speakers":[...], "mapping":{...}}
  otherwise:     {"turns":[{...,"speaker":"<name>"}], "speakers":[...], "mapping":{...}}

If no voiceprints are enrolled yet, speakers stay anonymous (SPEAKER_00/01) and
mapping is identity — i.e. it degrades gracefully to plain diarization.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _config import cfg

import diarize as dz
import speaker_id as sid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--words")
    ap.add_argument("--threshold", type=float, default=sid.DEFAULT_THRESHOLD)
    ap.add_argument("--num-speakers", type=int)
    ap.add_argument("--min-speakers", type=int)
    ap.add_argument("--max-speakers", type=int)
    ap.add_argument("--no-exclusive", action="store_true")
    ap.add_argument("--unknown-label", default=cfg("VOICEMEMOS_UNKNOWN_LABEL", "unknown"))
    args = ap.parse_args()

    token = sid._token()
    if not token:
        sys.exit("No HF_TOKEN (env or ~/.claude/.env).")
    if not os.path.exists(args.audio):
        sys.exit(f"Audio not found: {args.audio}")

    turns = dz.diarize(args.audio, token=token,
                       num_speakers=args.num_speakers, min_speakers=args.min_speakers,
                       max_speakers=args.max_speakers, exclusive=not args.no_exclusive)
    sid.log(f"identify: {len(turns)} turns, {dz.n_speakers(turns)} anon cluster(s)")

    mapping = sid.identify_turns(args.audio, turns, threshold=args.threshold,
                                 unknown_label=args.unknown_label, token=token)
    for t in turns:
        t["speaker"] = mapping.get(t["speaker"], t["speaker"])
    named = sorted({t["speaker"] for t in turns})

    if args.words:
        with open(args.words) as f:
            raw = json.load(f)
        words = raw["words"] if isinstance(raw, dict) and "words" in raw else raw
        dz.assign_speakers(words, turns)  # turns already carry resolved names
        out = {"words": words, "speakers": named, "mapping": mapping}
    else:
        out = {"turns": turns, "speakers": named, "mapping": mapping}
    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
