"""scripts/monitor/upstream_watch.py + cost_monitor.direct_spend (2026-11-16). Mutation-tested."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

M = Path(__file__).resolve().parent.parent / "scripts" / "monitor"


def _load(name, tmp_path, monkeypatch):
    monkeypatch.setenv("UPSTREAM_WATCH_STATE", str(tmp_path / "uw.json"))
    monkeypatch.setenv("HERMES_ASSISTANT_DIR", str(tmp_path / "assistant"))
    spec = importlib.util.spec_from_file_location(name, M / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def api(merged, containing=()):
    """A fake GitHub: the PR state, three releases newest-first, and which tags contain the fix."""
    def fetch(path):
        if path.startswith("/pulls/"):
            return {"merged": merged, "merge_commit_sha": "abc123" if merged else None}
        if path.startswith("/releases"):
            return [{"tag_name": "v0.23.3+canary.1", "prerelease": True}, {"tag_name": "v0.23.2", "prerelease": False},
                    {"tag_name": "v0.23.1", "prerelease": False}]
        tag = path.split("/compare/")[1].split("...")[0]
        return {"status": "behind" if tag in containing else "ahead"}
    return fetch


def test_one_message_per_stage_and_none_while_the_fix_is_open(tmp_path, monkeypatch):
    U = _load("upstream_watch", tmp_path, monkeypatch)
    sent = []
    send = lambda s, b: sent.append(s) or True                                   # noqa: E731
    for _ in range(3):
        assert U.run(api(False), send)["stage"] == ""
    assert sent == []
    for _ in range(3):
        assert U.run(api(True), send)["stage"] == "merged"                     # merged, in no stable release
    assert len(sent) == 1 and "merged" in sent[0]
    for _ in range(3):
        r = U.run(api(True, containing=("v0.23.2",)), send)
    assert r["stage"] == "released" and r["release"] == "v0.23.2"
    assert len(sent) == 2 and "unblocked" in sent[1]


def test_a_prerelease_containing_the_fix_does_not_count(tmp_path, monkeypatch):
    U = _load("upstream_watch", tmp_path, monkeypatch)
    assert U.run(api(True, containing=("v0.23.3+canary.1",)), lambda s, b: True)["stage"] == "merged"


def test_a_failed_send_is_retried_and_api_failures_are_announced_once(tmp_path, monkeypatch):
    U = _load("upstream_watch", tmp_path, monkeypatch)
    sent = []
    U.run(api(True), lambda s, b: False)
    assert U.load()["announced"] == []                                           # not marked: it never arrived
    U.run(api(True), lambda s, b: sent.append(s) or True)
    assert U.load()["announced"] == ["merged"] and len(sent) == 1

    def down(path):
        raise OSError("rate limited")
    for _ in range(5):
        U.run(down, lambda s, b: sent.append(s) or True)
    assert [s for s in sent if "cannot reach" in s] == ["⚠️ Upstream watcher cannot reach GitHub"]
    assert U.load()["announced"] == ["merged"]                                   # a failure changes nothing else
    U.run(api(True), lambda s, b: sent.append(s) or True)
    assert U.load()["fails"] == 0


def test_dry_run_sends_and_saves_nothing(tmp_path, monkeypatch):
    U = _load("upstream_watch", tmp_path, monkeypatch)
    r = U.run(api(True, containing=("v0.23.2",)), lambda s, b: pytest.fail("sent in dry run"), dry_run=True)
    assert r["stage"] == "released" and not (tmp_path / "uw.json").exists()


def test_cost_monitor_counts_direct_api_spend(tmp_path, monkeypatch):
    C = _load("cost_monitor", tmp_path, monkeypatch)
    now = datetime(2026, 11, 16, 21, 0)
    assert C.direct_spend(now) == (0.0, 0.0)                                     # no file yet
    (tmp_path / "assistant").mkdir()
    rows = [{"day": "2026-11-16", "usd": 0.11}, {"day": "2026-11-16", "usd": 0.05}, {"day": "2026-11-03", "usd": 0.40},
            {"day": "2026-10-31", "usd": 9.0}]
    (tmp_path / "assistant" / "spend.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n{broken\n")
    d, m = C.direct_spend(now)
    assert d == pytest.approx(0.16) and m == pytest.approx(0.56)


def test_deploy_ships_the_watcher():
    code = (M.parent / "deploy.sh").read_text()
    assert "upstream_watch.py" in code and "cp scripts/cron/upstream_watch.sh" in code


def test_compute_adds_direct_spend_to_the_chat_agent_total(tmp_path, monkeypatch):
    import sqlite3
    C = _load("cost_monitor", tmp_path, monkeypatch)
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("create table sessions (model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
                "reasoning_tokens, started_at, estimated_cost_usd, billing_provider)")
    con.execute("insert into sessions values ('claude-sonnet-4-6',0,0,0,0,0,?,1.00,'anthropic')", (datetime.now().timestamp(),))
    con.commit(); con.close()
    monkeypatch.setattr(C, "STATE_DB", str(db))
    (tmp_path / "assistant").mkdir()
    (tmp_path / "assistant" / "spend.jsonl").write_text(json.dumps({"day": datetime.now().strftime("%Y-%m-%d"), "usd": 0.25}) + "\n")
    _now, daily, mtd = C.compute()
    assert daily == pytest.approx(1.25) and mtd == pytest.approx(1.25)


def test_cost_log_breaks_spend_down_by_day_and_is_written_once_a_day(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("HERMES_VAULT", str(tmp_path / "vault"))
    C = _load("cost_monitor", tmp_path, monkeypatch)
    now = datetime(2026, 11, 16, 21, 0)
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("create table sessions (source, started_at, actual_cost_usd, estimated_cost_usd)")
    t = now.timestamp()
    con.executemany("insert into sessions values (?,?,?,?)", [
        ("telegram", t - 100, None, 1.13), ("telegram", t - 200, None, 0.20), ("cron", t - 300, None, 0.40),
        ("cli", t - 400, None, 0.45), ("telegram", t - 86400, None, 0.30)])
    con.commit(); con.close()
    monkeypatch.setattr(C, "STATE_DB", str(db))
    (tmp_path / "assistant").mkdir()
    (tmp_path / "assistant" / "spend.jsonl").write_text(json.dumps({"day": "2026-11-16", "usd": 0.16}) + "\n")
    today = C.cost_rows(now)[0]
    assert today["day"] == "2026-11-16" and today["chat"] == pytest.approx(1.33) and today["scheduled"] == pytest.approx(0.40)
    assert today["scripts"] == pytest.approx(0.16) and today["other"] == pytest.approx(0.45)
    assert today["total"] == pytest.approx(2.34) and today["chat_sessions"] == 2 and today["max_session"] == pytest.approx(1.13)
    assert C.write_cost_log(now) is False                                   # 9 PM: not the daily run
    assert C.write_cost_log(now.replace(hour=23)) is True
    text = (tmp_path / "vault" / "09 - Systems" / "Hermes" / "Cost Report.md").read_text()
    assert "| 2026-11-16 | **$2.34** | $1.33 | $0.40 | $0.16 | $0.45 | 2 | $1.13 |" in text
    assert "| 2026-11-15 | **$0.30** |" in text and "**Month to date: $2.64**" in text
    assert "2026-11-10" not in text                                          # empty days are not listed


def test_the_cost_log_prints_the_same_ceilings_the_guard_enforces(tmp_path, monkeypatch):
    C = _load("cost_monitor", tmp_path, monkeypatch)
    spec = importlib.util.spec_from_file_location("sg", M.parent.parent / "plugins" / "spend-guard" / "__init__.py")
    G = importlib.util.module_from_spec(spec); spec.loader.exec_module(G)
    assert (C.DAILY_HARD, C.MONTHLY_HARD) == (G.DAILY_HARD_USD, G.MONTHLY_HARD_USD)
