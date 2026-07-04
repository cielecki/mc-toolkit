import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import overview

def _memo(root, name, meta):
    d = os.path.join(root, name); os.makedirs(d)
    json.dump(meta, open(os.path.join(d, "meta.json"), "w"))

def test_scan_reads_memo_folders(tmp_path):
    _memo(str(tmp_path), "2026-07-03-adam",
          {"generated_title": "Adam", "transcript_health": "healthy",
           "status": "needs-routing", "routing_note": ""})
    _memo(str(tmp_path), "snapshot", {})  # non-memo dirs without date prefix ignored
    rows = overview.scan(str(tmp_path))
    assert len(rows) == 1
    assert rows[0]["status"] == "needs-routing"

def test_scan_skips_non_dated_dirs(tmp_path):
    os.makedirs(os.path.join(str(tmp_path), "voiceprints"))
    assert overview.scan(str(tmp_path)) == []

def test_format_table_has_header(tmp_path):
    out = overview.format_table([{"folder": "2026-07-03-adam", "title": "Adam",
                                  "health": "healthy", "status": "needs-routing", "note": ""}])
    assert "status" in out and "2026-07-03-adam" in out
