#!/usr/bin/env python3
"""On-demand overview: scan memo folders, print a status table. No stored state —
the source of truth stays distributed in each folder's meta.json."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # voicememos/ root, for _config
from _config import cfg

DATED = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def scan(data_dir):
    rows = []
    for name in sorted(os.listdir(data_dir), reverse=True):
        d = os.path.join(data_dir, name)
        if not os.path.isdir(d) or not DATED.match(name):
            continue
        mp = os.path.join(d, "meta.json")
        if not os.path.exists(mp):
            continue
        m = json.load(open(mp))
        rows.append({
            "folder": name,
            "title": m.get("generated_title") or m.get("title") or "",
            "health": m.get("transcript_health") or "?",
            "status": m.get("status") or "?",
            "note": (m.get("routing_note") or "")[:60],
        })
    return rows


def format_table(rows):
    head = f"{'folder':<40} {'health':<8} {'status':<14} title / note"
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(f"{r['folder']:<40} {r['health']:<8} {r['status']:<14} {r['title']} — {r['note']}")
    return "\n".join(lines)


if __name__ == "__main__":
    data = cfg("VOICEMEMOS_DATA", "~/voicememos", expand=True)
    print(format_table(scan(data)))
