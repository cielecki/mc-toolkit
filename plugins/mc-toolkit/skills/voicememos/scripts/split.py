#!/usr/bin/env python3
"""
Split one long multi-session recording into per-session child memo folders.

The deterministic core of the Step 0.5 "split" procedure (SKILL.md): given a
boundary list, it slices audio (ffmpeg, lossless copy), slices the transcript by
timestamp, writes a per-child meta.json (carrying `source` / `time_range` /
`talk_index` for traceability), and marks the master `routed`. Boundary DETECTION
(which timestamps, which titles) is the LLM step that feeds this — either an
in-session pass or an automated pre-routing pass; this script is the mechanical
executor so the cut is reproducible and never hand-done.

Usage:
    python3 split.py <memo_dir> <segments.json>

segments.json = [
  {"slug": "ivan-netflix-ai-adoption", "title": "Ivan (Netflix) — adopcja AI",
   "start": "05:48", "end": "17:45"},              # status defaults to needs-routing
  {"slug": "kuluary", "title": "Networking", "start": "58:16", "end": "1:14:37",
   "status": "archived"},                          # ambient/silence tail
  ...
]
Timestamps: "MM:SS" or "H:MM:SS". Children land as <parent>/<YYYY-MM-DD>-<slug>.
The master keeps its audio/transcript and is flagged routed (note lists children).
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import route  # noqa: E402

HEADER_RE = re.compile(r"^\*\*.+?\*\*\s*\[(\d+:\d+(?::\d+)?)\]\s*$")


def parse_ts(s):
    parts = [int(x) for x in s.strip().split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"bad timestamp: {s!r}")


def parse_blocks(transcript_path):
    """Return (preamble_lines, [(ts_seconds, [block_lines])])."""
    lines = open(transcript_path, encoding="utf-8").read().splitlines()
    preamble, blocks, cur_ts, cur = [], [], None, []
    for ln in lines:
        m = HEADER_RE.match(ln)
        if m:
            if cur_ts is not None:
                blocks.append((cur_ts, cur))
            elif cur:
                preamble.extend(cur)
            cur_ts, cur = parse_ts(m.group(1)), [ln]
        else:
            cur.append(ln)
    if cur_ts is not None:
        blocks.append((cur_ts, cur))
    elif cur:
        preamble.extend(cur)
    return preamble, blocks


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    memo_dir = os.path.abspath(sys.argv[1].rstrip("/"))
    segs = json.load(open(sys.argv[2], encoding="utf-8"))
    parent = os.path.dirname(memo_dir)
    master_slug = os.path.basename(memo_dir)

    meta_path = os.path.join(memo_dir, "meta.json")
    meta = json.load(open(meta_path, encoding="utf-8"))
    date10 = str(meta.get("date", ""))[:10] or master_slug[:10]
    master_title = meta.get("generated_title") or meta.get("title") or master_slug

    audio = os.path.join(memo_dir, "audio.m4a")
    transcript = os.path.join(memo_dir, "transcript.md")
    if not os.path.exists(audio):
        sys.exit(f"no audio.m4a in {memo_dir}")
    _, blocks = parse_blocks(transcript) if os.path.exists(transcript) else (None, [])

    made = []
    for i, seg in enumerate(segs, 1):
        s, e = parse_ts(seg["start"]), parse_ts(seg["end"])
        slug = seg["slug"]
        status = seg.get("status", "needs-routing")
        out_dir = os.path.join(parent, f"{date10}-{slug}")
        os.makedirs(out_dir, exist_ok=True)

        # 1) audio slice — lossless stream copy. -t <duration> (not -to) is
        # unambiguous with input-seek; keyframe-accurate copy is fine for talks.
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", seg["start"], "-t", str(e - s),
             "-i", audio, "-c", "copy", os.path.join(out_dir, "audio.m4a")],
            capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"ffmpeg failed on {slug}:\n{r.stderr[-800:]}")

        # 2) transcript slice — blocks whose timestamp falls in [s, e)
        kept = [b for (ts, b) in blocks if s <= ts < e]
        head = (f"# {seg['title']}\n\n"
                f"- **Source**: split from `{master_slug}` ({seg['start']}–{seg['end']})\n"
                f"- **Date**: {date10}\n- **Part**: {i}/{len(segs)}\n\n---\n")
        with open(os.path.join(out_dir, "transcript.md"), "w", encoding="utf-8") as f:
            f.write(head + "\n".join("\n".join(b) for b in kept) + "\n")

        # 3) per-child meta — merge base fields, add split provenance
        cmeta = {k: meta[k] for k in ("engine", "transcript_health", "language",
                                      "speakers") if k in meta}
        cmeta.update({
            "date": date10,
            "generated_title": seg["title"],
            "original_title": f"{master_title} — part {i}",
            "source": f"split from {master_slug}",
            "time_range": f"{seg['start']}-{seg['end']}",
            "time_range_s": [s, e],
            "duration_s": e - s,
            "talk_index": i,
            "transcript_health": meta.get("transcript_health", "healthy"),
            "status": status,
        })
        json.dump(cmeta, open(os.path.join(out_dir, "meta.json"), "w"),
                  ensure_ascii=False, indent=2)
        made.append(f"{date10}-{slug}({status})")
        print(f"  → {date10}-{slug}  [{seg['start']}-{seg['end']}]  {status}")

    note = (f"Split into {len(made)} parts (Step 0.5): " + ", ".join(made) +
            ". Master zachowany; child memo routowane niezaleznie.")
    route.write_disposition(memo_dir, "routed", note)
    print(f"master {master_slug} → routed ({len(made)} children)")


if __name__ == "__main__":
    main()
