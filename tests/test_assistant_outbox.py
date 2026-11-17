"""scripts/assistant/outbox.py + llm.py — the shared ledger, the nudge budget and the
spend guard (2026-11-16). Each test pins a way the assistant could over-message him,
lose track of what it said, or spend without limit. Mutation-tested."""
from __future__ import annotations

import datetime
import importlib.util
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

A = Path(__file__).resolve().parent.parent / "scripts" / "assistant"
TZ = ZoneInfo("America/New_York")


def T(h, m=0, day=16):
    return datetime.datetime(2026, 11, day, h, m, tzinfo=TZ)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, A / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def O(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ASSISTANT_DIR", str(tmp_path))
    return _load("outbox")


@pytest.fixture
def L(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ASSISTANT_DIR", str(tmp_path))
    return _load("llm")


def brief(O, at=None):
    O.record("digest", "morning-brief", "Morning.", at=at or T(8, 30))


# ------------------------------------------------------------------ the ledger
def test_every_record_is_one_line_and_reads_back(O, tmp_path):
    O.record("nudge", "lunch", "No lunch logged. What did you have?", expects_reply=True, at=T(14))
    O.record("alert", "sync-watchdog", "sync is down", at=T(15))
    rows = O.read()
    assert [(r["kind"], r["topic"]) for r in rows] == [("nudge", "lunch"), ("alert", "sync-watchdog")]
    assert rows[0]["expects_reply"] is True and "expects_reply" not in rows[1]
    assert len((tmp_path / "outbox.jsonl").read_text().splitlines()) == 2
    with pytest.raises(ValueError):
        O.record("spam", "x", "y")


def test_corrupt_ledger_blocks_nudges_and_is_never_read_as_empty(O, tmp_path):
    """The budget this replaced returned {} on a corrupt state file = no limit at all."""
    brief(O)
    assert O.allow("nudge", "lunch", at=T(14))[0]
    with open(tmp_path / "outbox.jsonl", "a") as fh:
        fh.write("{not json\n")
    ok, why = O.allow("nudge", "lunch", at=T(14))
    assert not ok and "unreadable" in why
    with pytest.raises(O.LedgerError):
        O.read()
    assert O.allow("alert", "sync-watchdog", at=T(14))[0]          # alerts are never blocked
    assert O.record_quiet("alert", "sync-watchdog", "still recorded", at=T(14))


def test_missing_ledger_is_an_empty_ledger_not_an_error(O):
    assert O.read() == []


# ------------------------------------------------------------------ the policy
def test_quiet_hours_and_quiet_until_the_brief(O):
    assert not O.allow("nudge", "x", at=T(22, 0))[0]               # 10 PM sharp is quiet
    assert not O.allow("nudge", "x", at=T(6, 59))[0]
    ok, why = O.allow("nudge", "x", at=T(9, 0))
    assert not ok and "brief" in why                                 # brief not out yet
    assert O.allow("nudge", "x", at=T(11, 0))[0]                    # fallback: quiet ends at 11 regardless
    brief(O, at=T(8, 30))
    assert O.allow("nudge", "x", at=T(9, 0))[0]                     # brief out -> the day is open
    assert O.allow("nudge", "x", at=T(21, 59))[0]


def test_daily_cap_counts_nudges_only(O):
    brief(O)
    for i, topic in enumerate(["a", "b", "c"]):
        O.record("nudge", topic, "t", at=T(12 + i))
    O.record("digest", "card:fuel", "t", at=T(15))
    O.record("reactive", "session-review", "t", at=T(15))
    O.record("alert", "sync", "t", at=T(15))
    assert O.allow("nudge", "d", at=T(16))[0]                       # 3 nudges so far
    O.record("nudge", "d", "t", at=T(16))
    ok, why = O.allow("nudge", "e", at=T(17))
    assert not ok and "cap" in why
    assert O.allow("nudge", "e", at=T(9, day=17))[0] is False       # next morning: no brief yet
    O.record("digest", "morning-brief", "t", at=T(8, day=17))
    assert O.allow("nudge", "e", at=T(9, day=17))[0]                # brief out, and the cap has reset


def test_a_topic_is_raised_once_a_day_across_the_brief_and_the_tick(O):
    O.record("digest", "morning-brief", "t", topics=["loop:acme-form", "food"], at=T(8, 30))
    ok, why = O.allow("nudge", "loop:acme-form", at=T(14))
    assert not ok and "already raised" in why                        # the brief claimed it
    assert not O.allow("nudge", "food", at=T(14))[0]
    assert O.allow("nudge", "water", at=T(14))[0]
    O.record("nudge", "water", "t", at=T(14))
    assert not O.allow("nudge", "water", at=T(18))[0]
    assert O.topic_used_today("water", at=T(18)) and not O.topic_used_today("water", at=T(12, day=17))


def test_reply_rules_apply_only_when_reply_state_is_known(O):
    """Before the plugin writes last_inbound.json, 'has he replied' is UNKNOWN. Reading
    unknown as 'no' would silence everything after five messages, for ever."""
    for d in range(10, 16):
        O.record("nudge", f"t{d}", "q?", expects_reply=True, loop_id="L1", at=T(14, day=d))
    brief(O)
    assert O.last_inbound() is None
    assert O.allow("nudge", "today", loop_id="L1", expects_reply=True, at=T(14))[0]


def test_question_lock_expires_and_asks_per_loop_are_capped(O):
    O.note_inbound(T(9, day=14))
    brief(O, at=T(8, 30, day=15))
    O.record("nudge", "loop:L1", "Heard back from Sam?", loop_id="L1", expects_reply=True, at=T(14, day=15))
    ok, why = O.allow("nudge", "water", expects_reply=True, at=T(18, day=15))
    assert not ok and "unanswered" in why                            # one open question at a time
    assert O.allow("nudge", "water", expects_reply=False, at=T(18, day=15))[0]   # a statement is fine
    brief(O, at=T(8, 30, day=16))
    assert O.allow("nudge", "water", expects_reply=True, at=T(14, 1, day=16))[0]  # lock expired after 24 h
    O.record("nudge", "loop:L1", "Still nothing from Sam?", loop_id="L1", expects_reply=True, at=T(15, day=16))
    brief(O, at=T(8, 30, day=18))
    ok, why = O.allow("nudge", "loop:L1", loop_id="L1", at=T(15, day=18))
    assert not ok and "no reply" in why                              # asked twice, he never answered
    O.note_inbound(T(15, 30, day=18))
    assert O.allow("nudge", "loop:L1", loop_id="L1", at=T(16, day=18))[0]   # he wrote: the count resets


def test_backoff_after_a_streak_of_unanswered_nudges(O):
    O.note_inbound(T(9, day=9))
    for d in range(10, 15):
        O.record("nudge", f"t{d}", "t", at=T(14, day=d))
    brief(O)
    ok, why = O.allow("nudge", "today", at=T(14))
    assert not ok and "in a row" in why
    assert O.allow("digest", "morning-brief", at=T(14))[0]          # the brief still goes
    O.note_inbound(T(14, 5))
    assert O.allow("nudge", "today", at=T(14, 10))[0]


def test_unanswered_lists_only_what_he_has_not_written_after(O):
    O.record("nudge", "lunch", "No lunch logged.", at=T(13))
    O.record("alert", "sync", "down", at=T(13, 30))
    O.record("nudge", "water", "Water?", at=T(16))
    assert [r["topic"] for r in O.unanswered(at=T(17))] == ["lunch", "water"]    # reply state unknown
    O.note_inbound(T(14))
    assert [r["topic"] for r in O.unanswered(at=T(17))] == ["water"]
    assert O.unanswered(max_age_h=18, at=T(17, day=17)) == []        # too old to hand to the agent


def test_cli_record_for_shell_senders(O):
    assert O.main(["record", "--kind", "alert", "--topic", "vault-snapshot", "--text", "failing"]) == 0
    assert O.read()[0]["topic"] == "vault-snapshot"


# ------------------------------------------------------------------ the spend guard
def _post(text="ok", usage=None, stop="end_turn"):
    def post(body, key, timeout):
        return {"stop_reason": stop, "usage": usage or {"input_tokens": 1000, "output_tokens": 100},
                "content": [{"type": "thinking", "thinking": "…"}, {"type": "text", "text": text}]}
    return post


def test_call_returns_text_by_block_type_and_records_the_cost(L, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_env", lambda k: "key")
    assert L.call("s", "u", purpose="tick", post=_post(" hello ")) == "hello"
    row = json.loads((tmp_path / "spend.jsonl").read_text())
    assert row["purpose"] == "tick" and row["in"] == 1000 and row["ok"] is True
    assert row["usd"] == pytest.approx((1000 * 1.0 + 100 * 5.0) / 1e6)          # Haiku pricing
    assert L.price("claude-sonnet-4-6", {"input_tokens": 1_000_000}) == 3.0
    assert L.price("some-new-model", {"input_tokens": 1_000_000}) == 15.0        # unknown = dearest


def test_daily_call_ceiling_refuses_without_touching_the_network(L, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_env", lambda k: "key")
    monkeypatch.setattr(L, "MAX_CALLS_PER_RUN", 10_000)
    for _ in range(L.DAILY_CALL_CEILING):
        assert L.call("s", "u", purpose="tick", post=_post()) == "ok"

    def boom(*a):
        raise AssertionError("the network was reached past the ceiling")
    assert L.call("s", "u", purpose="tick", post=boom) is None
    assert "calls today" in L.tripped()
    assert len((tmp_path / "spend.jsonl").read_text().splitlines()) == L.DAILY_CALL_CEILING


def test_daily_dollar_ceiling(L, monkeypatch):
    monkeypatch.setattr(L, "_env", lambda k: "key")
    big = {"input_tokens": 400_000, "output_tokens": 0}                           # $1.20 on Sonnet
    assert L.call("s", "u", purpose="extract", model=L.SONNET, post=_post(usage=big)) == "ok"
    assert L.allow()[0]
    assert L.call("s", "u", purpose="extract", model=L.SONNET, post=_post(usage=big)) == "ok"   # now $2.40
    ok, why = L.allow()
    assert not ok and "spend ceiling" in why


def test_one_run_cannot_loop(L, monkeypatch):
    monkeypatch.setattr(L, "_env", lambda k: "key")
    for _ in range(L.MAX_CALLS_PER_RUN):
        L.call("s", "u", purpose="tick", post=_post())
    ok, why = L.allow()
    assert not ok and "this run" in why


def test_failed_calls_still_count_toward_the_ceiling(L, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_env", lambda k: "key")

    def down(*a):
        raise OSError("network down")
    assert L.call("s" * 4000, "u", purpose="tick", post=down) is None
    row = json.loads((tmp_path / "spend.jsonl").read_text())
    assert row["ok"] is False and row["in"] == 1000 and L.today_totals()["calls"] == 1


def test_corrupt_spend_record_refuses_instead_of_reading_as_zero(L, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_env", lambda k: "key")
    (tmp_path / "spend.jsonl").write_text("{broken\n")
    ok, why = L.allow()
    assert not ok and "unreadable" in why
    assert L.call("s", "u", purpose="tick", post=_post()) is None


def test_oversized_prompt_and_reply_budget_are_bounded(L, monkeypatch):
    monkeypatch.setattr(L, "_env", lambda k: "key")
    seen = {}

    def post(body, key, timeout):
        seen.update(json.loads(body))
        return {"stop_reason": "end_turn", "usage": {}, "content": [{"type": "text", "text": "ok"}]}
    assert L.call("s", "u", purpose="tick", max_tokens=999_999, post=post) == "ok"
    assert seen["max_tokens"] == L.MAX_TOKENS_CEILING
    assert L.call("s", "u" * (L.MAX_PROMPT_CHARS + 1), purpose="tick", post=post) is None
    assert L.call("s", "u", purpose="tick", post=_post(stop="max_tokens")) is None   # cut-off reply is not text


def test_spend_resets_at_toronto_midnight(L, monkeypatch):
    monkeypatch.setattr(L, "_env", lambda k: "key")
    monkeypatch.setattr(L, "now", lambda: T(23, 50))
    L.call("s", "u", purpose="tick", post=_post())
    assert L.today_totals()["calls"] == 1
    monkeypatch.setattr(L, "now", lambda: T(0, 10, day=17))
    assert L.today_totals()["calls"] == 0


# ------------------------------------------------------------------ wiring
def test_deploy_copies_the_assistant_directory():
    """2026-10-04 precedent: scripts/email/ existed for weeks and nothing copied it."""
    deploy = (A.parent / "deploy.sh").read_text()
    code = "\n".join(ln for ln in deploy.splitlines() if not ln.strip().startswith("#"))
    assert "cp -r scripts/assistant ~/.hermes/scripts/assistant" in code


def test_assistant_code_cannot_write_to_gmail():
    """His rule: read-only on Gmail, by any tool. No assistant module may call a Gmail
    write verb through the google-workspace script."""
    import re
    for py in A.glob("*.py"):
        if py.name.startswith("test_"):
            continue
        code = "\n".join(ln for ln in py.read_text().splitlines() if not ln.strip().startswith("#"))
        assert not re.search(r"gmail[\"', ]+(send|reply|draft|modify|trash|delete|label)", code, re.I), py.name
