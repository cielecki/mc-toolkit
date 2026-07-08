#!/usr/bin/env python3
"""Deterministic mechanics the in-session router calls: slug, folder rename, meta writes.
Keeps JSON/rename logic out of the LLM flow so decisions are recorded consistently."""
import json
import os
import re


def safe_slug(title, maxlen=48):
    s = (title or "").strip().lower()
    s = re.sub(r"[^\w\sąćęłńóśźżĄĆĘŁŃÓŚŹŻ-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s-]+", "-", s).strip("-")  # collapse runs of whitespace/hyphens → single -
    return (s[:maxlen].strip("-") or "memo")


def _date_prefix(memo_dir):
    base = os.path.basename(memo_dir.rstrip("/"))
    m = re.match(r"(\d{4}-\d{2}-\d{2})-", base)
    if not m:
        raise ValueError(f"memo folder name lacks a YYYY-MM-DD- prefix: {base!r}")
    return m.group(1)


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
    _update_state_out(target)  # keep sync's state.json in step with the rename
    return target


def _update_state_out(new_dir):
    """After a folder rename, repoint state.json[<id>]['out'] → new_dir so a later
    `sync.py --force` rewrites the renamed folder IN PLACE instead of re-materializing
    the memo at its (unchanged) app-title slug — the bug that spawned shadow duplicates.
    Best-effort: the rename already succeeded, so a state hiccup must not raise."""
    parent = os.path.dirname(new_dir)              # memo folders live directly in the data dir
    state_path = os.path.join(parent, "state.json")
    meta_path = os.path.join(new_dir, "meta.json")
    if not (os.path.exists(state_path) and os.path.exists(meta_path)):
        return
    try:
        mid = json.load(open(meta_path)).get("id")
        if not mid:
            return
        state = json.load(open(state_path))
        if isinstance(state.get(mid), dict):
            state[mid]["out"] = new_dir
            with open(state_path, "w") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def write_disposition(memo_dir, status, note):
    p = os.path.join(memo_dir, "meta.json")
    if os.path.exists(p):
        with open(p) as f:
            meta = json.load(f)
    else:
        meta = {}
    meta["status"] = status
    meta["routing_note"] = note
    with open(p, "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
