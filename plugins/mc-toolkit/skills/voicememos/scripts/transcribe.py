#!/usr/bin/env python3
"""Local Whisper word-level transcription for one run's concatenated audio.

Runs under a Python that has mlx-whisper + silero-vad + torch installed
(on this Mac: /opt/homebrew/bin/python3.14 — NOT 3.13). transcribe-run.py shells
out to it for `--engine whisper` (free, offline; no AssemblyAI spend).

Pipeline:
  1. ffmpeg-decode the audio to 16 kHz mono int16.
  2. silero-VAD finds speech segments. This is ESSENTIAL: mlx-whisper hallucinates
     plausible text on long silent stretches between spoken comments. We feed Whisper
     ONLY speech, so silence is never transcribed.
  3. Group adjacent VAD speech into ~28 s windows and transcribe each window as ONE
     CONTIGUOUS audio slice (NOT segment-by-segment). Whisper needs sentence-scale
     context — feeding it isolated sub-second VAD fragments roughly DOUBLED Polish WER
     (measured 2026-06-14: per-fragment 24.4 vs windowed 11.7 mean WER on the eval set,
     and on hard real-world audio 44→34 vs the gpt-4o-transcribe target). The slice is
     contiguous, so word timestamps offset linearly by the slice start (diarization needs
     real-audio time); long silence BETWEEN windows is still skipped (anti-hallucination).
  4. Emit JSON {"words":[{text,start,end,speaker,confidence}]} — the same shape
     transcribe-run.py consumes from AssemblyAI, in MILLISECONDS. No speaker
     labels (local STT can't diarize) → speaker=None.

Usage: python3.14 whisper_words.py <audio_path>      # JSON to stdout
       python3.14 whisper_words.py <audio_path> --language pl
"""
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _config import cfg

SR = 16000
# large-v3 (NON-turbo) on purpose: voice-memo processing is async (speed doesn't
# matter) and quality does. turbo ≈ large-v2 quality and OpenAI flags larger
# degradation on harder/non-English audio — for non-English memos the accuracy is
# worth the slower run. (A live/real-time variant would keep turbo for speed.)
# Override per-run with VOICEMEMOS_WHISPER_MODEL (the eval harness compares models).
MODEL = os.environ.get("VOICEMEMOS_WHISPER_MODEL", "mlx-community/whisper-large-v3-mlx")
PAD_S = 0.15  # keep 150ms around each speech segment so word edges aren't clipped
WIN_S = 28.0  # group VAD speech into <=28s windows — whisper's native context window is
              # 30s; sentence-scale context is what fixes the Polish word mangling

# mlx-whisper still emits YouTube-outro / subtitle-credit boilerplate on borderline
# audio even after VAD (documented in CLAUDE.md + voice-mode notes). Drop any
# Whisper SEGMENT whose text is dominated by one of these — they're never real
# in-game speech. Substring match, lowercased.
BOILERPLATE = (
    "amara.org", "napisy stworzone przez", "napisy: ", "subtitles by",
    "wszystkie prawa zastrzeżone", "dziękuję za uwagę", "dziękuję za oglądanie",
    "zapraszam do subskrypcji", "zapraszam na kanał", "do zobaczenia",
    "thanks for watching", "thank you for watching",
)


def _is_boilerplate(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return True
    return any(b in t for b in BOILERPLATE)


def _drop_segment(seg: dict) -> bool:
    """Whisper's own hallucination/repetition gates + our boilerplate filter.
    `compression_ratio > 2.4` is the standard openai-whisper signal for a
    degenerate repetition loop ("placu internetu placu internetu…"); a very low
    `avg_logprob` means Whisper had no real signal. Drop those segments whole."""
    if _is_boilerplate(seg.get("text", "")):
        return True
    cr = seg.get("compression_ratio")
    if isinstance(cr, (int, float)) and cr > 2.4:
        return True  # repetition loop
    lp = seg.get("avg_logprob")
    if isinstance(lp, (int, float)) and lp < -1.0:
        return True  # no real signal — likely hallucinated
    return False


def decode_pcm(path):
    """ffmpeg → raw 16 kHz mono int16 numpy array."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"],
        check=True, capture_output=True,
    )
    return np.frombuffer(r.stdout, dtype=np.int16)


def speech_ranges(a):
    """silero-VAD speech timestamps in SAMPLES. Empty list if no speech."""
    import torch
    from silero_vad import load_silero_vad, get_speech_timestamps
    model = load_silero_vad()
    return get_speech_timestamps(
        torch.from_numpy(a.astype(np.float32) / 32768.0), model, sampling_rate=SR
    )


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: whisper_words.py <audio_path> [--language pl]")
    path = sys.argv[1]
    language = cfg("VOICEMEMOS_LANG", "auto")
    if "--language" in sys.argv:
        language = sys.argv[sys.argv.index("--language") + 1]

    a = decode_pcm(path)
    af = a.astype(np.float32) / 32768.0
    ranges = speech_ranges(a)
    if not ranges:
        print(json.dumps({"words": [], "text": "", "_engine": "whisper-local"}))
        return

    import mlx_whisper
    pad = int(PAD_S * SR)

    if language == "auto":
        # detect ONCE on the longest speech segment (most signal), lock for the whole
        # memo — per-segment detection flip-flops on short utterances. Forcing the
        # wrong language mangles the transcript (an English memo forced to pl lost
        # half its words and translated the rest).
        # verbose=None (NOT False): with language=None, whisper prints
        # "Detected language: …" to STDOUT unless verbose is None — which corrupts
        # the JSON contract of this script. redirect_stdout is the safety belt.
        import contextlib
        longest = max(ranges, key=lambda r: r["end"] - r["start"])
        s = max(0, longest["start"] - pad)
        e = min(len(a), longest["end"] + pad)
        with contextlib.redirect_stdout(sys.stderr):
            probe = mlx_whisper.transcribe(
                af[s:e], path_or_hf_repo=MODEL, language=None, verbose=None,
                condition_on_previous_text=False,
            )
        language = probe.get("language") or "en"
        print(f"language auto-detected: {language}", file=sys.stderr)

    # Group adjacent VAD speech into <=WIN_S contiguous windows. We extend a window
    # while its real-audio span (last.end − group.start) fits WIN_S, then start a new
    # one — so internal short pauses are KEPT (they give whisper context) but the long
    # dead air between groups is dropped. Each group is one contiguous real-audio slice,
    # so a single linear offset maps every word back to whole-audio time for diarization.
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
        # condition_on_previous_text left at whisper's default (True): cross-window
        # context helps Polish (measured), and the compression-ratio / avg-logprob gates
        # in _drop_segment catch the repetition loops that conditioning can trigger.
        res = mlx_whisper.transcribe(
            seg, path_or_hf_repo=MODEL, language=language, verbose=False,
            word_timestamps=True,
        )
        offset_ms = (s / SR) * 1000.0
        for seg_obj in res.get("segments", []):
            if _drop_segment(seg_obj):
                continue  # boilerplate / repetition-loop / no-signal hallucination
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
    print(json.dumps({
        "words": words,
        "text": " ".join(w["text"] for w in words),
        "language": language,
        "_engine": "whisper-local",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
