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
language: 2-letter code per clip ("pl" default) — if you record in multiple
languages, forcing the wrong one mangles the transcript (proven on an English
memo whisper'd with pl: half the words dropped, rest translated).

## Building references (the part only you can do)
  eval.py --prepare /abs/clip.m4a --engine elevenlabs
This runs the strongest engine and writes <clip>.ref.txt as a DRAFT. Then you open it
and FIX EVERY WORD against the audio (casing, punctuation, numbers, Polish proper nouns).
Never trust an engine's raw output as ground truth — that biases the eval. Register the
clip in clips.json (the --prepare step appends it for you).

References MAY be speaker-labeled — one turn per
paragraph, prefixed `MC: ` / `KAROL: ` / etc. WER scoring strips those prefixes;
the labels double as ground truth for future speaker-attribution scoring.

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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
OPENAI_MODELS = {  # engines via openai.py
    "openai": "gpt-4o-transcribe",
    "openai-mini": "gpt-4o-mini-transcribe",
    "openai-diarize": "gpt-4o-transcribe-diarize",
}
ISO3 = {"pl": "pol", "en": "eng"}  # elevenlabs language codes
ALL_ENGINES = list(WHISPER_MODELS) + list(CLOUD) + list(OPENAI_MODELS)


def log(*a):
    print(*a, file=sys.stderr)


def normalize(t):
    """Polish-aware WER normalization: lowercase, strip punctuation, KEEP diacritics
    (dropping ż/ź/ą is itself an error), collapse whitespace. \\w is unicode-aware."""
    t = t.lower()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


# hesitation tokens that carry no meaning — multi-char only, so real Polish words
# ("a", "no", "e") are never touched
FILLER_RE = re.compile(r"^(y{2,}|e{2,}|m{2,}|h+m+|mhm+|yhm+|aha+|u+h+m*|e?hm+|u+m+)$")


def lenient(t):
    """normalize() + drop filler tokens + collapse immediate word repeats — style
    differences that don't change meaning (one engine keeps every 'yyy'/false start,
    another drops them) shouldn't count as transcription errors."""
    out = []
    for w in normalize(t).split():
        if FILLER_RE.match(w):
            continue
        if out and out[-1] == w:
            continue
        out.append(w)
    return " ".join(out)


def hyp_text(engine, audio, lang=cfg("VOICEMEMOS_LANG", "auto"), fresh=False):
    """Return an engine's plain transcript text, cached under eval/hyps/ so
    re-scoring (new metric, new normalization) is free and re-uploads nothing.
    --fresh forces a re-run."""
    hyp_dir = os.path.join(EVAL_DIR, "hyps")
    cache = os.path.join(
        hyp_dir, f"{os.path.splitext(os.path.basename(audio))[0]}.{engine}.txt")
    if not fresh and os.path.exists(cache):
        return open(cache).read()
    text = _run_engine(engine, audio, lang)
    os.makedirs(hyp_dir, exist_ok=True)
    open(cache, "w").write(text)
    return text


def _run_engine(engine, audio, lang):
    if engine in WHISPER_MODELS:
        env = dict(os.environ, VOICEMEMOS_WHISPER_MODEL=WHISPER_MODELS[engine])
        out = subprocess.run([MLX_PY, os.path.join(HERE, "transcribe.py"), audio,
                              "--language", lang], capture_output=True, text=True, env=env)
        if out.returncode != 0:
            raise RuntimeError(out.stderr[-300:])
        words = json.loads(out.stdout).get("words", [])
        return " ".join(w["text"] for w in words)
    if engine in OPENAI_MODELS:
        out = subprocess.run([sys.executable, os.path.join(HERE, "openai.py"), audio,
                              "--language", lang, "--model", OPENAI_MODELS[engine]],
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(out.stderr[-300:])
        return out.stdout.strip()
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
    header lines like `**Alice** [00:00]`)."""
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


def ref_text(path):
    """Read a reference transcript, stripping optional `SPEAKER: ` prefixes
    (all-caps, start of line) so labeled refs score identically to plain ones."""
    out = []
    for line in open(path):
        out.append(re.sub(r"^\s*[A-ZĄĆĘŁŃÓŚŹŻ]{2,12}:\s*", "", line).strip())
    return " ".join(x for x in out if x)


def score(ref, hyp):
    """(WER strict, WER lenient, CER strict) — strict scores verbatim fidelity,
    lenient ignores filler/repeat style differences."""
    import jiwer
    wer = jiwer.wer(normalize(ref), normalize(hyp)) * 100
    wer_len = jiwer.wer(lenient(ref), lenient(hyp)) * 100
    cer = jiwer.cer(normalize(ref), normalize(hyp)) * 100
    return wer, wer_len, cer


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


def cmd_run(engines, fresh=False):
    if not os.path.exists(MANIFEST):
        sys.exit(f"No manifest at {MANIFEST}. Build one with --prepare first.")
    clips = json.load(open(MANIFEST))
    rows, agg = [], {}
    for c in clips:
        ref = ref_text(c["reference"])
        for e in engines:
            try:
                hyp = hyp_text(e, c["audio"], c.get("language", "pl"), fresh)
                wer, wer_len, cer = score(ref, hyp)
                rows.append((c["profile"], os.path.basename(c["audio"]), e, wer, wer_len, cer))
                agg.setdefault((c["profile"], e), []).append((wer, wer_len))
                log(f"  {os.path.basename(c['audio'])[:24]:24} {e:18} "
                    f"WER {wer:5.1f}  WER-len {wer_len:5.1f}  CER {cer:5.1f}")
            except Exception as ex:
                log(f"  {e} failed on {c['audio']}: {ex}")
    # markdown table
    print("\n## WER strict / WER lenient / CER (%, lower is better)\n")
    print("strict = verbatim; lenient additionally ignores fillers (yyy/eee/mhm) and")
    print("immediate word repeats — style, not meaning.\n")
    print("| Profile | Clip | Engine | WER | WER-len | CER |")
    print("|---|---|---|---|---|---|")
    for prof, clip, e, wer, wer_len, cer in sorted(rows):
        print(f"| {prof} | {clip} | {e} | {wer:.1f} | {wer_len:.1f} | {cer:.1f} |")
    print("\n## Mean WER per profile × engine\n")
    print("| Profile | Engine | mean WER | mean WER-len | n |")
    print("|---|---|---|---|---|")
    for (prof, e), v in sorted(agg.items()):
        mw = sum(x[0] for x in v) / len(v)
        ml = sum(x[1] for x in v) / len(v)
        print(f"| {prof} | {e} | {mw:.1f} | {ml:.1f} | {len(v)} |")
    if any(len(v) < 3 for v in agg.values()):
        print("\n_Note: <3 clips per cell — treat differences <~3 WER pts as noise; "
              "add more clips for confidence intervals._")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare", metavar="AUDIO", help="draft a reference for a clip")
    ap.add_argument("--engine", default="elevenlabs", help="engine for --prepare")
    ap.add_argument("--language", default=cfg("VOICEMEMOS_LANG", "auto"), help="2-letter clip language for --prepare")
    ap.add_argument("--engines", help="comma list for --run (default: all)")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore cached hypotheses in eval/hyps/ and re-run engines")
    args = ap.parse_args()
    if args.prepare:
        cmd_prepare(args.prepare, args.engine, args.language)
    else:
        engines = args.engines.split(",") if args.engines else ALL_ENGINES
        cmd_run(engines, args.fresh)


if __name__ == "__main__":
    main()
