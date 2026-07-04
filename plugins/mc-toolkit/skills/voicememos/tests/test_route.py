import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import route

def test_safe_slug_basic():
    assert route.safe_slug("Adam o projekt!") == "adam-o-projekt"

def test_safe_slug_keeps_polish():
    assert route.safe_slug("Rozmowa z Olgą") == "rozmowa-z-olgą"

def test_write_disposition(tmp_path):
    d = tmp_path / "2026-07-03-adam"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"title": "Adam", "status": "needs-routing"}))
    route.write_disposition(str(d), "routed", "3 taski → Todoist; podsumowanie → work/")
    m = json.loads((d / "meta.json").read_text())
    assert m["status"] == "routed"
    assert m["routing_note"] == "3 taski → Todoist; podsumowanie → work/"
    assert m["title"] == "Adam"  # untouched

def test_rename_memo_returns_new_path(tmp_path):
    d = tmp_path / "2026-07-03-old-slug"
    d.mkdir()
    (d / "meta.json").write_text("{}")
    new = route.rename_memo(str(d), "adam-o-projekt")
    assert os.path.basename(new) == "2026-07-03-adam-o-projekt"
    assert os.path.isdir(new) and not os.path.isdir(str(d))

def test_rename_memo_collision_suffix(tmp_path):
    (tmp_path / "2026-07-03-adam").mkdir()
    d = tmp_path / "2026-07-03-old"
    d.mkdir()
    (d / "meta.json").write_text("{}")
    new = route.rename_memo(str(d), "adam")
    assert os.path.basename(new) == "2026-07-03-adam-2"
