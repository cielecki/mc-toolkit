#!/usr/bin/env python3
"""Enroll a known voice → a reusable voiceprint. Run under the pyannote venv:

    ~/.venvs/diarization/bin/python enroll.py <name> <sample1.wav> [sample2 ...] [--phone-aug]

Give one or more CLEAN single-speaker samples of the person (cleaner + more is better;
30–60s total is plenty). The mean embedding is saved to
<data-dir>/voiceprints/<name>.npy and used by identify.py.

--phone-aug : ALSO enroll telephone-band-degraded copies of each sample (highpass 300 /
  lowpass 3400 / 8 kHz mono — simulates phone codec). This multi-condition enrollment
  makes the voiceprint robust to phone audio, where the owner's cosine otherwise drops
  (measured 0.99 clean → 0.75 phone — the documented codec/enroll-test mismatch). Recommended.

Your own solo voice memos are ideal enrollment material; pass several short clips.
"""
import argparse
import os
import subprocess
import sys
import tempfile

import speaker_id as sid


def phone_degrade(src):
    """telephone-band-degrade a sample to a temp wav (caller deletes)."""
    dst = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src,
                    "-af", "highpass=f=300,lowpass=f=3400", "-ar", "8000", "-ac", "1", dst],
                   check=True)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("samples", nargs="+")
    ap.add_argument("--phone-aug", action="store_true",
                    help="also enroll telephone-band-degraded copies (multi-condition)")
    args = ap.parse_args()
    for p in args.samples:
        if not os.path.exists(p):
            sys.exit(f"sample not found: {p}")

    samples, tmp = list(args.samples), []
    if args.phone_aug:
        for p in args.samples:
            d = phone_degrade(p)
            samples.append(d)
            tmp.append(d)
        sid.log(f"enroll: + {len(tmp)} telephone-band-degraded copies (multi-condition)")

    sid.log(f"enroll: '{args.name}' from {len(samples)} sample(s)")
    vp = sid.enroll(args.name, samples)
    sims = [float(sid.embed_file(p) @ vp) for p in samples]
    sid.log("enroll: per-sample similarity to voiceprint: "
            + ", ".join(f"{s:.3f}" for s in sims))
    for d in tmp:
        try:
            os.unlink(d)
        except OSError:
            pass
    print(f"Enrolled '{args.name}' -> {sid.VOICEPRINTS_DIR}/{args.name}.npy "
          f"(mean self-sim {sum(sims)/len(sims):.3f})")


if __name__ == "__main__":
    main()
