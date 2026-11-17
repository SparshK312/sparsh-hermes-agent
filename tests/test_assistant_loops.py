"""scripts/assistant/loops.py + loop_extract.py — the open-threads store and its intake
from the vault log (2026-11-16). All names are invented. Mutation-tested."""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

A = Path(__file__).resolve().parent.parent / "scripts" / "assistant"
TZ = ZoneInfo("America/New_York")
TODAY = datetime.date(2026, 11, 16)


def T(h=12, m=0, day=16):
    return datetime.datetime(2026, 11, day, h, m, tzinfo=TZ)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ASSISTANT_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HERMES_VAULT", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir()
    sys.path.insert(0, str(A))
    for name in ("loops", "llm", "loop_extract"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("loop_extract", A / "loop_extract.py")
    X = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(X)
    X.loops.now = lambda: T()
    return X, X.loops, tmp_path


def write_log(tmp_path, day, *entries):
    p = tmp_path / "vault" / "Log" / "2026" / "2026-11" / f"2026-11-{day:02d}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_text() if p.exists() else "---\ntype: log\n---\n"
    p.write_text(old + "".join(f"\n## [2026-11-{day:02d}] {a} | {scope} — {summary}\n- body line\n"
                               for a, scope, summary in entries))


def reply(*ops):
    return lambda s, u: json.dumps({"ops": list(ops)})


# ------------------------------------------------------------------ the store
def test_add_update_close_and_the_view(env):
    X, L, tmp = env
    d = L.load()
    lid = L.add(d, title="Acme tax form", kind="waiting_on", who="Dana", next_step="escalate to Lee",
                due="2026-11-18", source={"date": "2026-11-16", "scope": "Acme"}, at=T())
    assert d["loops"][lid]["next_check_at"].startswith("2026-11-18T10:00")
    assert L.add(d, title="ACME  tax form!", kind="waiting_on", next_step="call instead", at=T()) == lid   # same thread
    assert len(d["loops"]) == 1 and d["loops"][lid]["next_step"] == "call instead"
    L.save(d)
    assert L.write_view(d, at=T()) is True
    view = (tmp / "vault" / "00 - Dashboard" / "Open Loops.md").read_text()
    assert "| Acme tax form | Waiting on | Dana | call instead | Wed Nov 18 | Wed Nov 18 |" in view
    assert L.write_view(d, at=T(day=17)) is False                     # same content next day: no rewrite
    with pytest.raises(ValueError):
        L.close(d, lid, "  ")                                         # no evidence, no close
    L.close(d, lid, "form received", at=T(day=17))
    assert L.open_loops(d) == [] and L.write_view(d, at=T(day=17)) is True
    assert "form received" in (tmp / "vault" / "00 - Dashboard" / "Open Loops.md").read_text()


def test_corrupt_store_is_refused_never_overwritten(env):
    X, L, tmp = env
    (tmp / "state").mkdir()
    (tmp / "state" / "loops.json").write_text("{broken")
    with pytest.raises(L.LoopsError):
        L.load()
    with pytest.raises(L.LoopsError):
        X.run(tmp / "vault", today=TODAY, call=reply())
    assert (tmp / "state" / "loops.json").read_text() == "{broken"
    assert L.main(["list"]) == 2


def test_every_loop_expires_and_two_unanswered_asks_make_it_dormant(env):
    X, L, tmp = env
    d = L.load()
    a = L.add(d, title="No date thread", kind="waiting_on", at=T())
    b = L.add(d, title="Asked thread", kind="my_move", due="2026-11-18", at=T())
    assert d["loops"][a]["expires_at"].startswith("2026-11-30")       # 14 days, even with no date given
    assert [lid for lid, _ in L.due(d, at=T(day=17))] == []
    assert [lid for lid, _ in L.due(d, at=T(day=18))] == [b, a]        # b: its due date 10:00; a: +2 days (12:00)
    L.note_ask(d, b, at=T(day=18))
    assert d["loops"][b]["status"] == "open" and d["loops"][b]["next_check_at"].startswith("2026-11-20")
    L.note_ask(d, b, at=T(day=20))
    assert d["loops"][b]["status"] == "dormant"
    assert L.sweep(d, at=T(day=29)) == [] and L.sweep(d, at=T(day=30)) == [a]
    assert d["loops"][a]["status"] == "dormant" and L.open_loops(d) == []
    L.update(d, b, check_after="2026-12-05", at=T(day=30))            # new information revives it
    assert d["loops"][b]["status"] == "open" and d["loops"][b]["asks"] == []


def test_cli_refuses_an_ambiguous_fragment(env, capsys):
    X, L, tmp = env
    assert L.main(["add", "--title", "Acme tax form", "--kind", "waiting_on", "--who", "Dana"]) == 0
    assert L.main(["add", "--title", "Acme background check", "--kind", "waiting_on"]) == 0
    with pytest.raises(SystemExit) as e:
        L.main(["close", "acme", "--evidence", "done"])
    assert "2 open loops match" in str(e.value)
    assert L.main(["close", "tax form", "--evidence", "form received Nov 17"]) == 0
    assert [lp["title"] for _, lp in L.open_loops(L.load())] == ["Acme background check"]


# ------------------------------------------------------------------ intake from the log
def test_tagged_entry_becomes_a_loop_without_any_model_call(env):
    X, L, tmp = env
    write_log(tmp, 16, ("update", "Acme", "form requested from Dana ⏳ who=Dana; by=2026-11-18; then=escalate to Lee"),
              ("update", "hermes:prefill", "machine entry ⏳ who=Bot; by=2026-11-18; then=nothing"))

    def boom(s, u):
        raise AssertionError("the model was called for a tagged-only batch")
    out = X.run(tmp / "vault", today=TODAY, call=boom)
    assert out["tagged"] == 1 and out["model"] == "skipped"
    (lid, lp), = L.open_loops(L.load())
    assert (lp["who"], lp["next_step"], lp["due"]) == ("Dana", "escalate to Lee", "2026-11-18")
    assert lp["title"] == "Acme: form requested from Dana" and lp["sources"][0]["date"] == "2026-11-16"


def test_prose_entries_go_through_one_validated_model_call(env):
    X, L, tmp = env
    write_log(tmp, 15, ("decision", "Globex", "status check to the recruiter only if silent by Tue Nov 17"),
              ("ingest", "Finances", "saved a research report"))
    calls = []

    def call(s, u):
        calls.append(u)
        return json.dumps({"ops": [
            {"op": "add", "entry": "E1", "title": "Globex status check", "kind": "conditional_plan",
             "who": "", "next_step": "send a status check to the recruiter", "due": "2026-11-17", "check_after": "2026-11-17"},
            {"op": "add", "entry": "E9", "title": "Invented", "kind": "waiting_on"},                 # unknown entry
            {"op": "add", "entry": "E2", "title": "Report", "kind": "nonsense"},                     # bad kind
            {"op": "add", "entry": "E2", "title": "Waiting on Zed", "kind": "waiting_on", "who": "Zed"},  # name not in entry
        ]})
    out = X.run(tmp / "vault", today=TODAY, call=call)
    assert len(calls) == 1 and "E1 | 2026-11-15 | decision | Globex" in calls[0]
    assert (out["added"], len(out["rejected"])) == (1, 3)
    assert [lp["title"] for _, lp in L.open_loops(L.load())] == ["Globex status check"]
    assert X.run(tmp / "vault", today=TODAY, call=lambda s, u: pytest.fail("nothing new: no call"))["new"] == 0


def test_close_needs_hard_evidence(env):
    X, L, tmp = env
    write_log(tmp, 14, ("update", "Acme", "waiting on Dana for the agreement"))
    X.run(tmp / "vault", today=TODAY, call=reply(
        {"op": "add", "entry": "E1", "title": "Acme agreement", "kind": "waiting_on", "who": "Dana"}))
    (lid, _), = L.open_loops(L.load())
    write_log(tmp, 15, ("decision", "Acme", "plan: email Dana on Wednesday, then it is done"),
              ("update", "Acme", "Dana is still checking with the forms team"),
              ("update", "Acme", "agreement received from Dana and signed"))
    out = X.run(tmp / "vault", today=TODAY, call=reply(
        {"op": "close", "loop": lid, "entry": "E1", "evidence": "then it is done"},          # a decision never closes
        {"op": "close", "loop": lid, "entry": "E2", "evidence": "still checking"},           # nothing finished
        {"op": "close", "loop": lid, "entry": "E3", "evidence": "the agreement arrived"},    # not a verbatim quote
        {"op": "close", "loop": "nope", "entry": "E3", "evidence": "agreement received"}))   # unknown loop
    assert out["closed"] == 0 and len(out["rejected"]) == 4
    assert L.load()["loops"][lid]["status"] == "open"
    write_log(tmp, 16, ("update", "Acme", "Agreement received from Dana, signed and returned"))
    out = X.run(tmp / "vault", today=TODAY, call=reply(
        {"op": "close", "loop": lid, "entry": "E1", "evidence": "agreement received from dana"}))
    lp = L.load()["loops"][lid]
    assert out["closed"] == 1 and lp["status"] == "closed" and "log 2026-11-16 (Acme)" in lp["closed_evidence"]


def test_model_failure_changes_nothing_and_the_entries_are_retried(env):
    X, L, tmp = env
    write_log(tmp, 16, ("update", "Acme", "waiting on Dana for the agreement"))
    for bad in (None, "sorry, no JSON here", '{"ops": "x"}'):
        out = X.run(tmp / "vault", today=TODAY, call=lambda s, u, bad=bad: bad)
        assert out["added"] == 0 and L.open_loops(L.load()) == []
    assert L.load()["seen"] == []                                    # a failed call marks nothing seen
    out = X.run(tmp / "vault", today=TODAY, call=reply(
        {"op": "add", "entry": "E1", "title": "Acme agreement", "kind": "waiting_on", "who": "Dana"}))
    assert out["added"] == 1


def test_a_late_arriving_entry_is_processed_exactly_once(env):
    """Mac entries reach the VPS late through Sync: a day file gains an entry AFTER that
    day was already processed. A timestamp cursor would skip it."""
    X, L, tmp = env
    write_log(tmp, 16, ("update", "Acme", "waiting on Dana"))
    seen_users = []

    def call(s, u):
        seen_users.append(u)
        return '{"ops": []}'
    X.run(tmp / "vault", today=TODAY, call=call)
    write_log(tmp, 13, ("update", "Globex", "waiting on Sam for the offer letter"))    # older day, arrives now
    X.run(tmp / "vault", today=TODAY, call=call)
    X.run(tmp / "vault", today=TODAY, call=call)
    assert len(seen_users) == 2
    assert "Globex" in seen_users[1] and "Acme" not in seen_users[1].split("NEW LOG ENTRIES:")[1]


def test_the_per_run_cap_is_announced_and_nothing_is_dropped(env, capsys):
    X, L, tmp = env
    X.MAX_ENTRIES_PER_RUN = 3
    write_log(tmp, 16, *[("update", f"Scope{i}", f"note number {i}") for i in range(7)])
    batches = []

    def call(s, u):
        batches.append(u.split("NEW LOG ENTRIES:")[1].count("\nE"))
        return '{"ops": []}'
    assert X.run(tmp / "vault", today=TODAY, call=call)["left_for_next_run"] == 4
    assert "CAP: 7 new entries, processing 3 now, 4 left" in capsys.readouterr().err
    X.run(tmp / "vault", today=TODAY, call=call); X.run(tmp / "vault", today=TODAY, call=call)
    assert batches == [3, 3, 1] and X.run(tmp / "vault", today=TODAY, call=call)["new"] == 0


def test_seen_trim_drops_the_oldest_not_a_random_slice(env):
    """email_triage sorted its seen-set before trimming; hashes are random, so that threw
    away an arbitrary slice. Here the trim must drop the oldest-processed entries."""
    X, L, tmp = env
    X.SEEN_KEEP = 3
    order = []
    for day in range(12, 17):                                   # one entry a day; a 1-day window
        write_log(tmp, day, ("update", f"Scope{day}", f"note {day}"))
        X.run(tmp / "vault", days=1, today=datetime.date(2026, 11, day), call=reply())
        e, = X.done_facts.recent_entries(tmp / "vault", days=1, today=datetime.date(2026, 11, day))
        order.append(X.entry_hash(e))
    assert L.load()["seen"] == order[-3:]


def test_dry_run_writes_nothing(env):
    X, L, tmp = env
    write_log(tmp, 16, ("update", "Acme", "x ⏳ who=Dana; by=2026-11-18; then=escalate"))
    out = X.run(tmp / "vault", today=TODAY, dry_run=True, call=reply())
    assert out["tagged"] == 1 and not (tmp / "state" / "loops.json").exists()
    assert not (tmp / "vault" / "00 - Dashboard").exists()


def test_an_op_may_name_a_loop_added_in_the_same_batch_by_its_title(env):
    """First backfill, 2026-11-16: 'email X' was added from E3 and the same batch's E8 said
    it was sent, but the close named the loop by title (it had no id yet) and was dropped."""
    X, L, tmp = env
    write_log(tmp, 15, ("update", "Acme", "feedback email to Dana drafted, he sends it"),
              ("update", "Acme", "Feedback email sent to Dana (Nov 15 evening)"))
    out = X.run(tmp / "vault", today=TODAY, call=reply(
        {"op": "add", "entry": "E1", "title": "Acme feedback email to Dana", "kind": "my_move", "who": "Dana"},
        {"op": "close", "loop": "Acme feedback email to Dana", "entry": "E2", "evidence": "Feedback email sent to Dana"}))
    assert (out["added"], out["closed"], out["rejected"]) == (1, 1, [])
    assert L.open_loops(L.load()) == []


def test_open_rows_of_the_action_items_table_are_read_too(env):
    """2026-11-16: the extractor read only the log, so threads that lived in his THIS WEEK
    table never reached the list and he was asked to name what was missing."""
    X, L, tmp = env
    ai = tmp / "vault" / "00 - Dashboard"
    ai.mkdir(parents=True)
    (ai / "Action Items.md").write_text(
        "## 🔥 THIS WEEK — Mon Nov 16 → Sun Nov 22\n\n| When | What | Note |\n|---|---|---|\n"
        "| **Tue Nov 17** | 🎯 **Globex:** short status email to the recruiter | His call |\n"
        "| **By Wed Nov 18** | 🧾 **Acme form:** Dana checking with the tax team | Escalate to Lee only if she goes silent past Wed |\n"
        "| ✅ **Done Mon Nov 16** | research done | |\n"
        "| ⏳ **Waiting, nothing owed** | Initech · Hooli | |\n"
        "\n## 🗄️ LAST WEEK\n\n| When | What | Note |\n|---|---|---|\n| **Mon Nov 9** | old row | |\n")
    seen_user = []

    def call(s, u):
        seen_user.append(u)
        return json.dumps({"ops": [
            {"op": "add", "entry": "E2", "title": "Acme form with Dana", "kind": "waiting_on", "who": "Dana",
             "next_step": "Escalate to Lee if she goes silent past Wed", "due": "2026-11-18", "check_after": "2026-11-18"},
            {"op": "add", "entry": "E3", "title": "Initech reply", "kind": "waiting_on", "who": "Initech",
             "next_step": "Nothing owed; await a reply"}]})
    out = X.run(tmp / "vault", today=TODAY, call=call)
    body = seen_user[0].split("NEW LOG ENTRIES:")[1]
    assert body.count("Action Items / THIS WEEK") == 3                     # 3 open rows; ✅ row and last week skipped
    assert "note: Escalate to Lee only if she goes silent past Wed" in body and "old row" not in body
    assert out["added"] == 2
    titles = {lp["title"]: lp for _, lp in L.open_loops(L.load())}
    assert titles["Acme form with Dana"]["due"] == "2026-11-18"
    # unchanged table next day: nothing new, no model call
    assert X.run(tmp / "vault", today=TODAY + datetime.timedelta(days=1),
                 call=lambda s, u: pytest.fail("unchanged rows were re-read"))["new"] == 0
    # a missing file is announced, not fatal
    (ai / "Action Items.md").unlink()
    assert X.action_item_entries(tmp / "vault", set(), TODAY) == []
