"""Behavior tests for the fitness volume math — pure transforms that drive a
user-facing weekly card, previously untested (a wrong-but-plausible number would
have shipped green). Covers:

  * status()/_bucket() landmark bucketing
  * parse_workouts: set counting, fractional secondary-muscle credit, mapping
  * an empty "total_sets: 0" stub must NOT count as a workout
  * an unmapped exercise is surfaced (not silently dropped) and adds no volume

Rendering (cairosvg/matplotlib) is intentionally out of scope here — the math is
what must be correct; the render is a presentation concern.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

FIT = Path(__file__).resolve().parent.parent / "scripts" / "fitness"
sys.path.insert(0, str(FIT))

import muscle_volume as mv      # noqa: E402
import muscle_heatmap as mh     # noqa: E402


def test_status_buckets():
    lm = (10, 12, 20, 22)  # MEV, MAV_lo, MAV_hi, MRV
    assert mv.status(5, lm)[0] == "under MEV"
    assert mv.status(11, lm)[0] == "maintenance"
    assert mv.status(15, lm)[0] == "productive"
    assert mv.status(21, lm)[0] == "high"
    assert mv.status(25, lm)[0] == "OVER MRV"


def test_bucket_vs_own_landmarks():
    # Chest landmarks are (10, 12, 20, 22)
    assert mh._bucket("Chest", {}) == "gap"                  # 0 sets
    assert mh._bucket("Chest", {"Chest": 5}) == "grow"       # below MEV
    assert mh._bucket("Chest", {"Chest": 15}) == "solid"     # in band
    assert mh._bucket("Chest", {"Chest": 25}) == "high"      # above MAV_hi


def _write_workout(tmp_path, body: str) -> str:
    wdir = tmp_path / "07 - Health" / "Workouts"
    wdir.mkdir(parents=True, exist_ok=True)
    today = datetime.datetime.now(mv.TZ).date().isoformat()
    (wdir / f"{today}.md").write_text(
        f"---\ntype: workout\ndate: {today}\n{body}\n---\n\n# workout\n")
    mv.WORKOUTS = wdir   # redirect the module's workout source at the tmp vault
    return today


def test_parse_counts_sets_and_fractional_secondary(tmp_path):
    body = (
        "exercises:\n"
        "  - name: Pec Deck\n"
        "    sets:\n"
        "      - {weight_lb: 85, reps: null}\n"
        "      - {weight_lb: 85, reps: null}\n"
        "  - name: Lat Pulldown\n"
        "    sets:\n"
        "      - {weight_lb: 85, reps: null}\n"
    )
    _write_workout(tmp_path, body)
    vol, n, unmapped, _, _ = mv.parse_workouts(7)
    assert n == 1
    assert vol["Chest"] == 2.0     # Pec Deck → Chest primary ×2
    assert vol["Lats"] == 1.0      # Lat Pulldown → Lats primary ×1
    assert vol["Biceps"] == 0.5    # Lat Pulldown → Biceps secondary (0.5 credit)
    assert unmapped == []


def test_empty_stub_is_not_counted(tmp_path):
    _write_workout(tmp_path, "total_sets: 0\nexercises:\n  - name: Pec Deck\n    sets: []\n")
    vol, n, unmapped, _, _ = mv.parse_workouts(7)
    assert n == 0                  # no sets logged → not a workout
    assert sum(vol.values()) == 0


def test_unmapped_exercise_surfaced_not_dropped(tmp_path):
    body = ("exercises:\n  - name: Atlantean Deltoid Flux\n    sets:\n"
            "      - {weight_lb: 10, reps: null}\n")
    _write_workout(tmp_path, body)
    vol, n, unmapped, _, _ = mv.parse_workouts(7)
    assert n == 1                              # it IS a workout (a set was logged)
    assert "Atlantean Deltoid Flux" in unmapped
    assert sum(vol.values()) == 0             # but contributes no mapped volume


def test_smith_press_family_is_mapped(tmp_path):
    """Regression: 'Incline Smith Machine Press' was silently dropping chest volume."""
    body = ("exercises:\n  - name: Incline Smith Machine Press\n    sets:\n"
            "      - {weight_lb: 40, reps: null}\n"
            "      - {weight_lb: 40, reps: null}\n"
            "      - {weight_lb: 40, reps: null}\n")
    _write_workout(tmp_path, body)
    vol, n, unmapped, _, _ = mv.parse_workouts(7)
    assert unmapped == []
    assert vol["Chest"] == 3.0
    assert vol["Triceps"] == 1.5   # secondary 0.5 ×3
