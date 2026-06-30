#!/usr/bin/env python3
"""End-to-end speaker diarization via NVIDIA Sortformer (MLX). Alternative to the
pyannote pipeline in diarize.py — predicts speaker COUNT and overlap natively (no
num_speakers hint, no clustering threshold), and runs on Metal in ~0.1-0.2s/clip vs
pyannote-on-CPU's minutes. On the gym clip that pyannote MERGED into 1 speaker,
Sortformer correctly found 2 (validated 2026-06-16, 5/5 speaker-count across
solo/phone/gym clips after the <2s phantom filter).

Runs under the MLX env (python3.14 — has mlx-audio), NOT the pyannote venv. So it
emits turns to stdout/JSON; identify.py (venv) consumes them via --turns to do the
voiceprint naming + word assignment. Output shape MATCHES diarize.py's turns:
  {"turns":[{"start":sec,"end":sec,"speaker":"SPEAKER_00"}], "_engine":"sortformer"}

Caps at 4 speakers (plenty for voice memos). Gives SEPARATION, not naming — the
voiceprint step (speaker_id) still maps SPEAKER_NN -> a real name downstream.

Usage: python3.14 sortformer_diarize.py <audio> [--min-talk 2.0] [--threshold 0.5]
"""
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict

import numpy as np

MODEL = os.environ.get("VOICEMEMOS_SORTFORMER_MODEL",
                       "mlx-community/diar_sortformer_4spk-v1-fp16")
SR = 16000


def decode_pcm(path):
    """ffmpeg → float32 mono 16 kHz array."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        check=True, capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


# Above this audio length (seconds) the one-shot `model.generate` path runs full
# O(seq_len^2) self-attention over the ENTIRE file in unified memory and OOMs the Mac
# (a 60-80 min memo blows up the attention scores/softmax activations → "Python quit
# unexpectedly" ×N + system-wide RAM exhaustion). Above the cap we switch to mlx-audio's
# built-in `generate_stream`, which processes fixed-duration chunks with a BOUNDED speaker
# cache + FIFO (spkcache_max/fifo_max) so peak memory is flat regardless of file length;
# the spkcache carries speaker identity across chunks so labels stay consistent.
STREAM_OVER_S = 600.0   # 10 min — short clips keep the validated one-shot path
# 180s chunks (not mlx-audio's 30s default): measured to track cross-chunk speaker
# identity better (fewer chunk boundaries = fewer handoffs) while peak stays ~5GB on a
# 78-min file. Validated 2026-06-30: same speaker keeps one label across 26/26 boundaries
# on the full 78-min file; a late-joining 4th speaker gets its own stable slot. The
# streaming spkcache (bounded, compressed-not-dropped) threads identity through chunks;
# the one-shot path OVER-MERGES on long audio (collapsed 3 real speakers → 1 on a 15-min
# slice) AND OOMs (O(seq_len^2) attention: ~520GB projected on 78-min → crashes a 128GB Mac).
STREAM_CHUNK_S = 180.0  # per-chunk window for the streaming path


def diarize(path, min_talk=2.0, threshold=0.5, min_duration=0.3, merge_gap=0.5):
    """Return turns [{start,end,speaker}] in SECONDS (diarize.py shape). Drops phantom
    speakers with < min_talk seconds total talk-time (Sortformer occasionally spawns a
    ~1s spurious cluster on noisy/phone audio — measured on phone-adam)."""
    from mlx_audio.vad import load
    model = load(MODEL)
    audio = decode_pcm(path)
    duration_s = len(audio) / SR

    segments = []
    if duration_s > STREAM_OVER_S:
        # Long file → chunked streaming (flat memory). Merge segments across chunks;
        # each yielded chunk's segments are already in absolute file time and share the
        # carried-forward speaker cache, so labels are consistent across chunks.
        print(f"sortformer: {duration_s:.0f}s > {STREAM_OVER_S:.0f}s → streaming "
              f"({STREAM_CHUNK_S:.0f}s chunks, bounded memory)", file=sys.stderr)
        for out in model.generate_stream(
                audio, sample_rate=SR, chunk_duration=STREAM_CHUNK_S,
                threshold=threshold, min_duration=min_duration, merge_gap=merge_gap):
            segments.extend(out.segments)
    else:
        res = model.generate(audio, sample_rate=SR, threshold=threshold,
                             min_duration=min_duration, merge_gap=merge_gap)
        segments = res.segments

    # total talk-time per raw speaker → drop phantoms
    talk = defaultdict(float)
    for s in segments:
        talk[s.speaker] += (s.end - s.start)
    keep = {spk for spk, t in talk.items() if t >= min_talk}
    if not keep and talk:  # everything below threshold → keep the loudest so we never emit 0
        keep = {max(talk, key=talk.get)}

    # stable relabel: SPEAKER_00 = most talk-time, then descending (deterministic)
    order = sorted(keep, key=lambda spk: -talk[spk])
    label = {spk: f"SPEAKER_{i:02d}" for i, spk in enumerate(order)}

    turns = [{"start": float(s.start), "end": float(s.end), "speaker": label[s.speaker]}
             for s in segments if s.speaker in keep]
    turns.sort(key=lambda t: t["start"])
    return turns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--min-talk", type=float, default=2.0,
                    help="drop speakers with less than this total talk-time (phantom filter)")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="Sortformer per-frame speaker-activity threshold (0-1)")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args()
    if not os.path.exists(args.audio):
        sys.exit(f"audio not found: {args.audio}")

    turns = diarize(args.audio, min_talk=args.min_talk, threshold=args.threshold)
    n = len({t["speaker"] for t in turns})
    print(f"sortformer: {len(turns)} turns, {n} speaker(s)", file=sys.stderr)
    payload = {"turns": turns, "_engine": "sortformer"}
    if args.out:
        json.dump(payload, open(args.out, "w"), ensure_ascii=False)
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
