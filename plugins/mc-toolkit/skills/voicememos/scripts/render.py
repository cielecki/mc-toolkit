#!/usr/bin/env python3
"""Re-render transcript.md from a folder's data.json — INSTANT, no re-transcribe/
re-diarize. Use after changing the format in sync.write_transcript_md so you don't
pay the slow whisper+pyannote pipeline again just to restyle the output.

Only works for memos processed by sync.py v0.3.4+ (which saves data.json). Older
folders have no data.json → must be re-run through sync.py once.

Usage:
  python3 render.py <folder>     # one memo folder
  python3 render.py --all        # every data.json under data/voicememos/
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _config import cfg

from sync import write_transcript_md  # main() is __main__-guarded, safe to import

DATA = cfg("VOICEMEMOS_DATA", "~/voicememos", expand=True)


def render(folder):
    dp = os.path.join(folder, "data.json")
    if not os.path.exists(dp):
        return False
    d = json.load(open(dp))
    write_transcript_md(os.path.join(folder, "transcript.md"), d["rec"], d["words"])
    return True


def main():
    args = sys.argv[1:]
    if args == ["--all"]:
        n = 0
        for name in sorted(os.listdir(DATA)):
            f = os.path.join(DATA, name)
            if os.path.isdir(f) and render(f):
                n += 1
                print("rendered", name)
        print(f"{n} re-rendered")
    elif args:
        print("ok" if render(args[0]) else "no data.json in that folder")
    else:
        sys.exit("usage: render.py <folder> | --all")


if __name__ == "__main__":
    main()
