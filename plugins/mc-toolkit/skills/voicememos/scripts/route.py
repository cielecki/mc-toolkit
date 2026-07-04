#!/usr/bin/env python3
"""Deterministic mechanics the in-session router calls: slug, folder rename, meta writes.
Keeps JSON/rename logic out of the LLM flow so decisions are recorded consistently."""
import json
import os
import re


def safe_slug(title, maxlen=48):
    s = (title or "").strip().lower()
    s = re.sub(r"[^\w\sąćęłńóśźżĄĆĘŁŃÓŚŹŻ-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s).strip("-")
    return (s[:maxlen].strip("-") or "memo")


def _date_prefix(memo_dir):
    base = os.path.basename(memo_dir.rstrip("/"))
    m = re.match(r"(\d{4}-\d{2}-\d{2})-", base)
    return m.group(1) if m else base.split("-")[0]


def rename_memo(memo_dir, new_slug):
    """Rename <parent>/<date>-<old> → <parent>/<date>-<new_slug>, collision-safe.
    Returns the new absolute path (or the original if the target equals the source)."""
    memo_dir = memo_dir.rstrip("/")
    parent = os.path.dirname(memo_dir)
    date = _date_prefix(memo_dir)
    base = f"{date}-{new_slug}"
    target = os.path.join(parent, base)
    if os.path.abspath(target) == os.path.abspath(memo_dir):
        return memo_dir
    n = 2
    while os.path.exists(target):
        target = os.path.join(parent, f"{base}-{n}")
        n += 1
    os.rename(memo_dir, target)
    return target


def write_disposition(memo_dir, status, note):
    p = os.path.join(memo_dir, "meta.json")
    meta = json.load(open(p)) if os.path.exists(p) else {}
    meta["status"] = status
    meta["routing_note"] = note
    json.dump(meta, open(p, "w"), ensure_ascii=False, indent=2)
