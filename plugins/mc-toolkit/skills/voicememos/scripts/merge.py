#!/usr/bin/env python3
"""Merge an ordered list of memo folders back into ONE — the counterpart to split.py.

For a recording that was interrupted and resumed (phone stopped, storage, manual pause)
and landed as several separate memos, this stitches them into a single continuous memo:
concatenates the audio losslessly (ffmpeg concat), concatenates the `data.json` words
offsetting each subsequent recording by the running audio duration (so word timestamps
stay monotonic), re-renders `transcript.md`, and marks the absorbed source folders
`archived` (kept as provenance). Operates on `data.json` (per-word ms timestamps) — the
authoritative timeline — NOT the rendered transcript headers.

Usage:
    python3 merge.py <out_slug> <folder1> <folder2> [<folder3> ...]

Folders MUST be in chronological order. The merged memo lands at
<parent>/<date>-<out_slug> (date = first folder's). Every source needs a data.json
(sync.py v0.3.4+); older folders must be re-run through sync.py once first.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import route  # noqa: E402
from sync import write_transcript_md  # noqa: E402


def audio_dur_ms(path):
    """Exact audio duration in ms via ffprobe (the concat offset for the next segment)."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"ffprobe failed on {path}: {r.stderr[-300:]}")
    return int(round(float(r.stdout.strip()) * 1000))


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    out_slug = sys.argv[1]
    folders = [os.path.abspath(f.rstrip("/")) for f in sys.argv[2:]]
    for f in folders:
        if not os.path.exists(os.path.join(f, "data.json")):
            sys.exit(f"no data.json in {f} — re-run sync.py on it first")
        if not os.path.exists(os.path.join(f, "audio.m4a")):
            sys.exit(f"no audio.m4a in {f}")

    parent = os.path.dirname(folders[0])
    d0 = json.load(open(os.path.join(folders[0], "data.json"), encoding="utf-8"))
    date10 = str(d0["rec"].get("date", ""))[:10] or os.path.basename(folders[0])[:10]
    out_dir = os.path.join(parent, f"{date10}-{out_slug}")
    os.makedirs(out_dir, exist_ok=True)

    # 1) concat audio — lossless stream copy via the concat demuxer.
    listfile = os.path.join(out_dir, "_concat.txt")
    with open(listfile, "w") as f:
        for fo in folders:
            f.write(f"file '{os.path.join(fo, 'audio.m4a')}'\n")
    out_audio = os.path.join(out_dir, "audio.m4a")
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
         "-c", "copy", out_audio], capture_output=True, text=True)
    os.remove(listfile)
    if r.returncode != 0:
        sys.exit(f"ffmpeg concat failed:\n{r.stderr[-800:]}")

    # 2) concat words, offsetting each subsequent recording by the running audio duration.
    all_words, offset_ms, titles = [], 0, []
    for fo in folders:
        d = json.load(open(os.path.join(fo, "data.json"), encoding="utf-8"))
        titles.append(d["rec"].get("title", ""))
        for w in d["words"]:
            all_words.append({**w, "start": w["start"] + offset_ms,
                              "end": w["end"] + offset_ms})
        offset_ms += audio_dur_ms(os.path.join(fo, "audio.m4a"))

    rec = {"title": out_slug, "date": d0["rec"].get("date"),
           "duration_s": round(offset_ms / 1000, 1)}
    json.dump({"rec": rec, "words": all_words},
              open(os.path.join(out_dir, "data.json"), "w"), ensure_ascii=False)

    # 3) render transcript.md from the merged words
    write_transcript_md(os.path.join(out_dir, "transcript.md"), rec, all_words)

    # 4) per-merged meta — carry base fields, add merge provenance
    bm_path = os.path.join(folders[0], "meta.json")
    bm = json.load(open(bm_path, encoding="utf-8")) if os.path.exists(bm_path) else {}
    meta = {k: bm[k] for k in ("engine", "language", "transcript_health") if k in bm}
    meta.update({
        "date": date10,
        "generated_title": out_slug,
        "original_title": " + ".join(t for t in titles if t),
        "source": "merged from " + ", ".join(os.path.basename(f) for f in folders),
        "duration_s": rec["duration_s"],
        "transcript_health": bm.get("transcript_health", "healthy"),
        "status": "needs-routing",
    })
    json.dump(meta, open(os.path.join(out_dir, "meta.json"), "w"),
              ensure_ascii=False, indent=2)

    # 5) mark the absorbed sources archived (kept as provenance)
    for fo in folders:
        route.write_disposition(
            fo, "archived",
            f"Sklejone w {date10}-{out_slug} (przerwane/wznowione nagranie). "
            "Zachowane jako źródło; merged memo routowane niezaleznie.")

    print(f"merged {len(folders)} → {date10}-{out_slug}  "
          f"({rec['duration_s']}s, {len(all_words)} words)")


if __name__ == "__main__":
    main()
