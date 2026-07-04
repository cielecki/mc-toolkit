#!/usr/bin/env python3
"""Deterministic transcript-health gate — objective signals, never content.

Distinguishes "truly empty" (VAD ≈ silence) from "transcription-suspect"
(speech present but the STT output is sparse / low-confidence / looping),
so the router never archives a failed transcription as junk.

Thresholds are heuristic and tunable — see the design spec's "do wypracowania".
"""
from collections import Counter

EMPTY_SPEECH_S = 3.0     # < this much detected speech → treat as silent/ambient
MIN_WPM = 30.0           # human speech ~100-150 wpm; < this of DETECTED speech = STT barely caught it
LOW_CONF = 0.45          # mean word probability below this → suspect
LOOP_TRIGRAM_SHARE = 0.5 # one 3-gram covering ≥ this share of tokens → repetition loop


def mean_confidence(words):
    vals = [w["confidence"] for w in words if w.get("confidence") is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def is_repetition_loop(words):
    toks = [(w.get("text") or "").lower() for w in words]
    toks = [t for t in toks if t]
    if len(toks) < 12:
        return False
    grams = Counter(tuple(toks[i:i + 3]) for i in range(len(toks) - 2))
    top = grams.most_common(1)[0][1]
    return top / max(1, len(toks) - 2) >= LOOP_TRIGRAM_SHARE


def classify_health(speech_seconds, word_count, duration_s, mean_conf, is_loop):
    if speech_seconds < EMPTY_SPEECH_S:
        return "empty"
    if word_count == 0 or is_loop:
        return "suspect"
    wpm = word_count / (speech_seconds / 60.0)
    if wpm < MIN_WPM:
        return "suspect"
    if mean_conf is not None and mean_conf < LOW_CONF:
        return "suspect"
    return "healthy"
