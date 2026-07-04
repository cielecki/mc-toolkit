# Voice Memos Routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn each synced voice memo from a raw transcript into a decided, routed artifact — with a transcript-generated folder name, a quality verdict, engine escalation for failed transcriptions, and a text-file-driven routing table that says what happened and where.

**Architecture:** `sync.py` stays deterministic Python (transcribe + persist quality signals + mark `needs-routing`). The LLM-driven phases (auto-title, routing, escalation decisions) run **in-session** after sync, orchestrated by SKILL.md against a plain-text rules file — so v1 asks before every action. Small Python helpers (`quality.py`, `route.py`, `overview.py`, a rename helper) carry the deterministic mechanics the in-session flow calls.

**Tech Stack:** Python 3 (pyenv `python3` for sync/helpers; mlx env `/opt/homebrew/bin/python3.14` for transcribe; venv `~/.venvs/diarization/bin/python` for identify), mlx-whisper + silero-VAD, Sortformer, cloud STT scripts (`openai.py` etc.), pytest (introduced here for the pure helpers), Markdown rules file.

## Global Constraints

- **Never commit personal/biometric data.** Only skill code + `references/` are version-controlled. `<data-dir>/` (snapshot, per-memo folders, voiceprints), the venv, and the FDA app stay out of git. The routing-rules file lives in the data dir precisely so private paths never enter the plugin repo.
- **Two Python envs, kept separate.** `transcribe.py` + `sortformer_diarize.py` → mlx env `/opt/homebrew/bin/python3.14`; `identify.py` → venv `~/.venvs/diarization/bin/python`; `sync.py` + pure helpers (`quality.py`, `route.py`, `overview.py`) → pyenv `python3` (no heavy deps, so pytest runs on them directly).
- **Content, not name.** Every LLM decision (title, classification, routing) reads the full `transcript.md`, never the folder name.
- **Distributed state only.** All memo state lives in that memo's `meta.json`. No shared index/ledger file — any overview is generated on-demand by scanning folders.
- **Local-first, escalate deliberately.** Local whisper always runs first. Cloud escalation is a second step decided from the local transcript.
- **Privacy is per-engine + sensitivity-gated.** Sensitive content (health/therapy/intimacy/finance/family) never escalates past OpenAI. v1 always asks before any off-device send.
- **Ask-vs-auto lives in the rules text.** v1: every rule says `ZAPYTAJ` except obvious-empty archive. Graduation = editing a word in the rules file. No separate `trust` field.
- **Data-dir path** resolves via `_config.cfg("VOICEMEMOS_DATA", "~/voicememos", expand=True)`. Tests must not hardcode it — use `tmp_path`.

---

## File Structure

- `plugins/mc-toolkit/stt/engines/local.py` — MODIFY: return `speech_seconds` (extract a pure `speech_seconds_from_ranges` helper).
- `plugins/mc-toolkit/skills/voicememos/scripts/quality.py` — CREATE: pure health classifier (`classify_health`, `mean_confidence`, `is_repetition_loop`).
- `plugins/mc-toolkit/skills/voicememos/scripts/route.py` — CREATE: read/write `routing_note` + `status` into a memo's `meta.json`; slug + collision-safe folder rename.
- `plugins/mc-toolkit/skills/voicememos/scripts/overview.py` — CREATE: scan all memo folders → table (on-demand, no stored state).
- `plugins/mc-toolkit/skills/voicememos/scripts/sync.py` — MODIFY: persist quality signals + `original_title` + `status: needs-routing` into `meta.json`.
- `plugins/mc-toolkit/skills/voicememos/scripts/escalate.py` — CREATE: re-transcribe one memo via a chosen cloud engine + re-render.
- `plugins/mc-toolkit/skills/voicememos/tests/` — CREATE: `test_quality.py`, `test_route.py`, `test_overview.py` (pytest, pure-function coverage).
- `plugins/mc-toolkit/skills/voicememos/references/routing.md` — CREATE: process doc (how routing works, escalation ladder, per-engine privacy). Committed.
- `plugins/mc-toolkit/skills/voicememos/SKILL.md` — MODIFY: document the in-session Title → Quality → Route → Escalate flow + backlog procedure.
- `<data-dir>/routing-rules.md` — CREATE (NOT committed): seed criterion→action rules.

Naming contract shared across tasks (so tasks read in any order agree):
- `quality.classify_health(speech_seconds: float, word_count: int, duration_s: float, mean_conf: float|None, is_loop: bool) -> str` → `"healthy" | "empty" | "suspect"`.
- `quality.mean_confidence(words: list[dict]) -> float|None`.
- `quality.is_repetition_loop(words: list[dict]) -> bool`.
- `route.write_disposition(memo_dir: str, status: str, note: str) -> None`.
- `route.rename_memo(memo_dir: str, new_slug: str) -> str` (returns new path).
- `route.safe_slug(title: str, maxlen: int = 48) -> str`.
- `overview.scan(data_dir: str) -> list[dict]` and `overview.format_table(rows: list[dict]) -> str`.
- `local.speech_seconds_from_ranges(ranges: list[dict], sr: int = 16000) -> float`.

---

## Phase 1 — Quality signals + gate

### Task 1.1: `speech_seconds` from VAD in the shared engine

**Files:**
- Modify: `plugins/mc-toolkit/stt/engines/local.py:42-124`
- Test: `plugins/mc-toolkit/skills/voicememos/tests/test_quality.py` (this task adds one test for the pure helper; it lives with the other pure-fn tests)

**Interfaces:**
- Produces: `local.speech_seconds_from_ranges(ranges, sr=16000) -> float`; `local.transcribe()` return dict gains key `"speech_seconds": float`.

- [ ] **Step 1: Write the failing test**

Create `plugins/mc-toolkit/skills/voicememos/tests/test_speech_seconds.py`:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "stt", "engines"))
import local

def test_speech_seconds_sums_ranges_over_sr():
    # two ranges: 16000 samples (1.0s) + 8000 samples (0.5s) at 16kHz → 1.5s
    ranges = [{"start": 0, "end": 16000}, {"start": 32000, "end": 40000}]
    assert local.speech_seconds_from_ranges(ranges) == 1.5

def test_speech_seconds_empty_is_zero():
    assert local.speech_seconds_from_ranges([]) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/mc-toolkit/skills/voicememos && /opt/homebrew/bin/python3.14 -m pytest tests/test_speech_seconds.py -v`
Expected: FAIL — `AttributeError: module 'local' has no attribute 'speech_seconds_from_ranges'`

- [ ] **Step 3: Add the helper and wire it into both return paths**

In `local.py`, add after `speech_ranges` (around line 49):
```python
def speech_seconds_from_ranges(ranges, sr=SR):
    """Total detected-speech duration in seconds (VAD ranges are in samples)."""
    return round(sum(r["end"] - r["start"] for r in ranges) / sr, 1)
```
In `transcribe()`, right after `ranges = speech_ranges(a)` (line 58):
```python
    speech_s = speech_seconds_from_ranges(ranges)
```
Change the empty-return (line 60) to include it:
```python
        return {"words": [], "text": "", "language": language if language != "auto" else "en",
                "speech_seconds": speech_s}
```
Change the final return (line 120-124) to include it:
```python
    return {
        "words": words,
        "text": " ".join(w["text"] for w in words),
        "language": language,
        "speech_seconds": speech_s,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/mc-toolkit/skills/voicememos && /opt/homebrew/bin/python3.14 -m pytest tests/test_speech_seconds.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add plugins/mc-toolkit/stt/engines/local.py plugins/mc-toolkit/skills/voicememos/tests/test_speech_seconds.py
git commit -m "feat(voicememos): return speech_seconds (VAD) from local engine"
```

### Task 1.2: `quality.py` health classifier (pure)

**Files:**
- Create: `plugins/mc-toolkit/skills/voicememos/scripts/quality.py`
- Test: `plugins/mc-toolkit/skills/voicememos/tests/test_quality.py`

**Interfaces:**
- Produces: `classify_health`, `mean_confidence`, `is_repetition_loop` (signatures in the naming contract above).

- [ ] **Step 1: Write the failing test**

Create `tests/test_quality.py`:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import quality

def w(text, conf=0.9):
    return {"text": text, "confidence": conf}

def test_no_speech_is_empty():
    assert quality.classify_health(1.0, 0, 300.0, None, False) == "empty"

def test_speech_but_no_words_is_suspect():
    assert quality.classify_health(120.0, 0, 300.0, None, False) == "suspect"

def test_normal_speech_is_healthy():
    # 200 words over 120s speech = 100 wpm, good confidence
    assert quality.classify_health(120.0, 200, 300.0, 0.85, False) == "healthy"

def test_speech_but_sparse_text_is_suspect():
    # 10 words over 120s speech = 5 wpm → STT barely caught anything
    assert quality.classify_health(120.0, 10, 300.0, 0.9, False) == "suspect"

def test_low_confidence_is_suspect():
    assert quality.classify_health(120.0, 200, 300.0, 0.30, False) == "suspect"

def test_repetition_loop_is_suspect():
    assert quality.classify_health(120.0, 200, 300.0, 0.85, True) == "suspect"

def test_mean_confidence_ignores_none():
    assert quality.mean_confidence([w("a", 0.8), w("b", None), w("c", 0.6)]) == 0.7

def test_mean_confidence_empty_is_none():
    assert quality.mean_confidence([]) is None

def test_repetition_loop_detects_dominant_trigram():
    words = [w(t) for t in (["ok", "ok", "ok"] * 20)]
    assert quality.is_repetition_loop(words) is True

def test_repetition_loop_false_on_varied_text():
    words = [w(t) for t in "the quick brown fox jumps over the lazy dog again today".split()]
    assert quality.is_repetition_loop(words) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/mc-toolkit/skills/voicememos && python3 -m pytest tests/test_quality.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'quality'`

- [ ] **Step 3: Write `quality.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/mc-toolkit/skills/voicememos && python3 -m pytest tests/test_quality.py -v`
Expected: PASS (10 passed). If pytest is missing: `python3 -m pip install --user pytest` then re-run.

- [ ] **Step 5: Commit**

```bash
git add plugins/mc-toolkit/skills/voicememos/scripts/quality.py plugins/mc-toolkit/skills/voicememos/tests/test_quality.py
git commit -m "feat(voicememos): deterministic transcript-health gate (empty/suspect/healthy)"
```

### Task 1.3: Persist quality signals + routing scaffold in `sync.py`

**Files:**
- Modify: `plugins/mc-toolkit/skills/voicememos/scripts/sync.py:221-226`

**Interfaces:**
- Consumes: `quality.classify_health/mean_confidence/is_repetition_loop`, `words_obj["speech_seconds"]`.
- Produces: each `meta.json` now carries `original_title`, `speech_seconds`, `transcript_health`, `engine`, `status`, `routing_note` (empty until routed).

- [ ] **Step 1: Add the import**

Near the top imports of `sync.py` (after line 37 `from _config import cfg`):
```python
import quality  # scripts/ is already on sys.path (line 35)
```

- [ ] **Step 2: Compute health + expand the meta dict**

Replace the `meta = {...}` block (lines 221-224) with:
```python
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
```
(Note: `status` starts as `archived` only when `empty`; `suspect`/`healthy` → `needs-routing` so the in-session router sees them. The router may still re-open an auto-archived empty in v1 — see SKILL.md.)

- [ ] **Step 3: Live-verify on one existing recording**

Run (re-process the newest local memo without a fresh snapshot):
```bash
cd plugins/mc-toolkit/skills/voicememos && python3 scripts/sync.py --no-snapshot --force --limit 1 > /tmp/vm-verify.log 2>&1; echo "---"; ls -dt "$(python3 -c "from scripts._config import cfg; print(cfg('VOICEMEMOS_DATA','~/voicememos',expand=True))")"/2026-*/ | head -1
```
Then Read that folder's `meta.json`.
Expected: it now contains `original_title`, `speech_seconds`, `transcript_health`, `engine`, `status: needs-routing` (or `archived`), `routing_note: ""`.

- [ ] **Step 4: Commit**

```bash
git add plugins/mc-toolkit/skills/voicememos/scripts/sync.py
git commit -m "feat(voicememos): persist quality signals + routing status into meta.json"
```

---

## Phase 2 — Auto-title

### Task 2.1: `route.py` — disposition writer, slug, collision-safe rename (pure)

**Files:**
- Create: `plugins/mc-toolkit/skills/voicememos/scripts/route.py`
- Test: `plugins/mc-toolkit/skills/voicememos/tests/test_route.py`

**Interfaces:**
- Produces: `safe_slug`, `rename_memo`, `write_disposition` (naming contract above).

- [ ] **Step 1: Write the failing test**

Create `tests/test_route.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/mc-toolkit/skills/voicememos && python3 -m pytest tests/test_route.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'route'`

- [ ] **Step 3: Write `route.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/mc-toolkit/skills/voicememos && python3 -m pytest tests/test_route.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add plugins/mc-toolkit/skills/voicememos/scripts/route.py plugins/mc-toolkit/skills/voicememos/tests/test_route.py
git commit -m "feat(voicememos): route.py — slug, collision-safe rename, disposition writer"
```

### Task 2.2: Auto-title step in SKILL.md (in-session, LLM)

**Files:**
- Modify: `plugins/mc-toolkit/skills/voicememos/SKILL.md` (add a "Post-sync routing flow" section)

**Interfaces:**
- Consumes: `route.safe_slug`, `route.rename_memo`; reads `transcript.md`, writes `meta.json` `title`/`generated_title`.

- [ ] **Step 1: Author the auto-title step**

Add to SKILL.md a new section (full text to insert):
```markdown
## Post-sync routing flow (in-session)

After `sync.py` finishes, the skill (this session) processes every memo whose
`meta.json` has `status: needs-routing`, one at a time. Never batch the decisions.

### Step A — Auto-title (content, not name)
For each memo, read the FULL `transcript.md` and produce a short descriptive
Polish title (≤ 6 words, names the topic/people, e.g. "Adam — projekt i inwestycje").
Then:
1. `python3 scripts/route.py`-backed rename: call `route.rename_memo(memo_dir, route.safe_slug(title))`.
2. Write both titles into the (possibly moved) `meta.json`: set `generated_title` = the new title,
   keep `original_title` as-is.
Skip renaming for `status: archived` (empty) memos — they keep their date-slug.
```

- [ ] **Step 2: Live-verify on the memo from Task 1.3**

In-session: read one `needs-routing` memo's `transcript.md`, generate a title, run:
```bash
cd plugins/mc-toolkit/skills/voicememos && python3 -c "import sys; sys.path.insert(0,'scripts'); import route; print(route.rename_memo('<memo_dir>', route.safe_slug('<title>')))"
```
Expected: folder renamed to `YYYY-MM-DD-<generated-slug>`; `meta.json` still present in the new path; `original_title` intact.

- [ ] **Step 3: Commit**

```bash
git add plugins/mc-toolkit/skills/voicememos/SKILL.md
git commit -m "docs(voicememos): auto-title step in post-sync flow"
```

---

## Phase 3 — Routing

### Task 3.1: `references/routing.md` — process doc (committed)

**Files:**
- Create: `plugins/mc-toolkit/skills/voicememos/references/routing.md`

- [ ] **Step 1: Write the process doc**

Create the file with: the pipeline order (transcribe → quality gate → escalation → auto-title → routing), the escalation ladder table (local → OpenAI → AssemblyAI → ElevenLabs) with the per-engine privacy profiles verbatim from `references/privacy-research.md`, the sensitivity gate (never past OpenAI for sensitive), and the rule-file format (`Kryterium:` / `Akcja:` with `ZAPYTAJ | NIE pytaj`). This is the generic, path-free companion to the private rules file. Source of truth for content: the design spec `docs/superpowers/specs/2026-07-04-voicememos-routing-design.md`.

- [ ] **Step 2: Commit**

```bash
git add plugins/mc-toolkit/skills/voicememos/references/routing.md
git commit -m "docs(voicememos): routing process + escalation/privacy reference"
```

### Task 3.2: Seed the private rules file (NOT committed)

**Files:**
- Create: `<data-dir>/routing-rules.md`

- [ ] **Step 1: Write the seed rules**

Resolve the data dir (`python3 -c "import sys;sys.path.insert(0,'plugins/mc-toolkit/skills/voicememos/scripts');from _config import cfg;print(cfg('VOICEMEMOS_DATA','~/voicememos',expand=True))"`) and create `routing-rules.md` there with the seed rules from the design spec (empty→archive/ZAPYTAJ, spółki/projekt→Todoist+work/ZAPYTAJ, zdrowie→wellbeing transkrypty/ZAPYTAJ, rodzina→personal/rodzina/ZAPYTAJ, idea-dump→inbox/ZAPYTAJ). Every rule says `ZAPYTAJ` in v1.

- [ ] **Step 2: Verify it is NOT tracked by git**

Run: `git status --porcelain "$(python3 -c "import sys;sys.path.insert(0,'plugins/mc-toolkit/skills/voicememos/scripts');from _config import cfg;print(cfg('VOICEMEMOS_DATA','~/voicememos',expand=True))")/routing-rules.md" 2>/dev/null | head`
Expected: empty output (the data dir is outside the repo / gitignored). No commit for this file.

### Task 3.3: Routing step in SKILL.md (in-session, LLM)

**Files:**
- Modify: `plugins/mc-toolkit/skills/voicememos/SKILL.md` (extend the "Post-sync routing flow" section)

**Interfaces:**
- Consumes: `route.write_disposition`; reads `transcript.md`, `<data-dir>/routing-rules.md`.

- [ ] **Step 1: Author the routing step**

Append to the "Post-sync routing flow" section:
```markdown
### Step B — Route (rules from the text file)
1. Load `<data-dir>/routing-rules.md` (the private criterion→action table).
2. For each `needs-routing` memo, read the FULL `transcript.md`, match it against the
   criteria, and pick the applicable action(s). If none match, propose a best-guess disposition.
3. If the matched action says `NIE pytaj` → execute it. If `ZAPYTAJ` (v1 default: all) →
   present the proposal (category read from content, the concrete action, target paths) and
   wait for approve / edit / execute.
4. Execute the action: simple inline (archive, create Todoist task, file a short note),
   or hand off to the domain skill for complex work (e.g. specjalista → intimacy flow,
   projekt → skill-korporacyjny). Record what you did.
5. Call `route.write_disposition(memo_dir, "routed", "<free-text: what was done and where>")`.
   The free text is the durable "co zrobiono i dokąd" — be specific (targets, task links).
```

- [ ] **Step 2: Live-verify end-to-end on one memo**

In-session: pick one `needs-routing` memo, run Step A then Step B interactively, approve a disposition, then Read its `meta.json`.
Expected: `status: routed`, `routing_note` describes the action + targets; the target artifact (task/note) actually exists.

- [ ] **Step 3: Commit**

```bash
git add plugins/mc-toolkit/skills/voicememos/SKILL.md
git commit -m "docs(voicememos): routing step (text-file rules → disposition)"
```

---

## Phase 4 — Engine escalation

### Task 4.1: `escalate.py` — re-transcribe one memo via a cloud engine

**Files:**
- Create: `plugins/mc-toolkit/skills/voicememos/scripts/escalate.py`

**Interfaces:**
- Consumes: `scripts/openai.py` (and siblings) CLI: `<audio> [--language] [--model] [--out FILE.md]`.
- Produces: CLI `escalate.py <memo_dir> --engine openai|assemblyai|elevenlabs` → rewrites `transcript.md` from the cloud engine, sets `meta.json` `engine` + `transcript_health: healthy`.

- [ ] **Step 1: Write `escalate.py`**

```python
#!/usr/bin/env python3
"""Escalate ONE memo's transcription to a cloud engine (privacy decision made upstream,
in SKILL.md). Local-first already ran in sync; this is the deliberate second step.

Usage: escalate.py <memo_dir> --engine openai|assemblyai|elevenlabs [--model M]
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_SCRIPT = {"openai": "openai.py", "assemblyai": "assemblyai.py", "elevenlabs": "elevenlabs.py"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("memo_dir")
    ap.add_argument("--engine", required=True, choices=list(ENGINE_SCRIPT))
    ap.add_argument("--model")
    args = ap.parse_args()

    audio = os.path.join(args.memo_dir, "audio.m4a")
    if not os.path.exists(audio):
        sys.exit(f"no audio.m4a in {args.memo_dir}")
    out_md = os.path.join(args.memo_dir, "transcript.md")
    cmd = ["python3", os.path.join(HERE, ENGINE_SCRIPT[args.engine]), audio, "--out", out_md]
    if args.model:
        cmd += ["--model", args.model]
    if subprocess.run(cmd).returncode != 0:
        sys.exit(f"{args.engine} transcription failed")

    p = os.path.join(args.memo_dir, "meta.json")
    meta = json.load(open(p)) if os.path.exists(p) else {}
    meta["engine"] = args.engine
    meta["transcript_health"] = "healthy"
    meta["status"] = "needs-routing"
    json.dump(meta, open(p, "w"), ensure_ascii=False, indent=2)
    print(f"escalated {args.memo_dir} via {args.engine} → transcript.md rewritten")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Live-verify on ONE non-sensitive suspect memo (costs an API call)**

Pick a `transcript_health: suspect`, non-sensitive memo (or force one). Run:
```bash
cd plugins/mc-toolkit/skills/voicememos && python3 scripts/escalate.py "<memo_dir>" --engine openai
```
Then Read `<memo_dir>/transcript.md` and `meta.json`.
Expected: transcript.md rewritten with real text; `engine: openai`, `transcript_health: healthy`, `status: needs-routing`.

- [ ] **Step 3: Commit**

```bash
git add plugins/mc-toolkit/skills/voicememos/scripts/escalate.py
git commit -m "feat(voicememos): escalate.py — cloud re-transcription of a suspect memo"
```

### Task 4.2: Escalation decision in SKILL.md (privacy gate, ask)

**Files:**
- Modify: `plugins/mc-toolkit/skills/voicememos/SKILL.md`

- [ ] **Step 1: Author the escalation step**

Add before Step A in the routing flow:
```markdown
### Step 0 — Quality gate + escalation
For each memo, read `meta.json` `transcript_health`:
- `healthy` → proceed to Step A.
- `empty` → already `status: archived`; confirm with the user before finalizing (v1 ZAPYTAJ)
  — this is where trust in the empty-detector is earned; only graduate to silent-auto later.
- `suspect` → judge SENSITIVITY from the (partial) local transcript. Pick the highest rung the
  sensitivity allows (sensitive → max OpenAI; non-sensitive quality-critical → up to ElevenLabs).
  ALWAYS ask before sending: "słaby transkrypt, temat wygląda na X, proponuję <engine>
  (<privacy one-liner>) — ok?". On approval run `escalate.py`, then re-run the quality gate;
  if still `suspect`, set `status: needs-attention` and surface it.
Never send sensitive audio (health/therapy/intimacy/finance/family) past OpenAI.
```

- [ ] **Step 2: Commit**

```bash
git add plugins/mc-toolkit/skills/voicememos/SKILL.md
git commit -m "docs(voicememos): quality-gate + engine-escalation step (privacy-gated)"
```

---

## Phase 5 — Backlog + on-demand overview

### Task 5.1: `overview.py` — scan folders → table (pure-ish)

**Files:**
- Create: `plugins/mc-toolkit/skills/voicememos/scripts/overview.py`
- Test: `plugins/mc-toolkit/skills/voicememos/tests/test_overview.py`

**Interfaces:**
- Produces: `scan(data_dir) -> list[dict]`, `format_table(rows) -> str`; CLI prints the table.

- [ ] **Step 1: Write the failing test**

Create `tests/test_overview.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/mc-toolkit/skills/voicememos && python3 -m pytest tests/test_overview.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'overview'`

- [ ] **Step 3: Write `overview.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/mc-toolkit/skills/voicememos && python3 -m pytest tests/test_overview.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add plugins/mc-toolkit/skills/voicememos/scripts/overview.py plugins/mc-toolkit/skills/voicememos/tests/test_overview.py
git commit -m "feat(voicememos): overview.py — on-demand memo status table"
```

### Task 5.2: Backlog backfill + bulk-run procedure in SKILL.md

**Files:**
- Modify: `plugins/mc-toolkit/skills/voicememos/SKILL.md`

**Interfaces:**
- Consumes: `quality`, `route`, `overview`, existing `data.json` (words) + `audio.m4a` per folder.

- [ ] **Step 1: Author the backfill + bulk procedure**

Add a "Backlog (existing folders)" subsection:
```markdown
## Backlog — draining pre-existing folders

Old folders predate the quality/routing fields. To fold them into the same pipeline:
1. **Backfill health:** for each folder missing `transcript_health`, load `data.json` words
   → `quality.mean_confidence` + `quality.is_repetition_loop`; for `speech_seconds`, re-run
   VAD only when `words` is empty (to split empty vs suspect) via
   `/opt/homebrew/bin/python3.14 -c "import sys;sys.path.insert(0,'../../stt/engines');import local;print(local.speech_seconds_from_ranges(local.speech_ranges(local.decode_pcm('<audio>'))))"`.
   Otherwise `speech_seconds` ≈ derivable-enough from words; classify and write via a meta merge.
   Set `status: needs-routing` (or `archived` if empty).
2. **Run the same in-session flow** (Step 0 → A → B) over the backfilled `needs-routing` set,
   in date order, with approval — this IS the backlog decision pass. No separate mechanism.
3. Use `python3 scripts/overview.py` to see what's left at any time.
```

- [ ] **Step 2: Live-verify the overview against the real backlog**

Run: `cd plugins/mc-toolkit/skills/voicememos && python3 scripts/overview.py | head -20`
Expected: a table listing the real memo folders with their `health`/`status`.

- [ ] **Step 3: Commit**

```bash
git add plugins/mc-toolkit/skills/voicememos/SKILL.md
git commit -m "docs(voicememos): backlog backfill + bulk-run procedure"
```

---

## Self-Review

**Spec coverage:**
- Content-not-name classification → Tasks 2.2, 3.3 (read full transcript.md). ✓
- Distributed state, no shared file → all writes to per-folder meta.json; overview is generated (5.1), never stored. ✓
- Trust trajectory (ask→auto in text) → rules file all `ZAPYTAJ` (3.2); graduation = edit rule; no `trust` field. ✓
- Policy as plain text → 3.1 (process) + 3.2 (rules). ✓
- Local-first + deliberate escalation → escalation only for `suspect`, after local (4.1/4.2). ✓
- Per-engine privacy + sensitivity gate → 3.1 doc + 4.2 step. ✓
- Quality gate empty/suspect/healthy from VAD → 1.1 (speech_seconds) + 1.2 (classify). ✓
- Auto-title in folder name + original kept → 2.1 (rename) + 2.2 (generate). ✓
- meta.json additions → 1.3 (+ generated_title in 2.2, engine flip in 4.1). ✓
- Backlog as first batch through same pipeline → 5.2. ✓
- On-demand overview → 5.1. ✓

**Placeholder scan:** SKILL.md tasks quote the exact prose to insert; Python tasks show full code + tests; escalation/routing targets are concrete. `references/routing.md` (3.1) and the seed rules (3.2) describe content sourced verbatim from the committed spec rather than inlining the whole doc — acceptable (the spec is the single source and is in-repo).

**Type consistency:** `speech_seconds` (float) flows local.py → transcribe.py JSON → sync.py meta. `classify_health(speech_seconds, word_count, duration_s, mean_conf, is_loop)` signature identical in 1.2 test, 1.2 impl, 1.3 call. `route.write_disposition/rename_memo/safe_slug` and `overview.scan/format_table` match across tasks. `status` vocabulary (`needs-routing|routed|archived|needs-attention`) consistent with the spec's corrected model.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-04-voicememos-routing.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
