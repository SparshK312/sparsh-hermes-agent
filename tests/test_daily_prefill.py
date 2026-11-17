"""daily_prefill.py — the script that replaced the daily-note-prefill agent cron (2026-11-16).

Each test pins a way the morning note could come out wrong. Mutation-tested."""
from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

VAULT_DIR = Path(__file__).resolve().parent.parent / "scripts" / "vault"
D = datetime.date(2026, 11, 16)

TEMPLATE = '''---
date: "{{date}}"
type: daily
weight: 
sleep_hours: 
sleep_quality: 
kcal: 
protein_g: 
carbs_g: 
fat_g: 
water_l: 
lifted: 
energy: 
mood: 
---

# {{date:dddd, MMMM D, YYYY}}

## Schedule
| Time | Activity |
|------|----------|
|      |          |

## Tasks
- [ ]

## Health
> Fill the frontmatter numbers above.
- **Workout:** 

## Notes


## End of Day Reflection
**What went well?**

**What could improve?**
'''

STUB = '''---
type: daily
date: 2026-11-16
---

## Claude sessions

- **09:07** `claude` in `~/vault` — did things
'''

EVENTS = [
    {"summary": "Dentist | Dr. Lee", "start": "2026-11-16T11:30:00-05:00", "status": "confirmed"},
    {"summary": "Renew passport", "start": "2026-11-16T15:15:00+00:00", "status": "confirmed"},   # = 10:15 AM Eastern
    {"summary": "Reading week", "start": "2026-11-16", "status": "confirmed"},
    {"summary": "Dropped", "start": "2026-11-16T09:00:00-05:00", "status": "cancelled"},
]


@pytest.fixture
def P(tmp_path, monkeypatch):
    sys.path.insert(0, str(VAULT_DIR))
    spec = importlib.util.spec_from_file_location("daily_prefill", VAULT_DIR / "daily_prefill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.VAULT = tmp_path
    mod.DAILY_DIR = tmp_path / "04 - Daily Notes"
    mod.TEMPLATE = tmp_path / "Templates" / "Daily Note.md"
    mod.ACTION_ITEMS = tmp_path / "00 - Dashboard" / "Action Items.md"
    mod.TRIAGE_MD = tmp_path / "email_triage.md"
    for p in (mod.DAILY_DIR, mod.TEMPLATE.parent, mod.ACTION_ITEMS.parent):
        p.mkdir(parents=True)
    mod.TEMPLATE.write_text(TEMPLATE)
    mod.ACTION_ITEMS.write_text(
        "### 🔥 THIS WEEK\n\n| When | What |\n|---|---|\n"
        "| **Mon Nov 16** | 🧾 **Acme tax form**: long story about the wrong form ⬜ fill the right form when Dana replies |\n"
        "| **Mon Nov 16** | 💼 **Quarterly review: self-review questions due** and a lot more prose here |\n"
        "| **Wed Nov 18** | 🏦 **Brokerage transfer 2** |\n")
    monkeypatch.setattr(mod, "fetch_events", lambda d: EVENTS)
    return mod


def _fm(text):
    return yaml.safe_load(text.split("---", 2)[1])


def _note(P):
    return (P.DAILY_DIR / "2026-11-16.md").read_text()


def test_new_note_from_template_with_schedule_and_tasks(P):
    assert P.main(["--date", "2026-11-16", "--no-llm"]) == 0
    t = _note(P)
    assert _fm(t)["date"] == "2026-11-16" and "kcal" in _fm(t)
    assert "# Monday, November 16, 2026" in t and "{{" not in t
    sched = P._section(t, "Schedule")
    rows = [ln for ln in sched.split("\n") if ln.startswith("|")][2:]
    # all-day first, then by Eastern clock time; cancelled dropped; the pipe in a title neutralised
    assert rows == ["| All day | Reading week |", "| 10:15 AM | Renew passport |", "| 11:30 AM | Dentist / Dr. Lee |"]
    tasks = [ln for ln in P._section(t, "Tasks").split("\n") if ln.strip()]
    assert len(tasks) == 2
    assert tasks[0] == "- [ ] fill the right form when Dana replies"      # the ⬜ step is the task
    assert "Brokerage" not in P._section(t, "Tasks")                         # a [WEEK] row is never a task
    assert "- [ ]\n" not in P._section(t, "Tasks")                      # the empty bullet is replaced


def test_a_day_already_underway_is_never_touched(P):
    note = P.DAILY_DIR / "2026-11-16.md"
    live = P.from_template(TEMPLATE, D).replace("## Notes\n", "## Notes\nmet Sam for coffee\n")
    note.write_text(live)
    assert P.classify(live) == "populated"
    P.main(["--date", "2026-11-16", "--no-llm"])
    assert note.read_text() == live
    for edit in (("|      |          |", "| 9:00 AM | standup |"), ("- [ ]\n", "- [ ] call mom\n"),
                 ("**What went well?**\n", "**What went well?**\nshipped it\n")):
        assert P.classify(P.from_template(TEMPLATE, D).replace(*edit)) == "populated", edit


def test_stub_is_merged_not_overwritten_and_gets_the_health_keys(P):
    note = P.DAILY_DIR / "2026-11-16.md"
    note.write_text(STUB)
    P.main(["--date", "2026-11-16", "--no-llm"])
    t = note.read_text()
    assert "## Claude sessions" in t and "did things" in t              # nothing lost
    fm = _fm(t)
    for k in P.HEALTH_KEYS:                                              # vault_log needs these lines
        assert k in fm, k
    assert t.count("# Monday, November 16, 2026") == 1
    for name in P.SECTIONS:
        assert t.count(f"## {name}\n") == 1, name
    assert "| 11:30 AM | Dentist / Dr. Lee |" in t
    assert P.classify(t) == "populated"


def test_wearable_frontmatter_written_earlier_survives(P):
    note = P.DAILY_DIR / "2026-11-16.md"
    note.write_text(P.from_template(TEMPLATE, D).replace("sleep_hours: \n", "sleep_hours: 6.68\n")
                    .replace("mood: \n", "mood: \nresting_hr: 59\n"))
    P.main(["--date", "2026-11-16", "--no-llm"])
    fm = _fm(note.read_text())
    assert fm["sleep_hours"] == 6.68 and fm["resting_hr"] == 59


def test_calendar_window_uses_the_real_offset(P):
    assert P.day_window(datetime.date(2026, 11, 16)) == ("2026-11-16T00:00:00-05:00", "2026-11-16T23:59:59-05:00")
    assert P.day_window(datetime.date(2026, 7, 1))[0] == "2026-07-01T00:00:00-04:00"   # the old prompt typed -04:00 year-round


def test_unreadable_sources_become_visible_tasks_not_silence(P, monkeypatch):
    monkeypatch.setattr(P, "fetch_events", lambda d: None)
    P.ACTION_ITEMS.unlink()
    P.main(["--date", "2026-11-16", "--no-llm"])
    tasks = P._section(_note(P), "Tasks")
    assert "Could not read the task list" in tasks and "Could not read the calendar" in tasks


def test_inbox_is_pasted_verbatim_once_and_only_open_actions_become_tasks(P):
    inbox = ("## 📬 Inbox\n\n- **Needs a reply — Acme** Jo asked for times.\n    - ▶️ Reply to Jo with times\n"
             "- **Time-sensitive — Acme** The form link expires today.\n    - ✅ already handled: skipped\n    - ▶️ Fill in the form\n")
    P.TRIAGE_MD.write_text(inbox)
    P.main(["--date", "2026-11-16", "--no-llm"])
    t = _note(P)
    assert inbox.strip() in t and t.count("## 📬 Inbox") == 1
    assert t.index("## 📬 Inbox") < t.index("## Health")
    tasks = P._section(t, "Tasks")
    assert "- [ ] Reply to Jo with times" in tasks
    assert "Fill in the form" not in tasks                                     # handled items are not tasks
    assert P.insert_inbox(t, inbox) == t                                 # never inserted twice
    P.TRIAGE_MD.write_text("## 📬 Inbox\n\nNothing needing attention.\n")
    assert P.read_inbox() == ""


def test_model_reply_of_the_wrong_shape_never_drops_a_task(P):
    rows = ["🧾 **Acme tax form** ⬜ fill the right form", "💼 **Quarterly review due**"]
    assert P.condense(rows, call=lambda s, u: '["Fill the right form", "Finish the quarterly self-review"]') == \
        ["Fill the right form", "Finish the quarterly self-review"]
    rule = [P.shorten_by_rule(r) for r in rows]
    for bad in ('["only one"]', "not json", None, '["a", ""]', '{"a": 1}', '["a", 2]'):
        assert P.condense(rows, call=lambda s, u, bad=bad: bad) == rule, bad


def test_api_text_is_read_by_block_type_not_position(P, monkeypatch):
    import io, json
    payload = {"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": "hmm"},
                                                      {"type": "text", "text": ' ["x"] '}]}
    monkeypatch.setattr(P, "_env", lambda k: "key")
    monkeypatch.setattr(P.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    assert P.anthropic_text("s", "u") == '["x"]'


def test_a_write_landing_mid_run_is_not_lost(P, monkeypatch):
    note = P.DAILY_DIR / "2026-11-16.md"
    note.write_text(P.from_template(TEMPLATE, D))
    real = P.build_note
    fired = []

    def racing(current, *a, **k):
        if not fired:                                   # the wearable sync writes while we compose
            fired.append(1)
            note.write_text(current.replace("sleep_hours: \n", "sleep_hours: 7.1\n"))
        return real(current, *a, **k)
    monkeypatch.setattr(P, "build_note", racing)
    P.main(["--date", "2026-11-16", "--no-llm"])
    t = note.read_text()
    assert _fm(t)["sleep_hours"] == 7.1 and "| 11:30 AM |" in t
