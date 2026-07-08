#!/usr/bin/env python3
"""voicememos sync orchestrator (entry point).

Pipeline:
  1. snapshot_trigger.sh  → mirror the protected Voice Memos container locally (FDA app)
  2. read CloudRecordings.db → enumerate recordings (ZCLOUDRECORDING)
  3. for each NEW recording WITH local audio:
       transcribe.py (mlx-whisper + silero-VAD, python3.14 env)  → words[]
       identify.py   (pyannote diarize + voiceprint match, venv) → words + known/unknown speaker
  4. write data/voicememos/<date>-<slug>/{transcript.md, meta.json, audio.m4a}
  5. track per-recording state for incremental re-runs.

Cloud-only recordings (ZPATH null / evicted — audio not downloaded to this Mac) get
a metadata-only meta.json and are retried once their audio appears locally.

Schema (verified 2026-06-09): ZCLOUDRECORDING — ZENCRYPTEDTITLE (plaintext title),
ZCUSTOMLABEL (ISO-timestamp fallback name), ZDATE (Core Data epoch), ZDURATION,
ZPATH (m4a filename under Recordings/, null when not local), ZUNIQUEID, ZEVICTIONDATE.

Usage:
  python3 sync.py                 # full incremental sync
  python3 sync.py --limit 3       # process only the N newest (with local audio) — for quick runs
  python3 sync.py --force         # reprocess even if state says done
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _config import cfg
import quality  # scripts/ is already on sys.path (line 35)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = cfg("VOICEMEMOS_DATA", "~/voicememos", expand=True)
SNAPSHOT = os.path.join(DATA, "snapshot")
RECORDINGS = os.path.join(SNAPSHOT, "Recordings")
OUT = DATA
STATE_PATH = os.path.join(DATA, "state.json")
MLX_PY = cfg("VOICEMEMOS_MLX_PYTHON", "/opt/homebrew/bin/python3.14")
VENV_PY = cfg("VOICEMEMOS_VENV_PYTHON", "~/.venvs/diarization/bin/python", expand=True)
# Diarization backend: "sortformer" (default — end-to-end NVIDIA Sortformer via mlx-audio,
# predicts speaker count natively, ~1000x faster than pyannote-on-CPU, separates close-mic
# speakers pyannote merges; caps at 4 speakers) or "pyannote" (the clustering pipeline).
DIAR_ENGINE = cfg("VOICEMEMOS_DIAR_ENGINE", "sortformer")
CORE_DATA_EPOCH = 978307200  # 2001-01-01 UTC in unix seconds


def log(*a):
    print(*a, file=sys.stderr)


def find_db():
    for root, _d, files in os.walk(SNAPSHOT):
        if "CloudRecordings.db" in files:
            return os.path.join(root, "CloudRecordings.db")
    return None


def slugify(s, maxlen=48):
    s = (s or "").strip()
    s = re.sub(r"[^\w\sąćęłńóśźżĄĆĘŁŃÓŚŹŻ-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s).strip("-")
    return (s[:maxlen].strip("-") or "memo").lower()


def cdate(z):
    if z is None:
        return None
    return datetime.datetime.fromtimestamp(z + CORE_DATA_EPOCH, datetime.timezone.utc)


def enumerate_recordings(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT ZUNIQUEID, ZENCRYPTEDTITLE, ZCUSTOMLABEL, ZDATE, ZDURATION, "
        "ZLOCALDURATION, ZPATH, ZEVICTIONDATE FROM ZCLOUDRECORDING ORDER BY ZDATE DESC"
    ).fetchall()
    con.close()
    out = []
    for r in rows:
        path = r["ZPATH"]
        audio = os.path.join(RECORDINGS, path) if path else None
        local = bool(audio and os.path.exists(audio))
        dt = cdate(r["ZDATE"])
        title = (r["ZENCRYPTEDTITLE"] or r["ZCUSTOMLABEL"] or "memo")
        if isinstance(title, (bytes, bytearray)):  # defensive: truly-encrypted title
            title = (r["ZCUSTOMLABEL"] or "memo")
        out.append({
            "id": r["ZUNIQUEID"],
            "title": str(title),
            "date": dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None,
            "date_folder": dt.strftime("%Y-%m-%d") if dt else "undated",
            "duration_s": round(r["ZDURATION"] or r["ZLOCALDURATION"] or 0, 1),
            "audio": audio if local else None,
            "audio_local": local,
            "evicted": bool(r["ZEVICTIONDATE"]),
        })
    return out


def run_json(argv, cwd=None):
    """Run a subprocess that prints JSON to stdout; return parsed obj (stderr → our stderr)."""
    p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{argv[1] if len(argv) > 1 else argv[0]} failed: {p.stderr[-500:]}")
    return json.loads(p.stdout)


def write_transcript_md(path, rec, words):
    """Transcript in a consistent, portable format: a `# Title`, a `- **Key**: value`
    metadata block, a `---` rule, then turns as `**Speaker** [MM:SS]` with the
    speech below and a blank line between turns."""
    def clock(seconds):
        s = int(seconds)
        h, m, sec = s // 3600, (s % 3600) // 60, s % 60
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
    date = (rec.get("date") or "")[:10] or "?"
    spk_set = sorted({(w.get("speaker") or "?") for w in words}) if words else []
    lines = [f"# {rec['title']}", "",
             f"- **Date**: {date}",
             f"- **Duration**: {clock(rec['duration_s'])}"]
    if spk_set:
        lines.append(f"- **Speakers**: {' / '.join(spk_set)}")
    lines += ["- **Source**: Apple Voice Memos", "", "---", ""]
    if not words:
        lines.append("_(no speech transcribed)_")
    else:
        cur_spk, buf, buf_start = None, [], words[0]["start"]
        def flush():
            if buf:
                lines.append(f"**{cur_spk or '?'}** [{clock(buf_start / 1000)}]")
                lines.append(" ".join(buf))
                lines.append("")
        for w in words:
            spk = w.get("speaker") or "?"
            if spk != cur_spk:
                flush(); cur_spk, buf, buf_start = spk, [], w["start"]
            buf.append(w["text"])
        flush()
    with open(path, "w") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="process only N newest with local audio")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-snapshot", action="store_true", help="skip the FDA snapshot step")
    ap.add_argument("--include-evicted", action="store_true",
                    help="also process evicted recordings (ZEVICTIONDATE set — likely deleted / "
                         "old offloaded; default is active library only, to not resurrect deleted memos)")
    args = ap.parse_args()

    if not args.no_snapshot:
        log("voicememos: triggering snapshot…")
        if subprocess.run(["bash", os.path.join(HERE, "snapshot_trigger.sh")],
                          env={**os.environ, "VOICEMEMOS_DATA": DATA}).returncode != 0:
            sys.exit("snapshot failed — see references/fda-setup.md (FDA grant needed?).")

    db = find_db()
    if not db:
        sys.exit(f"CloudRecordings.db not found under {SNAPSHOT}")
    recs = enumerate_recordings(db)
    # Default to the ACTIVE library only (ZEVICTIONDATE IS NULL). Evicted rows are a
    # mix of deleted + old offloaded recordings whose audio may still linger in the
    # container — processing them would resurrect deleted memos. --include-evicted opts in.
    pool = recs if args.include_evicted else [r for r in recs if not r["evicted"]]
    n_evicted = sum(1 for r in recs if r["evicted"])
    local = [r for r in pool if r["audio_local"]]
    log(f"voicememos: {len(recs)} in DB | processing "
        f"{'ALL incl. evicted' if args.include_evicted else 'active only'}: {len(pool)} rows, "
        f"{len(local)} with local audio, {len(pool)-len(local)} active-but-audio-pending"
        + ("" if args.include_evicted else f" | skipping {n_evicted} evicted (use --include-evicted)"))

    state = {}
    if os.path.exists(STATE_PATH):
        # Always load state — even with --force. --force reprocesses every recording
        # (the `todo` filter below ignores `done`), but we still need each memo's prior
        # `out` path so a rewrite lands in the (possibly renamed/routed) existing folder
        # instead of spawning a title-slug duplicate.
        state = json.load(open(STATE_PATH))

    def save_state():
        json.dump(state, open(STATE_PATH, "w"), ensure_ascii=False, indent=2)

    todo = [r for r in local if args.force or state.get(r["id"], {}).get("done") is not True]
    if args.limit:
        todo = todo[:args.limit]
    log(f"voicememos: {len(todo)} to process this run")

    done = 0
    for rec in todo:
        # Prefer the folder this recording already lives in (survives an in-session
        # rename via route.rename_memo, which repoints state[id]['out']); only fall back
        # to the app-title slug for a genuinely new recording. Prevents --force from
        # re-materializing a shadow copy at the original title after a routing rename.
        prev_out = state.get(rec["id"], {}).get("out")
        outdir = prev_out if (prev_out and os.path.isdir(prev_out)) \
            else os.path.join(OUT, f"{rec['date_folder']}-{slugify(rec['title'])}")
        os.makedirs(outdir, exist_ok=True)
        log(f"  → {rec['title']} ({rec['duration_s']:.0f}s)")
        try:
            shutil.copy2(rec["audio"], os.path.join(outdir, "audio.m4a"))
            # default "auto": detect language per memo — forcing the wrong one
            # mangles the transcript (half the words dropped, rest translated)
            words_obj = run_json([MLX_PY, os.path.join(HERE, "transcribe.py"),
                                  rec["audio"], "--language", cfg("VOICEMEMOS_LANG", "auto")])
            wpath = os.path.join(outdir, "_words.json")
            json.dump(words_obj, open(wpath, "w"), ensure_ascii=False)
            id_cmd = [VENV_PY, os.path.join(HERE, "identify.py"),
                      rec["audio"], "--words", wpath]
            tpath = None
            if DIAR_ENGINE == "sortformer":
                # end-to-end Sortformer runs in the mlx env → turns; identify.py (venv)
                # does voiceprint naming + word assignment on those turns via --turns.
                turns_obj = run_json([MLX_PY, os.path.join(HERE, "sortformer_diarize.py"),
                                      rec["audio"]])
                tpath = os.path.join(outdir, "_turns.json")
                json.dump(turns_obj, open(tpath, "w"), ensure_ascii=False)
                id_cmd += ["--turns", tpath]
            ident = run_json(id_cmd, cwd=HERE)
            words = ident["words"]
            write_transcript_md(os.path.join(outdir, "transcript.md"), rec, words)
            speech_s = words_obj.get("speech_seconds", 0.0)
            mconf = quality.mean_confidence(words)
            loop = quality.is_repetition_loop(words)
            health = quality.classify_health(speech_s, len(words), rec["duration_s"], mconf, loop)
            meta = {**{k: rec[k] for k in ("id", "title", "date", "duration_s", "audio_local")},
                    "original_title": rec["title"],
                    "language": words_obj.get("language"),
                    "speakers": ident.get("speakers"), "speaker_map": ident.get("mapping"),
                    "speech_seconds": speech_s,
                    "mean_confidence": mconf,
                    "transcript_health": health,
                    "engine": words_obj.get("_engine", "whisper-local"),
                    "status": "archived" if health == "empty" else "needs-routing",
                    "routing_note": "",
                    "source_path": rec["audio"]}
            # Preserve a prior routing decision across a --force rewrite: re-transcribing
            # refreshes the transcript but must NOT silently un-route an already-handled
            # memo (drop it back into the needs-routing queue with an empty note).
            mp = os.path.join(outdir, "meta.json")
            if os.path.exists(mp):
                try:
                    prev = json.load(open(mp))
                except Exception:
                    prev = {}
                if prev.get("status") in ("routed", "archived") and health != "empty":
                    meta["status"] = prev["status"]
                    meta["routing_note"] = prev.get("routing_note", "")
                for k in ("generated_title",):
                    if prev.get(k):
                        meta[k] = prev[k]
            json.dump(meta, open(mp, "w"), ensure_ascii=False, indent=2)
            # keep the labeled words so transcript.md can be re-rendered later (e.g.
            # a format change) WITHOUT re-running the slow whisper+pyannote pipeline.
            json.dump({"rec": {k: rec[k] for k in ("title", "date", "duration_s")},
                       "words": words}, open(os.path.join(outdir, "data.json"), "w"),
                      ensure_ascii=False)
            os.remove(wpath)
            if tpath and os.path.exists(tpath):
                os.remove(tpath)
            state[rec["id"]] = {"done": True, "date": rec["date"], "out": outdir}
            done += 1
        except Exception as e:
            log(f"    FAILED: {e}")
            state[rec["id"]] = {"done": False, "error": str(e)[:200]}
        save_state()  # incremental: a kill mid-run keeps finished items done

    # metadata stubs for ACTIVE cloud-only recordings (pending audio download; retried
    # once their audio appears locally). Evicted rows are excluded unless --include-evicted.
    for rec in pool:
        if rec["audio_local"] or state.get(rec["id"], {}).get("done"):
            continue
        state.setdefault(rec["id"], {})
        state[rec["id"]].update({"done": False, "reason": "audio not local (cloud-only, pending download)",
                                 "title": rec["title"], "date": rec["date"]})

    json.dump(state, open(STATE_PATH, "w"), ensure_ascii=False, indent=2)
    log(f"voicememos: done. processed {done}, "
        f"{sum(1 for r in recs if not r['audio_local'])} pending audio download.")
    print(f"Done. {done} transcribed → {OUT}")


if __name__ == "__main__":
    main()
