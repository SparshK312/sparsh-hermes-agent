"""scripts/assistant/checkin.py + ground.py — the hourly check-in and the code that
verifies its wording (2026-11-16). Invented names throughout. Mutation-tested."""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

A = Path(__file__).resolve().parent.parent / "scripts" / "assistant"
TZ = ZoneInfo("America/New_York")


def T(h=12, m=0, day=18):          # Wed Nov 18 2026 by default
    return datetime.datetime(2026, 11, day, h, m, tzinfo=TZ)


def _fresh(name):
    spec = importlib.util.spec_from_file_location(name, A / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def C(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ASSISTANT_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HERMES_VAULT", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir()
    sys.path.insert(0, str(A))
    for n in ("outbox", "loops", "llm", "ground", "checkin"):
        sys.modules.pop(n, None)
    mod = _fresh("checkin")
    mod.tmp = tmp_path
    mod.outbox.record("digest", "morning-brief", "Morning.", at=T(8, 30))      # the day is open
    return mod


def engine(tmp_path, meals=(), fm=None, workouts=(), planned=3):
    w = tmp_path / "workouts"
    w.mkdir(exist_ok=True)
    for d in workouts:
        (w / f"{d}.md").write_text("x")
    return SimpleNamespace(meals_logged=lambda d: set(meals), daily_fm=lambda d: dict(fm or {}),
                           planned_sessions_per_week=lambda: planned, WORKOUTS=w)


def no_model(*a):
    raise AssertionError("a model call was made")


def no_mail(*a):
    raise AssertionError("a mail search was made")


def add_loop(C, title="Acme tax form", who="Dana", kind="waiting_on", due="2026-11-18", step="escalate to Lee"):
    d = C.loops.load()
    lid = C.loops.add(d, title=title, kind=kind, who=who, next_step=step, due=due, at=T(12, day=16))
    C.loops.save(d)
    return lid


# ------------------------------------------------------------------ silence is the default
def test_nothing_to_say_means_no_model_call_no_mail_search_no_message(C):
    sent = []
    r = C.tick(T(12), shadow=False, call=no_model, mail=no_mail, engine=engine(C.tmp), send=sent.append)
    assert r["sent"] is None and r["candidates"] == 0 and sent == []
    assert len(C.outbox.read()) == 1                                   # only the brief


def test_quiet_hours_and_a_spent_budget_build_nothing(C):
    add_loop(C)

    def no_engine_attr(*a, **k):
        raise AssertionError("candidates were built outside sendable hours")
    boom = SimpleNamespace(meals_logged=no_engine_attr, daily_fm=no_engine_attr,
                           planned_sessions_per_week=no_engine_attr, WORKOUTS=C.tmp)
    assert C.tick(T(22, 30), shadow=False, call=no_model, mail=no_mail, engine=boom)["why"] == "quiet hours"
    for i in range(4):
        C.outbox.record("nudge", f"t{i}", "x", at=T(9 + i))
    r = C.tick(T(15), shadow=False, call=no_model, mail=no_mail, engine=boom)
    assert r["sent"] is None and "daily cap" in r["why"]


# ------------------------------------------------------------------ loops
def test_due_loop_with_no_mail_says_what_was_checked_and_asks_once(C):
    lid = add_loop(C)
    sent = []
    r = C.tick(T(14, 5), shadow=False, use_llm=False, mail=lambda who, since: ("checked_none", {}),
               engine=engine(C.tmp), send=lambda t: sent.append(t) or True)
    assert r["sent"] == "live" and r["status"] == "checked_none"
    assert sent == ['I checked your inbox for mail from Dana since Mon Nov 16: nothing as of 2:05 PM. '
                    'This is "Acme tax form". Plan on file: escalate to Lee. Has anything come in another way?']
    row = C.outbox.read()[-1]
    assert (row["kind"], row["topic"], row["loop_id"], row["expects_reply"]) == ("nudge", f"loop:{lid}", lid, True)
    lp = C.loops.load()["loops"][lid]
    assert len(lp["asks"]) == 1 and lp["next_check_at"].startswith("2026-11-20")
    # an hour later: same topic today -> silent, and no second mail search
    r2 = C.tick(T(15, 5), shadow=False, call=no_model, mail=no_mail, engine=engine(C.tmp, meals=("lunch",)), send=sent.append)
    assert r2["sent"] is None and len(sent) == 1


def test_mail_found_is_reported_as_found_with_the_real_subject(C):
    add_loop(C)
    r = C.tick(T(14), shadow=False, use_llm=False, engine=engine(C.tmp), send=lambda t: True,
               mail=lambda who, since: ("found", {"date": "Tue, 17 Nov 2026 10:40", "subject": "Re: the form", "sender": "Dana <d@acme.example.com>"}))
    assert r["status"] == "found" and 'Mail from Dana is in your inbox' in r["text"] and '"Re: the form"' in r["text"]


def test_an_unsearchable_who_or_a_failed_search_never_becomes_nothing_found(C):
    assert not C.searchable("a friend") and not C.searchable("the forms team") and not C.searchable("")
    assert C.searchable("Dana") and C.searchable("Dana Lee") and C.searchable("jobs@acme.example.com")
    assert C.mail_from("a friend", datetime.date(2026, 11, 16))[0] == "could_not_check"
    add_loop(C, who="a friend")
    r = C.tick(T(14), shadow=False, use_llm=False, mail=C.mail_from, engine=engine(C.tmp), send=lambda t: True)
    assert r["status"] == "could_not_check" and "couldn't check" in r["text"] and "nothing as of" not in r["text"]
    # …and with no date on the loop there is nothing worth sending at all (shadow report, Nov 16)
    add_loop(C, title="Initech intro", who="a friend", due=None, step="the friend forwards the note")
    d = C.loops.load(); lid2 = [k for k in d["loops"] if "initech" in k][0]
    assert C.loop_candidate(lid2, d["loops"][lid2], T(14), mail=C.mail_from) is None
    C2 = C
    add_loop(C2, title="Globex offer", who="Sam")
    r = C2.loop_candidate("x", C2.loops.load()["loops"][[k for k in C2.loops.load()["loops"] if "globex" in k][0]],
                          T(14), mail=lambda w, s: ("could_not_check", {"reason": "the inbox search failed"}))
    assert r.status == "could_not_check" and "nothing as of" not in r.draft


def test_a_failed_send_records_nothing_and_does_not_count_as_an_ask(C):
    lid = add_loop(C)
    r = C.tick(T(14), shadow=False, use_llm=False, mail=lambda w, s: ("checked_none", {}),
               engine=engine(C.tmp), send=lambda t: False)
    assert r["sent"] is None and "send failed" in r["why"]
    assert len(C.outbox.read()) == 1 and C.loops.load()["loops"][lid]["asks"] == []


# ------------------------------------------------------------------ health
def test_health_gaps_fire_only_in_their_window_and_blank_is_unknown(C):
    E = engine(C.tmp, meals=("breakfast",), fm={"vitamins_taken": ""}, workouts=("2026-11-16",))
    topics = lambda n, e=E: [c.topic for c in C.health_candidates(n, e)]     # noqa: E731
    assert topics(T(12)) == [] and topics(T(14, 29)) == []
    assert topics(T(14, 30)) == ["lunch"] and topics(T(18, 0)) == []
    assert topics(T(20, 30)) == ["dinner"] and topics(T(21, 15)) == ["dinner", "vitamins"]
    c, = C.health_candidates(T(14, 30), E)
    assert c.draft == "No lunch logged today as of 2:30 PM. What did you have?"
    c, = C.health_candidates(T(14, 30), engine(C.tmp))
    assert "Nothing is logged for today yet." in c.draft and not any(ch.isdigit() for ch in c.draft.replace("2:30", ""))
    assert topics(T(14, 30), engine(C.tmp, meals=("lunch",))) == []
    assert topics(T(21, 15), engine(C.tmp, meals=("lunch", "dinner"), fm={"vitamins_taken": "true"})) == []


def test_lift_asks_only_when_the_week_is_getting_tight(C):
    topics = lambda n, e: [c.topic for c in C.health_candidates(n, e)]       # noqa: E731
    none = engine(C.tmp)                                                    # 0 of 3 logged
    assert "lift" not in topics(T(18, day=16), none)                        # Mon: 7 days left
    assert "lift" not in topics(T(18, day=18), none)                        # Wed: 5 left, 3 owed
    assert "lift" in topics(T(18, day=19), none)                            # Thu: 4 left, 3 owed
    assert "lift" not in topics(T(16, 59, day=19), none)                    # not before 5 PM
    one = engine(C.tmp, workouts=("2026-11-17",))
    assert "lift" not in topics(T(18, day=19), one)                         # 1 done, 2 owed, 4 left
    c = [x for x in C.health_candidates(T(18, day=20), one) if x.topic == "lift"][0]
    assert c.draft == "1 of 3 session logged this week and 3 days left, today included. Training today?"
    assert "lift" not in topics(T(18, day=20), engine(C.tmp, workouts=("2026-11-17", "2026-11-20")))   # trained today
    assert "lift" not in topics(T(18, day=22), engine(C.tmp, workouts=("2026-11-16", "2026-11-18", "2026-11-21")))


# ------------------------------------------------------------------ wording is verified in code
def test_model_wording_is_used_only_when_it_passes_grounding(C):
    add_loop(C)
    run = lambda reply: C.tick(T(14, 5), shadow=True, dry_run=True, mail=lambda w, s: ("checked_none", {}),   # noqa: E731
                               engine=engine(C.tmp), call=lambda s, u: reply)
    good = "Nothing from Dana in your inbox since Mon Nov 16, as of 2:05 PM. The plan was to escalate to Lee. Has it come in another way?"
    r = run(good)
    assert r["text"] == good and r["how"] == "model"
    for bad in ("Dana confirmed the tax form this morning, so you're all set.",            # asserts it happened
                "Nothing from Dana yet. Maybe ping Priya on the forms team?",                # invented name
                "Nothing from Dana since Nov 12. Has it come in another way?",              # wrong date number
                "Nothing from Dana yet. Did it come another way? Should I escalate to Lee?",  # two questions
                "Nothing from Dana yet, see https://acme.test/forms",                        # link
                "", None):
        r = run(bad)
        assert r["text"].startswith("I checked your inbox for mail from Dana") and r["how"].startswith("draft"), bad


def test_ground_rules_directly(C):
    G = C.ground
    facts = "who: Dana\ninbox check: no mail from Dana since Mon Nov 16, checked 2:05 PM\nplan on file: escalate to Lee"
    ok = lambda t, s="checked_none": G.validate(t, facts, s)[0]             # noqa: E731
    assert ok("Still nothing from Dana since Mon Nov 16. Want to escalate to Lee?")
    assert ok("Dana hasn't replied yet.") and ok("Has Dana replied another way?")       # negated / asked, not asserted
    assert not ok("Dana replied.") and ok("Dana replied.", "found")
    assert not ok("Nothing since Tuesday.") and not ok("Nothing in 3 days.")
    assert not ok("Nothing from Dana. Try Slack?")                                       # invented product name
    assert not ok("x" * 500) and not ok("") and not G.validate("ok", facts, "weird")[0]
    assert ok("Nothing yet. I'll look again tomorrow.")                                   # everyday capitals are fine
    # bookkeeping only: it may offer to look again or close, never to act (first VPS dry run, Nov 16)
    for bad in ("Still nothing from Dana. Want me to send it now?", "Nothing yet. I'll email Dana for you.",
                "Nothing from Dana. Should I escalate to Lee?", "Nothing yet. I can book a call."):
        assert not ok(bad), bad
    for fine in ("Nothing from Dana. Want me to look again tomorrow?", "Nothing yet. Should I close this one?",
                 "Nothing from Dana. I'll check again tomorrow."):
        assert ok(fine), fine


# ------------------------------------------------------------------ shadow
def test_shadow_writes_the_report_and_touches_no_live_state(C):
    lid = add_loop(C)
    before_ledger = (C.tmp / "state" / "outbox.jsonl").read_text()
    before_loops = (C.tmp / "state" / "loops.json").read_text()

    def never(t):
        raise AssertionError("shadow mode sent a message")
    r = C.tick(T(14, 5), shadow=True, use_llm=False, mail=lambda w, s: ("checked_none", {}),
               engine=engine(C.tmp), send=never)
    assert r["sent"] == "shadow"
    assert (C.tmp / "state" / "outbox.jsonl").read_text() == before_ledger
    assert (C.tmp / "state" / "loops.json").read_text() == before_loops
    report = (C.tmp / "vault" / "09 - Systems" / "Hermes" / "Assistant Shadow Report.md").read_text()
    assert "I checked your inbox for mail from Dana" in report and "Nothing below was sent" in report
    # shadow obeys the same once-a-day rule against its own rows
    r2 = C.tick(T(15, 5), shadow=True, call=no_model, mail=no_mail, engine=engine(C.tmp, meals=("lunch",)), send=never)
    assert r2["sent"] is None and "shadow" in r2["why"]


def test_shadow_is_the_default_until_the_live_flag_exists(C, monkeypatch):
    seen = {}
    monkeypatch.setattr(C, "tick", lambda n, **k: seen.update(k) or {})
    C.main(["--now", "2026-11-18T14:00"])
    assert seen["shadow"] is True
    C.live_flag().write_text("on")
    C.main(["--now", "2026-11-18T14:00"])
    assert seen["shadow"] is False
    C.main(["--now", "2026-11-18T14:00", "--shadow"])
    assert seen["shadow"] is True


def test_the_tick_reads_mail_through_functions_and_never_runs_triage_main():
    code = "\n".join(ln for ln in (A / "checkin.py").read_text().splitlines() if not ln.strip().startswith("#"))
    assert "ET.main(" not in code and "email_triage.main(" not in code
    assert "ET._gapi(\"gmail\", \"search\"" in code                       # the only Gmail verb it uses


def test_a_health_topic_already_raised_today_stays_quiet(C):
    C.outbox.record("nudge", "lunch", "No lunch logged…", at=T(14, 30))
    r = C.tick(T(15, 30), shadow=False, call=no_model, mail=no_mail, engine=engine(C.tmp), send=lambda t: True)
    assert r["sent"] is None and "already raised" in r["why"]


def test_a_failed_inbox_search_is_could_not_check_never_nothing_found(C, monkeypatch):
    def down(*a, **k):
        raise RuntimeError("google_api.py gmail search failed")
    monkeypatch.setitem(sys.modules, "email_triage", SimpleNamespace(_gapi=down, _parse_search=lambda s: []))
    status, info = C.mail_from("Dana", datetime.date(2026, 11, 16))
    assert status == "could_not_check" and "failed" in info["reason"]
    monkeypatch.setitem(sys.modules, "email_triage", SimpleNamespace(_gapi=lambda *a, **k: "[]", _parse_search=lambda s: []))
    assert C.mail_from("Dana", datetime.date(2026, 11, 16)) == ("checked_none", {})


def test_no_lift_question_on_a_day_he_already_trained(C):
    sat = T(18, day=21)                                    # 2 days left; only today's session logged
    assert "lift" in [c.topic for c in C.health_candidates(sat, engine(C.tmp, workouts=("2026-11-20",)))]
    # same week, but now today's session is logged too (the helper adds to the same folder)
    assert "lift" not in [c.topic for c in C.health_candidates(sat, engine(C.tmp, workouts=("2026-11-21",)))]


def test_a_passive_wait_with_nothing_arrived_stays_silent_but_speaks_when_mail_arrives(C):
    """First shadow run, 2026-11-16: it asked 'has anything come in another way?' about a
    thread whose plan was 'no further chasing, await response'."""
    lid = add_loop(C, title="Globex status", who="jobs@globex.example.com", step="No further chasing planned; await response")
    sent = []
    r = C.tick(T(14), shadow=False, call=no_model, mail=lambda w, s: ("checked_none", {}),
               engine=engine(C.tmp), send=lambda t: sent.append(t) or True)
    assert r["sent"] is None and sent == [] and "passive" in r["why"]
    assert C.loops.load()["loops"][lid]["next_check_at"].startswith("2026-11-21")        # looked at again in 3 days
    d = C.loops.load(); C.loops.update(d, lid, check_after="2026-11-18", at=T(9)); C.loops.save(d)
    r = C.tick(T(15), shadow=False, use_llm=False, engine=engine(C.tmp, meals=("lunch",)), send=lambda t: sent.append(t) or True,
               mail=lambda w, s: ("found", {"date": "Wed, 18 Nov 2026", "subject": "Your application", "sender": "jobs@globex.example.com"}))
    assert r["sent"] == "live" and "Mail from jobs@globex.example.com is in your inbox" in sent[0]


# ------------------------------------------------------------------ new mail, twice a day
MAILS = [{"id": "m1", "from": "Deals <news@shop.example.com>", "subject": "50% off this weekend", "date": "Wed, 18 Nov 2026 09:10", "body": "Sale ends Sunday."},
         {"id": "m2", "from": "Dana Reyes <dana@acme.example.com>", "subject": "Forms for Friday", "date": "Wed, 18 Nov 2026 11:42",
          "body": "Hi, could you send the signed form back by Friday Nov 20? Thanks, Dana"}]


def fetcher(mails):
    return lambda seen: [m for m in mails if m["id"] not in seen]


def test_inbox_picks_at_most_one_message_and_checks_its_summary_against_the_mail(C):
    pick = lambda s, u: json.dumps({"pick": 2, "summary": "Dana asks for the signed form back by Friday Nov 20."})   # noqa: E731
    c = C.inbox_candidate(T(13, 35), shadow=False, call=pick, fetch=fetcher(MAILS), triage_seen=[])
    assert c.topic == "mail:m2" and c.polish is False and c.status == "found"
    assert c.draft == ('New mail from Dana Reyes: "Forms for Friday". Dana asks for the signed form back by Friday Nov 20. '
                       'Want me to keep track of it?')
    assert C.ground.validate(c.draft, c.draft, "found")[0]                 # the draft itself passes the send rules
    # an invented detail in the summary is dropped; the subject line still goes out
    for n_, bad in enumerate(("Dana needs the form by Thursday and Priya is waiting.", "Reply within 3 hours.")):
        C.inbox_state_path(False).unlink()
        c = C.inbox_candidate(T(13, 35), shadow=False, call=lambda s, u, bad=bad: json.dumps({"pick": 2, "summary": bad}),
                              fetch=fetcher(MAILS), triage_seen=[])
        assert c.draft == 'New mail from Dana Reyes: "Forms for Friday". Want me to keep track of it?', bad


def test_inbox_runs_once_per_slot_never_rereads_and_skips_what_the_morning_read_saw(C):
    calls = []

    def nothing(s, u):
        calls.append(u)
        return '{"pick": null}'
    assert C.inbox_candidate(T(12, 35), shadow=False, call=nothing, fetch=fetcher(MAILS), triage_seen=[]) is None   # not an inbox hour
    assert calls == []
    assert C.inbox_candidate(T(13, 35), shadow=False, call=nothing, fetch=fetcher(MAILS), triage_seen=["m1"]) is None
    assert len(calls) == 1 and "Forms for Friday" in calls[0] and "50% off" not in calls[0]     # m1 was the morning's
    assert C.inbox_candidate(T(13, 50), shadow=False, call=nothing, fetch=fetcher(MAILS), triage_seen=[]) is None   # same slot
    assert len(calls) == 1
    later = MAILS + [{"id": "m3", "from": "Sam <sam@globex.example.com>", "subject": "Call?", "date": "Wed, 18 Nov 2026 16:00", "body": "Free at 5?"}]
    C.inbox_candidate(T(17, 35), shadow=False, call=nothing, fetch=fetcher(later), triage_seen=["m1"])
    assert len(calls) == 2 and "Call?" in calls[1] and "Forms for Friday" not in calls[1]      # m2 is not read again
    assert not C.inbox_state_path(True).exists()                                               # shadow keeps its own state


def test_inbox_failures_raise_nothing_and_a_failed_call_is_retried(C):
    def down(seen):
        raise RuntimeError("gmail search failed")
    assert C.inbox_candidate(T(13, 35), shadow=False, call=no_model, fetch=down, triage_seen=[]) is None
    assert C.inbox_candidate(T(13, 35), shadow=False, call=lambda s, u: None, fetch=fetcher(MAILS), triage_seen=[]) is None
    assert not C.inbox_state_path(False).exists()                    # nothing remembered: the same mail is looked at next slot
    for junk in ("not json", '{"pick": 9, "summary": "x"}', '{"pick": "two"}'):
        if C.inbox_state_path(False).exists():
            C.inbox_state_path(False).unlink()
        assert C.inbox_candidate(T(13, 35), shadow=False, call=lambda s, u, j=junk: j, fetch=fetcher(MAILS), triage_seen=[]) is None
    C.inbox_state_path(False).unlink()
    assert C.inbox_candidate(T(13, 35), shadow=False, call=lambda s, u: '{"pick": null}', fetch=fetcher(MAILS),
                             triage_seen=[], persist=False) is None
    assert not C.inbox_state_path(False).exists()                    # --dry-run writes nothing


def test_tick_raises_new_mail_after_loops_and_before_health(C):
    cand = C.Candidate("mail:m2", "found", 'New mail from Dana Reyes: "Forms for Friday". Want me to keep track of it?',
                       {"from": "Dana"}, True, None, polish=False)
    sent = []
    r = C.tick(T(14, 35), shadow=False, call=no_model, mail=no_mail, engine=engine(C.tmp), inbox=lambda n: cand,
               send=lambda t: sent.append(t) or True)
    assert r["sent"] == "live" and r["topic"] == "mail:m2" and r["how"] == "draft (already checked)"
    assert sent == [cand.draft]                                      # lunch was also due; mail goes first


def test_gym_question_mentions_what_is_still_on_the_calendar(C):
    events = [{"summary": "Haircut", "start": "2026-11-19T18:30:00-05:00", "status": "confirmed"},
              {"summary": "Standup", "start": "2026-11-19T09:00:00-05:00", "status": "confirmed"},       # already past
              {"summary": "Trip", "start": "2026-11-19", "status": "confirmed"},                          # all-day
              {"summary": "Dropped", "start": "2026-11-19T20:00:00-05:00", "status": "cancelled"}]
    n = T(17, 35, day=19)
    assert C.evening_events(n, fetch=lambda d: events) == ["Haircut at 6:30 PM"]
    assert C.evening_events(n, fetch=lambda d: (_ for _ in ()).throw(OSError("down"))) == []
    r = C.tick(n, shadow=False, use_llm=False, mail=no_mail, engine=engine(C.tmp), send=lambda t: True,
               calendar=lambda t: ["Haircut at 6:30 PM"])
    assert r["topic"] == "lift"
    assert r["text"] == ("0 of 3 sessions logged this week and 4 days left, today included. "
                         "Still on your calendar today: Haircut at 6:30 PM. Training today?")
    assert C.ground.validate(r["text"], r["text"], "fact")[0]
