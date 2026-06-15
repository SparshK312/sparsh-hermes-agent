"""Behavior tests for the morning-brief gate — the daily-firing, user-facing,
hardest-to-debug-in-prod script. These pin the load-bearing invariants:

  * fire-once: once today's brief is marked, the next tick is silent (no resend)
  * mark-ONLY-on-confirmed-send: a delivery failure must NOT mark the day done,
    so the brief re-fires next tick instead of vanishing
  * compose_templated always produces a non-empty brief (the offline fallback)
  * the section parser matches headers exactly (no '## Schedule' capturing
    '## Scheduled Maintenance')

All of this runs offline — no Hermes, no network, no LLM (NO_LLM / monkeypatched
send). The script reads HERMES_VAULT + redirectable module globals, which makes
it trivially testable; before this it was covered only by a py_compile check.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
from pathlib import Path

HAE_DIR = Path(__file__).resolve().parent.parent / "scripts" / "hae"


def _load():
    spec = importlib.util.spec_from_file_location(
        "health_morning_brief_gate", HAE_DIR / "health_morning_brief_gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _facts(sleep_h=7.2, synced=True, schedule=None, tasks=None, deadlines=""):
    return {
        "date": "2026-06-10",
        "sleep_synced": synced,
        "sleep": {"sleep_total_h": sleep_h} if synced else {},
        "yesterday_activity": {},
        "schedule": schedule or [],
        "tasks": tasks or [],
        "hard_deadlines": deadlines,
        "this_week": "",
    }


# ---- compose_templated (the always-works fallback) ----

def test_templated_flags_low_sleep():
    g = _load()
    out = g.compose_templated(_facts(sleep_h=5.5))
    assert "⚠️" in out and "5.5h" in out
    assert "7h floor" in out


def test_templated_marks_good_sleep_ok():
    g = _load()
    out = g.compose_templated(_facts(sleep_h=8.0))
    assert "✅" in out and "⚠️" not in out.split("active")[0]


def test_templated_excludes_completed_deadlines():
    g = _load()
    deadlines = "- **Ship it** due Fri\n- ✅ **Done thing** done\n- ~~**Cancelled**~~"
    out = g.compose_templated(_facts(deadlines=deadlines))
    assert "Ship it" in out
    assert "Done thing" not in out and "Cancelled" not in out


def test_templated_never_empty_even_with_no_facts():
    g = _load()
    out = g.compose_templated(_facts(synced=False))
    assert out.strip()
    assert "hasn't synced" in out


# ---- section parser: exact header match ----

def test_section_no_false_prefix_capture():
    g = _load()
    text = "## Schedule\n- 9am standup\n## Scheduled Maintenance\n- not this\n"
    sec = g._section(text, "## schedule")
    assert "9am standup" in sec
    assert "not this" not in sec


# ---- main(): fire-once + mark-only-on-confirmed-send ----

def _arm(g, tmp_path, monkeypatch, sleep_present=True):
    """Put the gate into a deterministic, offline, firing state."""
    monkeypatch.setattr(g, "DRY_RUN", False)
    monkeypatch.setattr(g, "FORCE", False)
    monkeypatch.setattr(g, "NO_LLM", True)               # skip the OpenAI call
    monkeypatch.setattr(g, "_refresh", lambda: None)     # no subprocess pipeline
    monkeypatch.setattr(g, "STATE", tmp_path / "brief_state.json")
    monkeypatch.setattr(g, "_row_for",
                        lambda d: {"sleep_total_h": "7.2"} if sleep_present else {})
    monkeypatch.setattr(g, "gather_facts", lambda t, y: _facts())


def test_main_fire_once_is_silent(tmp_path, capsys, monkeypatch):
    g = _load()
    _arm(g, tmp_path, monkeypatch)
    today = datetime.datetime.now(g.TZ).strftime("%Y-%m-%d")
    # a complete brief (had sleep) already went out today
    g.STATE.write_text(json.dumps({"last_brief_date": today, "brief_had_sleep": True}))
    sent = []
    monkeypatch.setattr(g, "send_message", lambda t: sent.append(t) or True)

    rc = g.main()
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == g.WAKE_GATE_SKIP   # skips the agent, sends nothing
    assert sent == []                # already done today → no resend


def test_main_does_not_mark_when_send_fails(tmp_path, capsys, monkeypatch):
    g = _load()
    _arm(g, tmp_path, monkeypatch)
    monkeypatch.setattr(g, "send_message", lambda t: False)   # delivery fails

    g.main()
    out = capsys.readouterr().out.strip()
    # On failure it prints the brief (so Hermes can deliver as fallback) — NOT the gate
    assert out and out != g.WAKE_GATE_SKIP
    # and it must NOT have marked the day done, so it re-fires next tick
    state = json.loads(g.STATE.read_text()) if g.STATE.exists() else {}
    assert "last_brief_date" not in state


def test_main_marks_only_after_confirmed_send(tmp_path, capsys, monkeypatch):
    g = _load()
    _arm(g, tmp_path, monkeypatch)
    monkeypatch.setattr(g, "send_message", lambda t: True)    # delivery confirmed
    today = datetime.datetime.now(g.TZ).strftime("%Y-%m-%d")

    rc = g.main()
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == g.WAKE_GATE_SKIP                            # sent itself → skip agent
    assert json.loads(g.STATE.read_text())["last_brief_date"] == today


def test_main_silent_when_waiting_for_sleep(tmp_path, capsys, monkeypatch):
    """Before the cutoff, with no sleep yet, it stays silent (waits) rather than
    firing a half-empty brief."""
    g = _load()
    _arm(g, tmp_path, monkeypatch, sleep_present=False)
    # Force a pre-cutoff weekday morning time so the cutoff branch holds.
    monkeypatch.setattr(g, "WEEKDAY_CUTOFF", datetime.time(23, 59))
    monkeypatch.setattr(g, "WEEKEND_CUTOFF", datetime.time(23, 59))
    sent = []
    monkeypatch.setattr(g, "send_message", lambda t: sent.append(t) or True)

    g.main()
    out = capsys.readouterr().out.strip()
    assert out == g.WAKE_GATE_SKIP
    assert sent == []


# ---- sleep follow-up: brief fired at the cutoff WITHOUT sleep; sleep lands later ----

def _facts_steps(sleep_h=6.4, synced=True, steps=8200):
    f = _facts(sleep_h=sleep_h, synced=synced)
    f["yesterday_activity"] = {"steps": steps} if steps is not None else {}
    return f


def test_followup_fires_once_when_sleep_lands_after_brief(tmp_path, capsys, monkeypatch):
    g = _load()
    _arm(g, tmp_path, monkeypatch, sleep_present=True)        # sleep is now in the archive
    monkeypatch.setattr(g, "gather_facts", lambda t, y: _facts_steps())
    today = datetime.datetime.now(g.TZ).strftime("%Y-%m-%d")
    # brief already went out today WITHOUT sleep
    g.STATE.write_text(json.dumps(
        {"last_brief_date": today, "brief_had_sleep": False, "brief_steps": 5000}))
    sent = []
    monkeypatch.setattr(g, "send_message", lambda t: sent.append(t) or True)

    rc = g.main()
    out = capsys.readouterr().out.strip()
    assert rc == 0 and out == g.WAKE_GATE_SKIP
    assert len(sent) == 1 and "Sleep synced" in sent[0] and "6.4h" in sent[0]
    assert "8,200" in sent[0]   # corrected step total (brief had 5,000)
    assert json.loads(g.STATE.read_text())["sleep_followup_date"] == today


def test_followup_not_repeated(tmp_path, capsys, monkeypatch):
    g = _load()
    _arm(g, tmp_path, monkeypatch, sleep_present=True)
    monkeypatch.setattr(g, "gather_facts", lambda t, y: _facts_steps())
    today = datetime.datetime.now(g.TZ).strftime("%Y-%m-%d")
    g.STATE.write_text(json.dumps({"last_brief_date": today, "brief_had_sleep": False,
                                   "brief_steps": 5000, "sleep_followup_date": today}))
    sent = []
    monkeypatch.setattr(g, "send_message", lambda t: sent.append(t) or True)

    g.main()
    assert capsys.readouterr().out.strip() == g.WAKE_GATE_SKIP
    assert sent == []   # already followed up today


def test_no_followup_when_brief_already_had_sleep(tmp_path, capsys, monkeypatch):
    g = _load()
    _arm(g, tmp_path, monkeypatch, sleep_present=True)
    monkeypatch.setattr(g, "gather_facts", lambda t, y: _facts_steps())
    today = datetime.datetime.now(g.TZ).strftime("%Y-%m-%d")
    g.STATE.write_text(json.dumps({"last_brief_date": today, "brief_had_sleep": True}))
    sent = []
    monkeypatch.setattr(g, "send_message", lambda t: sent.append(t) or True)

    g.main()
    assert capsys.readouterr().out.strip() == g.WAKE_GATE_SKIP
    assert sent == []   # brief already had sleep → nothing to follow up
