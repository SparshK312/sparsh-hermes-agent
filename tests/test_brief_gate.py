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

import pytest
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
    # 2026-10-08: hard_deadlines is now today_actions' open rows ("- [TODAY] <when> — <what>"),
    # finished rows already removed upstream. The fallback shows the What, today's first,
    # keeps an open ⬜ step, skips STALE rows, and never shows a non-row line.
    g = _load()
    deadlines = ("# header line\n"
                 "- [WEEK] 🟡 **Later** — **Week thing**\n"
                 "- [TODAY] 🔴 **TODAY** — **Ship it**\n"
                 "- [TODAY] ✅ **TEST SUBMITTED** · ⬜ **VOICE ROUND still to do** — **Widgetco OAs**\n"
                 "- [WEEK] 🔴 **TODAY Tue Oct 6** — **Old row** ⚠️ STALE (labelled TODAY Oct 6)")
    out = g.compose_templated(_facts(deadlines=deadlines))
    assert "Ship it" in out and "VOICE ROUND still to do" in out
    assert out.index("Ship it") < out.index("Week thing")
    assert "Old row" not in out and "header line" not in out


def test_templated_says_when_task_list_unreadable():
    g = _load()
    out = g.compose_templated(_facts(deadlines="UNKNOWN — Action Items.md could not be read"))
    assert "couldn't read your task list" in out


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


# ---- open loops in the brief + shared topics with the check-in loop (2026-11-16) ----
def _assistant(g, tmp_path, monkeypatch):
    import sys
    monkeypatch.setenv("HERMES_ASSISTANT_DIR", str(tmp_path / "assistant"))
    monkeypatch.setenv("HERMES_VAULT", str(tmp_path / "vault"))
    for n in ("loops", "outbox"):
        sys.modules.pop(n, None)
    return g._assistant_module("loops"), g._assistant_module("outbox")


def test_brief_gets_only_the_loops_due_today_and_survives_a_broken_store(tmp_path, monkeypatch):
    g = _load()
    L, O = _assistant(g, tmp_path, monkeypatch)
    now = datetime.datetime(2026, 11, 18, 8, 30, tzinfo=g.TZ)
    d = L.load()
    L.add(d, title="Acme — tax form", kind="waiting_on", who="Dana", next_step="escalate to Lee",
          due="2026-11-18", at=now - datetime.timedelta(days=2))
    L.add(d, title="Globex — offer letter", kind="waiting_on", who="Sam", check_after="2026-11-25",
          at=now - datetime.timedelta(days=2))
    L.save(d)
    got = g.gather_open_loops(now)
    assert [(x["title"], x["who"], x["next_step"]) for x in got] == [("Acme — tax form", "Dana", "escalate to Lee")]
    monkeypatch.setattr(g, "BRIEF_INCLUDES_LOOPS", False)
    assert g.gather_open_loops(now) == []                     # the off switch, with a healthy store
    monkeypatch.setattr(g, "BRIEF_INCLUDES_LOOPS", True)
    (tmp_path / "assistant" / "loops.json").write_text("{broken")
    assert g.gather_open_loops(now) == []                     # a broken store: the brief still goes out


def test_brief_claims_the_loop_topics_it_mentions_so_the_tick_stays_quiet(tmp_path, monkeypatch):
    g = _load()
    L, O = _assistant(g, tmp_path, monkeypatch)
    loops_ = [{"id": "acme-1", "title": "Acme — tax form", "who": "Dana"},
              {"id": "globex-2", "title": "Globex — offer letter", "who": "Sam"},
              {"id": "transfer-3", "title": "Brokerage — transfer 2", "who": ""}]
    brief = "Morning. The form from Dana is due for a look today; the plan is to escalate to Lee. The brokerage transfer is tomorrow."
    assert g.loops_mentioned(brief, loops_) == ["loop:acme-1", "loop:transfer-3"]
    assert g.loops_mentioned("Quiet day.", loops_) == [] and g.loops_mentioned(brief, None) == []
    g._record_sent("morning-brief", brief, g.loops_mentioned(brief, loops_))
    at = O.now().replace(hour=14, minute=0)
    assert not O.allow("nudge", "loop:acme-1", at=at)[0]      # already raised by the brief today
    assert O.allow("nudge", "loop:globex-2", at=at)[0]        # not mentioned -> the tick may raise it


def test_a_sent_brief_is_recorded_with_its_topics(tmp_path, monkeypatch):
    g = _load()
    L, O = _assistant(g, tmp_path, monkeypatch)
    _arm(g, tmp_path, monkeypatch)
    facts = _facts()
    facts["open_loops"] = [{"id": "acme-1", "title": "Acme — tax form", "who": "Dana", "next_step": "", "due": ""}]
    monkeypatch.setattr(g, "gather_facts", lambda t, y: facts)
    monkeypatch.setattr(g, "compose_templated", lambda f: "Morning. Dana's form is due for a look.")
    monkeypatch.setattr(g, "send_message", lambda t: True)
    monkeypatch.setattr(g, "send_morning_card", lambda *a, **k: None)
    monkeypatch.setattr(g, "WEEKDAY_CUTOFF", datetime.time(0, 0)); monkeypatch.setattr(g, "WEEKEND_CUTOFF", datetime.time(0, 0))
    assert g.main() == 0
    row = O.read()[-1]
    assert (row["kind"], row["topic"], row.get("topics")) == ("digest", "morning-brief", ["loop:acme-1"])


# ---- the brief's claims are checked before it is sent (2026-11-16) ----
FACTS = {"schedule": ["11:30 AM — Interview with Globex"], "sleep": {"sleep_total_h": 6.68},
         "hard_deadlines": "- [TODAY] By Wed Nov 18 — Acme form: Dana checking with the tax team (her reply Nov 16, 10:40 AM)"}
GOOD = "Morning. You slept 6h 41m. The Globex interview is at 11:30 AM."
BAD = "Dana's replying this morning with the forms, so that's arriving shortly."


def test_unsupported_sentences_are_removed_by_exact_quote_only(tmp_path, monkeypatch):
    g = _load()
    monkeypatch.setattr(g, "_log", lambda m: None)
    text = f"{GOOD} {BAD} Dana is checking with the tax team."
    out = g.verify_brief(text, FACTS, call=lambda s, u: json.dumps(
        {"unsupported": [{"sentence": BAD, "why": "a prediction; the facts say she already replied"},
                         {"sentence": "You slept about six hours", "why": "paraphrase, not a quote"}]}))
    assert out == f"{GOOD} Dana is checking with the tax team."
    assert g.verify_brief(GOOD, FACTS, call=lambda s, u: '{"unsupported": []}') == GOOD


def test_an_invented_name_marks_its_sentence_without_any_model(tmp_path, monkeypatch):
    g = _load()
    monkeypatch.setattr(g, "_log", lambda m: None)
    text = f"{GOOD} Also ping Priya about the offer letter today."
    assert [s for s, _ in g.ungrounded_name_sentences(text, json.dumps(FACTS))] == ["Also ping Priya about the offer letter today."]
    assert g.ungrounded_name_sentences(GOOD, json.dumps(FACTS)) == []
    assert g.verify_brief(text, FACTS, call=lambda s, u: '{"unsupported": []}') == GOOD


def test_too_much_unsupported_means_the_templated_brief(tmp_path, monkeypatch):
    g = _load()
    monkeypatch.setattr(g, "_log", lambda m: None)
    sents = [f"Invented claim number {i} about something else." for i in range(5)]
    reply = json.dumps({"unsupported": [{"sentence": s, "why": "x"} for s in sents]})
    assert g.verify_brief(" ".join(sents) + " " + GOOD, FACTS, call=lambda s, u: reply) is None


def test_a_failing_checker_never_costs_him_the_brief(tmp_path, monkeypatch):
    g = _load()
    logs = []
    monkeypatch.setattr(g, "_log", logs.append)

    def down(s, u):
        raise OSError("api down")
    assert g.verify_brief(GOOD, FACTS, call=down) == GOOD
    assert any("NOT verified" in m for m in logs)
    assert g.verify_brief(GOOD, FACTS, call=lambda s, u: "no json here") == GOOD
    assert g.verify_brief(GOOD, FACTS, call=lambda s, u: None) == GOOD
    monkeypatch.setattr(g, "BRIEF_VERIFY", False)
    assert g.verify_brief(BAD, FACTS, call=lambda s, u: pytest.fail("checker ran while switched off")) == BAD
