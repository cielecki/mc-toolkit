#!/usr/bin/env python3
"""Evaluation harness — measure transcription quality (WER/CER) per engine against
hand-corrected references, so engine choice is a number, not a vibe. Run under the
venv: ~/.venvs/diarization/bin/python eval.py

Why this exists: eyeballing two clips gave contradictory results (cloud won one call,
tied the other). This turns "which engine is best for my audio" into a measured table.

## Manifest — data/voicememos/eval/clips.json
A list of clips, each pointing at a hand-corrected reference transcript:
  [{"audio": "/abs/clip.m4a", "profile": "phone-noisy", "reference": "/abs/clip.ref.txt",
    "language": "pl"}]
profiles: solo-clean | meeting-clean | phone-noisy (span your real distribution; 2 each).
language: 2-letter code per clip ("pl" default) — you may record in multiple languages,
and forcing the wrong language mangles the transcript (proven on an English memo
whisper'd with pl: half the words dropped, rest translated).

## Building references (the part only you can do)
  eval.py --prepare /abs/clip.m4a --engine elevenlabs
This runs the strongest engine and writes <clip>.ref.txt as a DRAFT. Then you open it
and FIX EVERY WORD against the audio (casing, punctuation, numbers, Polish proper nouns).
Never trust an engine's raw output as ground truth — that biases the eval. Register the
clip in clips.json (the --prepare step appends it for you).

## Running
  eval.py                         # all engines × all clips → markdown table
  eval.py --engines whisper-large-v3,elevenlabs,assemblyai
Metrics: WER + CER, Polish-normalized (lowercase, strip punctuation, KEEP diacritics).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _config import cfg

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.join(cfg("VOICEMEMOS_DATA", "~/voicememos", expand=True), "eval")
MANIFEST = os.path.join(EVAL_DIR, "clips.json")
MLX_PY = cfg("VOICEMEMOS_MLX_PYTHON", "/opt/homebrew/bin/python3.14")

# engine -> how to get plain hypothesis text for an audio file
WHISPER_MODELS = {
    "whisper-large-v3": "mlx-community/whisper-large-v3-mlx",
    "whisper-turbo": "mlx-community/whisper-large-v3-turbo",
}
CLOUD = {  # script; assemblyai takes 2-letter codes, elevenlabs ISO 639-3
    "assemblyai": "assemblyai.py",
    "elevenlabs": "elevenlabs.py",
}
ISO3 = {"pl": "pol", "en": "eng"}  # elevenlabs language codes
ALL_ENGINES = list(WHISPER_MODELS) + list(CLOUD)


def log(*a):
    print(*a, file=sys.stderr)


def normalize(t):
    """Polish-aware WER normalization: lowercase, strip punctuation, KEEP diacritics
    (dropping ż/ź/ą is itself an error), collapse whitespace. \\w is unicode-aware."""
    t = t.lower()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def hyp_text(engine, audio, lang=cfg("VOICEMEMOS_LANG", "en")):
    """Run an engine and return its plain transcript text."""
    if engine in WHISPER_MODELS:
        env = dict(os.environ, VOICEMEMOS_WHISPER_MODEL=WHISPER_MODELS[engine])
        out = subprocess.run([MLX_PY, os.path.join(HERE, "transcribe.py"), audio,
                              "--language", lang], capture_output=True, text=True, env=env)
        if out.returncode != 0:
            raise RuntimeError(out.stderr[-300:])
        words = json.loads(out.stdout).get("words", [])
        return " ".join(w["text"] for w in words)
    script = CLOUD[engine]
    if engine == "elevenlabs":
        lang = ISO3.get(lang, lang)
    tmp = tempfile.NamedTemporaryFile(suffix=".md", delete=False).name
    out = subprocess.run([sys.executable, os.path.join(HERE, script), audio,
                          "--language", lang, "--out", tmp], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-300:])
    return md_speech(tmp)


def md_speech(path):
    """Extract just the spoken text from a transcript .md (drop metadata + speaker
    header lines like `**Speaker** [00:00]`)."""
    lines, after_rule = [], False
    for raw in open(path):
        l = raw.rstrip("\n")
        if l.strip() == "---":
            after_rule = True
            continue
        if not after_rule or not l.strip():
            continue
        if re.match(r"^\*\*.*\]\s*$", l):   # speaker header
            continue
        lines.append(l)
    return " ".join(lines)


def score(ref, hyp):
    import jiwer
    r, h = normalize(ref), normalize(hyp)
    return jiwer.wer(r, h) * 100, jiwer.cer(r, h) * 100


def cmd_prepare(audio, engine, lang):
    os.makedirs(EVAL_DIR, exist_ok=True)
    log(f"prepare: running {engine} on {os.path.basename(audio)}…")
    text = hyp_text(engine, audio, lang)
    ref = os.path.splitext(audio)[0] + ".ref.txt"
    open(ref, "w").write(text + "\n")
    # register in manifest
    clips = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else []
    if not any(c["audio"] == audio for c in clips):
        clips.append({"audio": audio, "profile": "UNSET", "reference": ref,
                      "language": lang})
        json.dump(clips, open(MANIFEST, "w"), ensure_ascii=False, indent=2)
    print(f"Draft reference: {ref}\n"
          f"→ HAND-CORRECT it against the audio (every word), then set 'profile' in {MANIFEST}.")


def cmd_run(engines):
    if not os.path.exists(MANIFEST):
        sys.exit(f"No manifest at {MANIFEST}. Build one with --prepare first.")
    clips = json.load(open(MANIFEST))
    rows, agg = [], {}
    for c in clips:
        ref = open(c["reference"]).read()
        for e in engines:
            try:
                wer, cer = score(ref, hyp_text(e, c["audio"], c.get("language", cfg("VOICEMEMOS_LANG", "en"))))
                rows.append((c["profile"], os.path.basename(c["audio"]), e, wer, cer))
                agg.setdefault((c["profile"], e), []).append(wer)
                log(f"  {os.path.basename(c['audio'])[:24]:24} {e:18} WER {wer:5.1f}  CER {cer:5.1f}")
            except Exception as ex:
                log(f"  {e} failed on {c['audio']}: {ex}")
    # markdown table
    print("\n## WER / CER (%, Polish-normalized; lower is better)\n")
    print("| Profile | Clip | Engine | WER | CER |")
    print("|---|---|---|---|---|")
    for prof, clip, e, wer, cer in sorted(rows):
        print(f"| {prof} | {clip} | {e} | {wer:.1f} | {cer:.1f} |")
    print("\n## Mean WER per profile × engine\n")
    print("| Profile | Engine | mean WER | n |")
    print("|---|---|---|---|")
    for (prof, e), v in sorted(agg.items()):
        print(f"| {prof} | {e} | {sum(v)/len(v):.1f} | {len(v)} |")
    if any(len(v) < 3 for v in agg.values()):
        print("\n_Note: <3 clips per cell — treat differences <~3 WER pts as noise; "
              "add more clips for confidence intervals._")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare", metavar="AUDIO", help="draft a reference for a clip")
    ap.add_argument("--engine", default="elevenlabs", help="engine for --prepare")
    ap.add_argument("--language", default=cfg("VOICEMEMOS_LANG", "en"), help="2-letter clip language for --prepare")
    ap.add_argument("--engines", help="comma list for --run (default: all)")
    args = ap.parse_args()
    if args.prepare:
        cmd_prepare(args.prepare, args.engine, args.language)
    else:
        engines = args.engines.split(",") if args.engines else ALL_ENGINES
        cmd_run(engines)


if __name__ == "__main__":
    main()
