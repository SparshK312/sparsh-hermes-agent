"""plugins/assistant-context + scripts/assistant/cutover.sh (2026-11-16). Mutation-tested."""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
A = ROOT / "scripts" / "assistant"
TZ = ZoneInfo("America/New_York")


def T(h=12, m=0, day=18):
    return datetime.datetime(2026, 11, day, h, m, tzinfo=TZ)


@pytest.fixture
def P(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ASSISTANT_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HERMES_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))            # so the plugin finds the REPO copy
    (tmp_path / "home").mkdir()
    for n in ("outbox", "loops"):
        sys.modules.pop(n, None)
    spec = importlib.util.spec_from_file_location("assistant_context", ROOT / "plugins" / "assistant-context" / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.O, mod.L = mod._assistant()
    mod.clock = [T(15)]
    mod.O.now = lambda: mod.clock[0]
    mod.L.now = lambda: mod.clock[0]
    return mod


def ev(text="yeah she replied", platform="telegram"):
    return SimpleNamespace(text=text, source=SimpleNamespace(platform=platform))


def test_a_reply_gets_the_follow_up_it_answers_even_though_the_stamp_comes_first(P):
    P.O.record("nudge", "loop:acme-1", "I checked your inbox for mail from Dana: nothing. Anything another way?",
               loop_id="acme-1", expects_reply=True, at=T(14))
    assert P._on_inbound(event=ev()) is None                        # never blocks or rewrites
    assert P.O.last_inbound() == T(15)                               # "he wrote" is stamped…
    ctx = P._inject(platform="telegram", is_first_turn=False)["context"]
    assert "[loop:acme-1]" in ctx and "mail from Dana" in ctx       # …and the agent still sees what was asked
    assert "Do NOT bring these up" in ctx
    # a second message in the same conversation keeps the context; after 30 minutes it is gone
    P.clock[0] = T(15, 10); P._on_inbound(event=ev("she said the form is fine"))
    assert "[loop:acme-1]" in P._inject(platform="telegram", is_first_turn=False)["context"]
    P.clock[0] = T(16, 0)
    assert P._inject(platform="telegram", is_first_turn=False) is None


def test_nothing_is_injected_outside_telegram_and_non_telegram_messages_do_not_stamp(P):
    P.O.record("nudge", "lunch", "No lunch logged. What did you have?", at=T(14))
    P._on_inbound(event=ev(platform="cron")); P._on_inbound(event=ev(text="  "))
    assert P.O.last_inbound() is None
    P._on_inbound(event=ev())
    for platform in ("cron", "cli", "", None):
        assert P._inject(platform=platform, is_first_turn=True) is None
    assert P._inject(platform="Platform.TELEGRAM", is_first_turn=False) is not None


def test_open_loops_only_on_the_first_turn_and_the_block_is_bounded(P):
    d = P.L.load()
    for i in range(50):
        P.L.add(d, title=f"Thread number {i} " + "x" * 80, kind="waiting_on", next_step="y" * 200, at=T(9))
    P.L.save(d)
    for i in range(6):
        P.O.record("nudge", f"t{i}", "z" * 2000, at=T(14, i))
    P._on_inbound(event=ev())
    first = P._inject(platform="telegram", is_first_turn=True)["context"]
    later = P._inject(platform="telegram", is_first_turn=False)["context"]
    assert len(first) <= P.MAX_CHARS and first.count("- ") <= P.MAX_FOLLOWUPS + P.MAX_LOOPS
    assert "His open loops" in first and "His open loops" not in later
    assert first.count("zzzz") == 0 or first.count("z" * (P.FOLLOWUP_CHARS + 1)) == 0


def test_answered_follow_ups_are_not_handed_over_again(P):
    P.O.record("nudge", "lunch", "No lunch logged. What did you have?", at=T(13))
    P.O.note_inbound(T(13, 30))                                      # he already answered it
    P.O.record("nudge", "water", "Water?", at=T(14))
    P._on_inbound(event=ev())
    ctx = P._inject(platform="telegram", is_first_turn=False)["context"]
    assert "Water?" in ctx and "No lunch logged" not in ctx


def test_the_plugin_fails_open(P, monkeypatch):
    (Path(os.environ["HERMES_ASSISTANT_DIR"])).mkdir(parents=True, exist_ok=True)
    (Path(os.environ["HERMES_ASSISTANT_DIR"]) / "outbox.jsonl").write_text("{broken\n")
    (Path(os.environ["HERMES_ASSISTANT_DIR"]) / "loops.json").write_text("{broken")
    assert P._on_inbound(event=ev()) is None
    assert P._inject(platform="telegram", is_first_turn=True) is None
    assert P._on_inbound(event=None) is None and P._inject() is None
    monkeypatch.setattr(P, "_assistant", lambda: (_ for _ in ()).throw(ImportError("gone")))
    assert P._on_inbound(event=ev()) is None and P._inject(platform="telegram") is None


def test_deploy_ships_the_plugin_and_the_tick_wrapper():
    code = "\n".join(ln for ln in (ROOT / "scripts" / "deploy.sh").read_text().splitlines() if not ln.strip().startswith("#"))
    assert "cp -r plugins/assistant-context ~/.hermes/plugins/assistant-context" in code
    assert "cp scripts/cron/assistant_tick.sh ~/.hermes/scripts/assistant_tick.sh" in code


# ------------------------------------------------------------------ cutover.sh
NAMES = ["health-lunch-check", "health-meal-rescue", "health-water-check", "health-dinner-check",
         "health-evening-summary-nudge", "health-tracking-dark"]


def _cutover_env(tmp_path, names=NAMES):
    home = tmp_path / "home"
    (home / ".hermes" / "cron").mkdir(parents=True)
    (home / ".hermes" / "hermes-agent" / "venv" / "bin").mkdir(parents=True)
    jobs = [{"id": f"id{i}", "name": n, "state": "scheduled"} for i, n in enumerate(names)]
    jobs.append({"id": "keep", "name": "fitbit-sync", "state": "scheduled"})
    jp = home / ".hermes" / "cron" / "jobs.json"
    jp.write_text(json.dumps({"jobs": jobs}))
    fake = home / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes"      # pause/resume edit jobs.json
    fake.write_text(f'''#!/usr/bin/env python3
import json, sys
p = {str(jp)!r}; d = json.load(open(p))
for j in d["jobs"]:
    if j["id"] == sys.argv[3]:
        j["state"] = "paused" if sys.argv[2] == "pause" else "scheduled"
json.dump(d, open(p, "w"))
''')
    fake.chmod(0o755)
    run = lambda *a: subprocess.run(["bash", str(A / "cutover.sh"), *a], capture_output=True, text=True,   # noqa: E731
                                    env={"HOME": str(home), "PATH": os.environ["PATH"]})
    states = lambda: {j["name"]: j["state"] for j in json.loads(jp.read_text())["jobs"]}                    # noqa: E731
    return home, run, states


def test_cutover_live_pauses_the_six_nudges_first_and_revert_restores_them(tmp_path):
    home, run, states = _cutover_env(tmp_path)
    flag = home / ".hermes" / "assistant" / "LIVE"
    assert "shadow" in run("status").stdout and not flag.exists()
    r = run("live")
    assert r.returncode == 0 and flag.exists() and "LIVE" in r.stdout
    assert all(states()[n] == "paused" for n in NAMES) and states()["fitbit-sync"] == "scheduled"
    r = run("revert")
    assert r.returncode == 0 and not flag.exists()
    assert all(s == "scheduled" for s in states().values())


def test_cutover_refuses_to_go_live_if_an_old_nudge_cannot_be_found(tmp_path):
    home, run, states = _cutover_env(tmp_path, names=NAMES[:-1])       # tracking-dark missing
    r = run("live")
    assert r.returncode == 1 and "not found" in r.stdout
    assert not (home / ".hermes" / "assistant" / "LIVE").exists()      # never live with a nudge unaccounted for
