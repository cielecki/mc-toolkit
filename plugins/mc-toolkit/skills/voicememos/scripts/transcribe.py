#!/usr/bin/env python3
"""Local Whisper word-level transcription — THIN WRAPPER.

The implementation now lives ONCE in the shared STT layer:
`plugins/mc-toolkit/stt/engines/local.py` (also driven by the `mc-stt` CLI and reused by
other skills). This wrapper preserves the JSON stdout contract that sync.py / eval.py
consume, so nothing downstream changed:
  {"words":[{text,start,end(ms),speaker,confidence}], "text", "language", "_engine"}

Runs under a Python with mlx-whisper + silero-vad + torch (/opt/homebrew/bin/python3.14).
VOICEMEMOS_WHISPER_MODEL overrides the model (the eval harness compares models).

Usage: python3.14 transcribe.py <audio_path> [--language pl|auto]
"""
import json
import os
import sys

# Import the shared engine via realpath so it resolves through the ~/.claude/skills
# symlink too. stt/engines is three levels up from this scripts/ dir.
_HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "..", "stt", "engines")))
sys.path.insert(0, os.path.dirname(_HERE))  # voicememos root, for _config
from _config import cfg  # noqa: E402
import local  # noqa: E402  (stt/engines/local.py)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: transcribe.py <audio_path> [--language pl]")
    path = sys.argv[1]
    language = cfg("VOICEMEMOS_LANG", "en")
    if "--language" in sys.argv:
        language = sys.argv[sys.argv.index("--language") + 1]
    model = os.environ.get("VOICEMEMOS_WHISPER_MODEL")  # eval model override; None → default
    res = local.transcribe(path, language=language, model=model)
    res["_engine"] = "whisper-local"
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
