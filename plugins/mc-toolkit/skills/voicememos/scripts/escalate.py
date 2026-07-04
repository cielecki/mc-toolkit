#!/usr/bin/env python3
"""Escalate ONE memo's transcription to a cloud engine (privacy decision made upstream,
in SKILL.md). Local-first already ran in sync; this is the deliberate second step.

Usage: escalate.py <memo_dir> --engine openai|assemblyai|elevenlabs [--model M]
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_SCRIPT = {"openai": "openai.py", "assemblyai": "assemblyai.py", "elevenlabs": "elevenlabs.py"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("memo_dir")
    ap.add_argument("--engine", required=True, choices=list(ENGINE_SCRIPT))
    ap.add_argument("--model")
    args = ap.parse_args()

    audio = os.path.join(args.memo_dir, "audio.m4a")
    if not os.path.exists(audio):
        sys.exit(f"no audio.m4a in {args.memo_dir}")
    out_md = os.path.join(args.memo_dir, "transcript.md")
    cmd = ["python3", os.path.join(HERE, ENGINE_SCRIPT[args.engine]), audio, "--out", out_md]
    if args.model:
        cmd += ["--model", args.model]
    if subprocess.run(cmd).returncode != 0:
        sys.exit(f"{args.engine} transcription failed")

    p = os.path.join(args.memo_dir, "meta.json")
    meta = json.load(open(p)) if os.path.exists(p) else {}
    meta["engine"] = args.engine
    meta["transcript_health"] = "healthy"
    meta["status"] = "needs-routing"
    json.dump(meta, open(p, "w"), ensure_ascii=False, indent=2)
    print(f"escalated {args.memo_dir} via {args.engine} → transcript.md rewritten")


if __name__ == "__main__":
    main()
