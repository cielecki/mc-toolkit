#!/usr/bin/env python3
"""Speaker IDENTIFICATION layer — turn anonymous diarization clusters into named
people via enrolled voiceprints. Runs under the pyannote venv (~/.venvs/diarization).

Diarization (diarize.py) answers "who spoke when" but anonymously (SPEAKER_00/01).
This module answers "which of those is a known person" by matching each cluster's
voice embedding against pre-enrolled reference voiceprints (cosine similarity).

Embedding model: pyannote/wespeaker-voxceleb-resnet34-LM (256-d). Same family
community-1 uses internally; accessible without extra HF gating. Measured on
synthetic test voices: same-speaker cosine ~0.90, different-speaker ~0.10 — a
threshold of 0.5 separates with huge margin.

Voiceprints live OUTSIDE git (biometric data of real people):
  <data-dir>/voiceprints/<name>.npy
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _config import cfg

VOICEPRINTS_DIR = os.path.join(
    cfg("VOICEMEMOS_DATA", "~/voicememos", expand=True), "voiceprints")
EMBED_MODEL = "pyannote/wespeaker-voxceleb-resnet34-LM"
MIN_SEG_S = 0.6          # wespeaker needs a little audio; skip shorter crops
DEFAULT_THRESHOLD = 0.5  # cosine; above → match, below → the unknown label

_INF = None              # lazy global (model load is ~2s)


def log(*a):
    print(*a, file=sys.stderr)


def _token(explicit=None):
    if explicit:
        return explicit
    v = os.environ.get("HF_TOKEN")
    if v:
        return v.strip()
    try:
        for line in open(os.path.expanduser("~/.claude/.env")):
            if line.startswith("HF_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return None


def _inference(token=None):
    global _INF
    if _INF is None:
        from pyannote.audio import Model, Inference
        model = Model.from_pretrained(EMBED_MODEL, token=_token(token))
        _INF = Inference(model, window="whole")
    return _INF


def _norm(v):
    v = np.asarray(v, dtype=np.float64).reshape(-1)
    return v / (np.linalg.norm(v) + 1e-9)


def embed_file(path, token=None):
    """Whole-file embedding (use for clean single-speaker enrollment samples)."""
    return _norm(_inference(token)(path))


def embed_segments(path, segments, token=None):
    """Duration-weighted mean embedding over a list of (start,end) seconds —
    used to embed one diarization cluster (its possibly-disjoint segments).
    Segments shorter than MIN_SEG_S are skipped; falls back to the longest
    segment, then the whole file, so a cluster always yields a vector."""
    from pyannote.core import Segment
    inf = _inference(token)
    usable = [(s, e) for s, e in segments if (e - s) >= MIN_SEG_S]
    if not usable:
        if segments:
            usable = [max(segments, key=lambda se: se[1] - se[0])]
        else:
            return embed_file(path, token)
    vecs, weights = [], []
    for s, e in usable:
        v = None
        # crop can fail when a diarization turn ends a few ms past the file
        # (rounding) — retry with a slightly trimmed end before giving up.
        for trim in (0.0, 0.1, 0.3):
            end = max(s + MIN_SEG_S * 0.5, e - trim)
            try:
                v = inf.crop(path, Segment(s, end))
                break
            except Exception as ex:
                last = ex
        if v is None:
            log(f"speaker_id: crop {s:.2f}-{e:.2f} skipped ({last})")
            continue
        vecs.append(_norm(v))
        weights.append(e - s)
    if not vecs:
        return embed_file(path, token)
    w = np.asarray(weights)
    return _norm(np.average(np.vstack(vecs), axis=0, weights=w))


# --- voiceprint registry -----------------------------------------------------
def enroll(name, sample_paths, token=None):
    """Compute a voiceprint from one or more clean single-speaker samples (mean)
    and save it to VOICEPRINTS_DIR/<name>.npy. Returns the vector."""
    os.makedirs(VOICEPRINTS_DIR, exist_ok=True)
    vecs = [embed_file(p, token) for p in sample_paths]
    vp = _norm(np.mean(np.vstack(vecs), axis=0))
    np.save(os.path.join(VOICEPRINTS_DIR, f"{name}.npy"), vp)
    return vp


def load_voiceprints():
    """{name: vector} for every enrolled <name>.npy. Empty dict if none."""
    out = {}
    if not os.path.isdir(VOICEPRINTS_DIR):
        return out
    for fn in os.listdir(VOICEPRINTS_DIR):
        if fn.endswith(".npy"):
            out[fn[:-4]] = _norm(np.load(os.path.join(VOICEPRINTS_DIR, fn)))
    return out


def match(cluster_vec, voiceprints, threshold=DEFAULT_THRESHOLD):
    """Best-matching enrolled name for a cluster embedding, or None if below
    threshold. Returns (name_or_None, best_score, all_scores_dict)."""
    if not voiceprints:
        return None, 0.0, {}
    scores = {name: float(cluster_vec @ vp) for name, vp in voiceprints.items()}
    best = max(scores, key=scores.get)
    return (best if scores[best] >= threshold else None), scores[best], scores


def audio_is_narrowband(path, cutoff_hz=3800, min_ratio=0.02, secs=40):
    """True if the audio has little energy above ~3.8 kHz — i.e. telephone-band /
    narrowband (compressed phone call). Used to pick a looser match threshold,
    since codec degradation drops the owner's cosine (measured 0.99 clean → 0.75 phone)."""
    import subprocess
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-t", str(secs),
             "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
            capture_output=True).stdout
        x = np.frombuffer(raw, dtype=np.float32)
        if x.size < 16000:
            return False
        spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
        freqs = np.fft.rfftfreq(x.size, 1 / 16000)
        ratio = spec[freqs >= cutoff_hz].sum() / (spec.sum() + 1e-9)
        return ratio < min_ratio
    except Exception:
        return False


def identify_turns(audio_path, turns, *, threshold=None,
                   unknown_label=cfg("VOICEMEMOS_UNKNOWN_LABEL", "unknown"), token=None):
    """Given diarization `turns` [{start,end,speaker}] (seconds), embed each
    anonymous cluster, match against enrolled voiceprints, and return a mapping
    {anon_label: resolved_name}. Unmatched clusters → `unknown_label`. If no
    voiceprints are enrolled, the mapping is identity (anon labels unchanged).

    threshold=None → auto per-condition: a looser 0.45 on narrowband/phone audio
    (codec degrades the owner's cosine), 0.60 on clean. Pass a float to override."""
    voiceprints = load_voiceprints()
    if not voiceprints:
        return {t["speaker"] for t in turns} and {s: s for s in {t["speaker"] for t in turns}}
    if threshold is None:
        nb = audio_is_narrowband(audio_path)
        threshold = 0.45 if nb else 0.60
        log(f"speaker_id: {'narrowband/phone' if nb else 'wideband/clean'} audio "
            f"→ threshold {threshold}")
    # group segments per anonymous speaker
    by_spk = {}
    for t in turns:
        by_spk.setdefault(t["speaker"], []).append((t["start"], t["end"]))
    # First pass: match each anonymous cluster to an enrolled voiceprint (or None).
    raw = {}  # spk -> (name|None, score, scores)
    for spk, segs in by_spk.items():
        cv = embed_segments(audio_path, segs, token)
        raw[spk] = match(cv, voiceprints, threshold)
    # Second pass: build the label map. Matched clusters take the enrolled name (two
    # clusters matching the SAME person correctly collapse to that name). UNMATCHED
    # clusters must STAY DISTINCT — they are different real people we just can't name,
    # so they get suffixed labels ("inny 1", "inny 2", …) rather than all collapsing
    # to one `unknown_label` (which made render.py merge several speakers into a single
    # block). A lone unknown keeps the bare label (no "1" suffix) for readability.
    unmatched = [spk for spk, (name, _, _) in raw.items() if not name]
    suffix = {}
    if len(unmatched) > 1:
        # number by first appearance in the timeline so labels read in spoken order
        order = sorted(unmatched, key=lambda s: min(st for st, _ in by_spk[s]))
        suffix = {spk: f"{unknown_label} {i}" for i, spk in enumerate(order, 1)}
    mapping = {}
    for spk, (name, score, scores) in raw.items():
        mapping[spk] = name or suffix.get(spk, unknown_label)
        log(f"speaker_id: {spk} -> {mapping[spk]}  (best {score:.3f}; "
            + ", ".join(f"{n}:{s:.2f}" for n, s in scores.items()) + ")")
    return mapping
