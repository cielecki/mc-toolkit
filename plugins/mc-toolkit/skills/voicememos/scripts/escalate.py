#!/usr/bin/env python3
"""Escalate ONE memo's transcription to a cloud engine (privacy decision made upstream,
in SKILL.md). Local-first already ran in sync; this is the deliberate second step.

Usage: escalate.py <memo_dir> --engine openai|assemblyai|elevenlabs [--model M]

--model is openai-only. The openai engine defaults to gpt-4o-transcribe-diarize so the
escalated transcript keeps speaker turns. transcript.md is overwritten in place — the
local transcript isn't preserved (escalation only runs when it was suspect anyway, and
it's still reconstructible via data.json + render.py).
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_SCRIPT = {"openai": "openai.py", "assemblyai": "assemblyai.py", "elevenlabs": "elevenlabs.py"}
# Only openai.py accepts --model; assemblyai.py / elevenlabs.py define no such flag.
MODEL_ENGINES = {"openai"}
# Plain gpt-4o-transcribe returns text only (no speakers); the -diarize variant returns
# speaker turns, so the escalated transcript stays speaker-labeled (see references/routing.md).
OPENAI_DEFAULT_MODEL = "gpt-4o-transcribe-diarize"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("memo_dir")
    ap.add_argument("--engine", required=True, choices=list(ENGINE_SCRIPT))
    ap.add_argument("--model", help="only valid for --engine openai")
    ap.add_argument("--language", help="force a language code (e.g. en, pl); "
                    "assemblyai/elevenlabs only. Omit for auto-detection. Use when a memo "
                    "was mis-detected (e.g. Whisper hallucinated Russian on English audio).")
    args = ap.parse_args()

    if args.model and args.engine not in MODEL_ENGINES:
        ap.error(f"--model is only supported for the openai engine, not {args.engine}")

    audio = os.path.join(args.memo_dir, "audio.m4a")
    if not os.path.exists(audio):
        sys.exit(f"no audio.m4a in {args.memo_dir}")
    out_md = os.path.join(args.memo_dir, "transcript.md")

    model = args.model or (OPENAI_DEFAULT_MODEL if args.engine == "openai" else None)
    cmd = ["python3", os.path.join(HERE, ENGINE_SCRIPT[args.engine]), audio, "--out", out_md]
    if model:
        cmd += ["--model", model]
    if args.language and args.engine != "openai":
        cmd += ["--language", args.language]
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
