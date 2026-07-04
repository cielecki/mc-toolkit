"""Local mlx-whisper transcription — VAD-windowed, word-level, fully on-device.

The canonical local STT engine (was voicememos/transcribe.py). Pipeline:
  1. ffmpeg-decode to 16 kHz mono int16.
  2. silero-VAD finds speech; we never feed Whisper long silence (it hallucinates there).
  3. Group adjacent VAD speech into <=WIN_S CONTIGUOUS windows and transcribe each window
     as ONE slice — Whisper needs sentence-scale context; isolated sub-second fragments
     roughly DOUBLE Polish WER (measured 2026-06-14: 24.4 vs 11.7 on the eval set).
     Contiguous slice → word timestamps offset linearly by slice start (diarization needs
     real-audio time); long silence BETWEEN windows is skipped (anti-hallucination).
  4. Return {"words":[{text,start,end(ms),speaker:None,confidence}], "text", "language",
     "speech_seconds"}.

condition_on_previous_text is left at whisper's default (True): cross-window context
helps Polish (measured); the compression-ratio / avg-logprob gates in _filter catch the
repetition loops conditioning can trigger. No initial_prompt — measured to HURT.
"""
import contextlib
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _filter import drop_segment  # noqa: E402

SR = 16000
DEFAULT_MODEL = "mlx-community/whisper-large-v3-mlx"
PAD_S = 0.15   # keep 150ms around each speech segment so word edges aren't clipped
WIN_S = 28.0   # group VAD speech into <=28s windows — whisper's native context window is 30s


def decode_pcm(path):
    """ffmpeg → raw 16 kHz mono int16 numpy array."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        check=True, capture_output=True,
    )
    return np.frombuffer(r.stdout, dtype=np.int16)


def speech_ranges(a):
    """silero-VAD speech timestamps in SAMPLES. Empty list if no speech."""
    import torch
    from silero_vad import load_silero_vad, get_speech_timestamps
    model = load_silero_vad()
    return get_speech_timestamps(
        torch.from_numpy(a.astype(np.float32) / 32768.0), model, sampling_rate=SR)


def speech_seconds_from_ranges(ranges, sr=SR):
    """Total detected-speech duration in seconds (VAD ranges are in samples)."""
    return round(sum(r["end"] - r["start"] for r in ranges) / sr, 1)


def transcribe(path, language="auto", model=None):
    """Transcribe one audio file. Returns {"words", "text", "language", "speech_seconds"}.
    language="auto" detects once on the longest speech segment and locks it."""
    import mlx_whisper
    model = model or DEFAULT_MODEL
    a = decode_pcm(path)
    af = a.astype(np.float32) / 32768.0
    ranges = speech_ranges(a)
    speech_s = speech_seconds_from_ranges(ranges)
    if not ranges:
        return {"words": [], "text": "", "language": language if language != "auto" else "en",
                "speech_seconds": speech_s}

    pad = int(PAD_S * SR)

    if language == "auto":
        # Detect ONCE on the longest segment (most signal), lock for the whole file —
        # per-segment detection flip-flops; forcing the wrong language mangles output.
        # verbose=None keeps whisper from printing "Detected language:" to stdout.
        longest = max(ranges, key=lambda r: r["end"] - r["start"])
        s = max(0, longest["start"] - pad)
        e = min(len(a), longest["end"] + pad)
        with contextlib.redirect_stdout(sys.stderr):
            probe = mlx_whisper.transcribe(
                af[s:e], path_or_hf_repo=model, language=None, verbose=None,
                condition_on_previous_text=False)
        language = probe.get("language") or "en"
        print(f"language auto-detected: {language}", file=sys.stderr)

    # Group adjacent VAD speech into <=WIN_S contiguous windows (keeps internal short
    # pauses for context, drops long dead air between groups).
    win = int(WIN_S * SR)
    groups, gs, ge = [], None, None
    for rng in ranges:
        s = max(0, rng["start"] - pad)
        e = min(len(a), rng["end"] + pad)
        if gs is None:
            gs, ge = s, e
        elif e - gs <= win:
            ge = e
        else:
            groups.append((gs, ge))
            gs, ge = s, e
    if gs is not None:
        groups.append((gs, ge))

    words = []
    for s, e in groups:
        seg = af[s:e]
        if len(seg) < SR * 0.1:  # <100ms — nothing to transcribe
            continue
        res = mlx_whisper.transcribe(
            seg, path_or_hf_repo=model, language=language, verbose=False,
            word_timestamps=True)
        offset_ms = (s / SR) * 1000.0
        for seg_obj in res.get("segments", []):
            if drop_segment(seg_obj):
                continue
            for w in seg_obj.get("words", []):
                txt = (w.get("word") or "").strip()
                if not txt:
                    continue
                words.append({
                    "text": txt,
                    "start": int(offset_ms + (w.get("start") or 0.0) * 1000.0),
                    "end": int(offset_ms + (w.get("end") or 0.0) * 1000.0),
                    "speaker": None,
                    "confidence": w.get("probability"),
                })

    words.sort(key=lambda w: w["start"])
    return {
        "words": words,
        "text": " ".join(w["text"] for w in words),
        "language": language,
        "speech_seconds": speech_s,
    }
