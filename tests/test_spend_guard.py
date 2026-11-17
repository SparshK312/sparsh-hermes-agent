"""plugins/spend-guard — the chat agent's hard spend ceiling (2026-11-16). Mutation-tested."""
from __future__ import annotations

import datetime
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("America/New_York")
NOW = datetime.datetime(2026, 11, 16, 21, 0, tzinfo=TZ)


@pytest.fixture
def G(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ASSISTANT_DIR", str(tmp_path / "assistant"))
    spec = importlib.util.spec_from_file_location("spend_guard", ROOT / "plugins" / "spend-guard" / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.LOG = tmp_path / "guard.log"
    mod.now = lambda: NOW
    mod.tmp = tmp_path
    return mod


def ev(text="had eggs for lunch", platform="telegram"):
    return SimpleNamespace(text=text, source=SimpleNamespace(platform=platform, chat_id="42"))


def run(G, text, day, month=0.0, platform="telegram", deliver=True):
    sent = []
    out = G._guard(event=ev(text, platform), read_spend=lambda: (day, max(day, month)),
                   send=lambda cid, t: (sent.append((cid, t)) or deliver))
    return out, sent


def test_under_the_limit_every_message_goes_through_untouched(G):
    assert run(G, "had eggs", 1.20) == (None, [])
    assert run(G, "had eggs", G.DAILY_HARD_USD - 0.01) == (None, [])


def test_over_the_daily_limit_a_message_is_answered_without_a_model_call_and_dropped(G):
    out, sent = run(G, "plan my week in detail", 5.40)
    assert out == {"action": "skip", "reason": "spend-guard: over the spend limit"}
    assert sent[0][0] == "42" and "paused" in sent[0][1] and "$5.40" in sent[0][1] and "override limit" in sent[0][1]
    assert "NOT processed" in sent[0][1]                         # he must know the log did not happen
    assert "BLOCKED" in G.LOG.read_text()


def test_override_lifts_it_until_midnight_and_is_itself_free(G):
    out, sent = run(G, "Override limit", 5.40)
    assert out["action"] == "skip" and "lifted" in sent[0][1]    # the command never reaches the agent
    assert G.override_active()
    assert run(G, "plan my week", 7.00) == (None, [])            # same day: allowed
    G.now = lambda: NOW + datetime.timedelta(days=1)
    assert not G.override_active()                               # next day: the limit is back
    out, _ = run(G, "plan my week", 5.40)
    assert out["action"] == "skip"
    for not_cmd in ("override limits on my lifts?", "can you override limit", "override"):
        assert G.decide(not_cmd, 1.0, 1.0, False)[0] == "allow", not_cmd


def test_monthly_limit_blocks_too(G):
    out, sent = run(G, "hi", 0.30, month=61.00)
    assert out["action"] == "skip" and "this month" in sent[0][1] and "the 1st" in sent[0][1]


def test_only_telegram_messages_are_guarded(G):
    assert run(G, "anything", 99.0, platform="cron") == (None, [])
    assert run(G, "anything", 99.0, platform="cli") == (None, [])


def test_it_fails_open_and_says_so(G):
    def broken():
        raise sqlite3.OperationalError("database is locked")
    assert G._guard(event=ev(), read_spend=broken, send=lambda c, t: True) is None
    assert "FAILED OPEN" in G.LOG.read_text()
    assert G._guard(event=None) is None
    out, sent = run(G, "had eggs", 9.0, deliver=False)            # the notice could not be delivered
    assert out is None and "letting the message through" in G.LOG.read_text()


def test_spend_adds_the_database_and_the_assistant_scripts(G):
    db = G.tmp / "state.db"
    con = sqlite3.connect(db)
    con.execute("create table sessions (started_at, actual_cost_usd, estimated_cost_usd)")
    t = NOW.timestamp()
    con.executemany("insert into sessions values (?,?,?)", [
        (t - 3600, None, 1.10),            # today, estimate only
        (t - 7200, 0.50, 0.90),            # today, actual wins
        (t - 86400 * 3, None, 2.00),       # earlier this month
        (t - 86400 * 20, None, 9.00)])     # last month
    con.commit(); con.close()
    (G.tmp / "assistant").mkdir()
    (G.tmp / "assistant" / "spend.jsonl").write_text(
        json.dumps({"day": "2026-11-16", "usd": 0.20}) + "\n" + json.dumps({"day": "2026-11-02", "usd": 0.30}) + "\n{bad\n")
    day, month = G.spend(NOW, db)
    assert day == pytest.approx(1.80) and month == pytest.approx(4.10)
    with pytest.raises(Exception):
        G.spend(NOW, G.tmp / "missing.db")


def test_deploy_ships_the_plugin():
    code = (ROOT / "scripts" / "deploy.sh").read_text()
    assert "cp -r plugins/spend-guard ~/.hermes/plugins/spend-guard" in code
