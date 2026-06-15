"""Behavior tests for the Phase 4b coach engine — the deterministic + guardrail
layer that decides what the coach says and whether it fires. Pure/offline.

Covers: the structured-output validator (anti-hallucination / anti-generic),
the midday intake-pace math, per-lift progression parsing, and the message
budget (quiet hours / daily cap / dedup).
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

FIT = Path(__file__).resolve().parent.parent / "scripts" / "fitness"
sys.path.insert(0, str(FIT))

import coach_engine as E  # noqa: E402

TZ = ZoneInfo("America/Toronto")


# ---- validator ----
def _ev():
    return {"nutrition": {"avg_kcal": 1985}, "training": {"sessions_done": 3}}


def test_validator_accepts_grounded():
    p = {"analysis": {"primary_bottleneck": "under-eating", "evidence": ["avg 1985 kcal", "3 sessions"],
                      "risk_level": "red"},
         "actions": {"today": ["hit 2400 kcal"], "fallback_if_busy": "shake"},
         "conversation": {"question_for_user": "what broke?"}}
    ok, _ = E.validate(p, _ev())
    assert ok


def test_validator_rejects_ungrounded_number():
    p = {"analysis": {"primary_bottleneck": "x", "evidence": ["you ate 999 kcal"]},
         "actions": {"today": ["a"]}, "conversation": {"question_for_user": "q"}}
    ok, reason = E.validate(p, _ev())
    assert not ok and "999" in reason


def test_validator_rejects_too_many_actions():
    p = {"analysis": {"primary_bottleneck": "x", "evidence": []},
         "actions": {"today": ["a", "b", "c"]}, "conversation": {"question_for_user": "q"}}
    assert not E.validate(p, _ev())[0]


def test_validator_rejects_missing_question():
    p = {"analysis": {"primary_bottleneck": "x", "evidence": []},
         "actions": {"today": ["a"]}, "conversation": {}}
    assert not E.validate(p, _ev())[0]


# ---- intake pace (midday rescue trigger) ----
def test_intake_pace_flags_behind(monkeypatch):
    monkeypatch.setattr(E, "now", lambda: datetime.datetime(2026, 6, 15, 15, 0, tzinfo=TZ))
    monkeypatch.setattr(E, "todays_intake", lambda: {"kcal": 0, "protein_g": 0, "meals": []})
    p = E.intake_pace()
    assert p["behind_kcal"] and p["behind_protein"]
    assert p["kcal_expected_by_now"] > 0          # 7am-11pm window, 3pm ≈ half the day
    assert p["kcal_so_far"] == 0


def test_intake_pace_on_track_not_flagged(monkeypatch):
    monkeypatch.setattr(E, "now", lambda: datetime.datetime(2026, 6, 15, 15, 0, tzinfo=TZ))
    monkeypatch.setattr(E, "todays_intake", lambda: {"kcal": 1500, "protein_g": 90, "meals": ["breakfast", "lunch"]})
    p = E.intake_pace()
    assert not p["behind_kcal"] and not p["behind_protein"]


# ---- per-lift progression ----
def test_lift_progression_up_flat_down(tmp_path, monkeypatch):
    wdir = tmp_path / "07 - Health" / "Workouts"
    wdir.mkdir(parents=True)

    def w(date, weight):
        (wdir / f"{date}.md").write_text(
            f"---\ntype: workout\ndate: {date}\nexercises:\n  - name: Bench Press\n    sets:\n"
            f"      - {{weight_lb: {weight}, reps: null}}\n---\n")
    w("2026-06-10", 95)
    w("2026-06-13", 100)   # later session, heavier → up
    monkeypatch.setattr(E, "WORKOUTS", wdir)
    monkeypatch.setattr(E, "now", lambda: datetime.datetime(2026, 6, 15, 12, 0, tzinfo=TZ))
    prog = {p["lift"]: p for p in E.lift_progression(14)}
    assert prog["bench press"]["status"] == "up"
    assert prog["bench press"]["last_top_lb"] == 100.0
    assert prog["bench press"]["prev_top_lb"] == 95.0


# ---- message budget ----
def test_budget_quiet_hours(monkeypatch):
    monkeypatch.setattr(E, "now", lambda: datetime.datetime(2026, 6, 15, 23, 30, tzinfo=TZ))
    ok, reason = E.budget_ok("meal-rescue")
    assert not ok and "quiet" in reason


def test_budget_daily_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(E, "STATE", tmp_path / "coach_state.json")
    monkeypatch.setattr(E, "now", lambda: datetime.datetime(2026, 6, 15, 14, 0, tzinfo=TZ))
    assert E.budget_ok("a")[0]
    E.record_send("a")
    E.record_send("b")
    assert not E.budget_ok("c")[0]   # 2 already sent today (normal cap)
    assert E.budget_ok("c", red_alert=True)[0]   # red-alert cap is 3
