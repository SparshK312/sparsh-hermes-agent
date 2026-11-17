"""Tests for vault_log.py — the deterministic vault writer the log-* skills call.

Covers the behavior that would corrupt the dashboards/Dataview or silently lose
tracking if it regressed:
  * line-targeted frontmatter edit: add (accumulate) vs set (replace), and that
    every UNRELATED field + the note body stay byte-identical.
  * food: macros accumulate across meals, the Food Log file re-sums its totals
    from the macro lines (not a drifting counter), daily-note macros increment.
  * malformed date is refused (never writes a $(date)-style literal file).

Syntax/compile is covered by test_scripts.py's rglob.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VAULT_DIR = REPO_ROOT / "scripts" / "vault"


def _load_into(tmp_path: Path):
    """Load vault_log fresh and repoint its module-level paths at a temp vault."""
    spec = importlib.util.spec_from_file_location("vault_log", VAULT_DIR / "vault_log.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.VAULT = tmp_path
    mod.DAILY_DIR = tmp_path / "04 - Daily Notes"
    mod.FOODLOG_DIR = tmp_path / "07 - Health" / "Food Log"
    mod.TEMPLATE = tmp_path / "Templates" / "Daily Note.md"
    mod.WORKOUTS_DIR = tmp_path / "07 - Health" / "Workouts"
    mod.WORKOUT_BACKUPS = tmp_path / "_backups"
    mod.DAILY_DIR.mkdir(parents=True, exist_ok=True)
    mod.FOODLOG_DIR.mkdir(parents=True, exist_ok=True)
    # point the pantry (lazy-imported by cmd_food's auto-remember) at the temp vault too
    import sys
    sys.path.insert(0, str(VAULT_DIR))
    import food_pantry
    food_pantry.PANTRY = mod.FOODLOG_DIR / "pantry.json"
    return mod


def _seed_note(mod, date="2026-06-15", extra_body="\n## Notes\nkeep me\n"):
    note = mod.DAILY_DIR / f"{date}.md"
    note.write_text(
        "---\n"
        f'date: "{date}"\n'
        "type: daily\n"
        "weight: \nsleep_hours: \nkcal: \nprotein_g: \ncarbs_g: \nfat_g: \nwater_l: \n"
        "mood: happy\n"
        "---\n"
        f"# {date}{extra_body}",
        encoding="utf-8",
    )
    return note


def _fm(note: Path) -> dict:
    lines = note.read_text().split("\n")
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    out = {}
    for ln in lines[1:end]:
        if ":" in ln and not ln.startswith((" ", "\t", "#")):
            k, v = ln.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _run(mod, argv):
    return mod.main(argv)


def test_water_accumulates_and_preserves_unrelated(tmp_path):
    mod = _load_into(tmp_path)
    note = _seed_note(mod)
    _run(mod, ["water", "--ml", "500", "--date", "2026-06-15"])
    _run(mod, ["water", "--liters", "1", "--date", "2026-06-15"])
    fm = _fm(note)
    assert fm["water_l"] == "1.5"
    assert fm["mood"] == "happy"            # unrelated field untouched
    assert "keep me" in note.read_text()    # body untouched
    assert note.read_text().count("---") == 2  # exactly one frontmatter delimiter pair


def test_weight_and_sleep_set_not_accumulate(tmp_path):
    mod = _load_into(tmp_path)
    note = _seed_note(mod)
    _run(mod, ["weight", "--lb", "165.4", "--date", "2026-06-15"])
    _run(mod, ["weight", "--lb", "166.2", "--date", "2026-06-15"])  # replace, not add
    _run(mod, ["sleep", "--hours", "5.2", "--quality", "6", "--date", "2026-06-15"])
    fm = _fm(note)
    assert fm["weight"] == "166.2"
    assert fm["sleep_hours"] == "5.2"
    assert fm["sleep_quality"] == "6"


def test_food_accumulates_daily_and_resums_foodlog(tmp_path):
    mod = _load_into(tmp_path)
    note = _seed_note(mod)
    _run(mod, ["food", "--meal-type", "lunch",
               "--item", "rice|220|5|45|2", "--item", "cod|180|22|6|7",
               "--source", "mcp:food-tracker", "--date", "2026-06-15"])
    _run(mod, ["food", "--meal-type", "snack", "--kcal", "200", "--protein", "20",
               "--date", "2026-06-15"])
    fm = _fm(note)
    assert fm["kcal"] == "600"          # 400 + 200
    assert fm["protein_g"] == "47"      # 27 + 20
    assert fm["carbs_g"] == "51"        # 51 + 0
    # Food Log file totals re-summed from the two macro lines:
    foodlog = (mod.FOODLOG_DIR / "2026-06-15.md").read_text()
    assert "total_kcal: 600" in foodlog
    assert "total_protein_g: 47" in foodlog
    assert foodlog.count("**Macros:**") == 2


def test_food_totals_flag_wins_over_items(tmp_path):
    mod = _load_into(tmp_path)
    _seed_note(mod)
    # explicit --kcal overrides the (wrong) summed items
    _run(mod, ["food", "--meal-type", "dinner", "--kcal", "900", "--protein", "60",
               "--item", "bowl|123|9|9|9", "--date", "2026-06-15"])
    fm = _fm(mod.DAILY_DIR / "2026-06-15.md")
    assert fm["kcal"] == "900"
    assert fm["protein_g"] == "60"


def test_missing_note_created_from_template(tmp_path):
    mod = _load_into(tmp_path)
    (tmp_path / "Templates").mkdir(parents=True, exist_ok=True)
    mod.TEMPLATE.write_text('---\ndate: "{{date}}"\ntype: daily\nwater_l: \n---\n# {{date:dddd}}\n')
    _run(mod, ["water", "--ml", "250", "--date", "2026-06-20"])
    note = mod.DAILY_DIR / "2026-06-20.md"
    assert note.exists()
    assert 'date: "2026-06-20"' in note.read_text()
    assert _fm(note)["water_l"] == "0.2" or _fm(note)["water_l"] == "0.3"  # round(0.25,1)


def test_malformed_date_refused(tmp_path):
    mod = _load_into(tmp_path)
    with pytest.raises(SystemExit):
        _run(mod, ["water", "--ml", "500", "--date", "$(date +%F)"])


def test_weight_out_of_range_refused(tmp_path):
    mod = _load_into(tmp_path)
    _seed_note(mod)
    with pytest.raises(SystemExit):
        _run(mod, ["weight", "--lb", "12", "--date", "2026-06-15"])


def test_food_coach_nudge_and_pantry_autofill(tmp_path, capsys):
    mod = _load_into(tmp_path)
    _seed_note(mod, date="2026-06-15")
    _run(mod, ["food", "--meal-type", "breakfast",
               "--item", "honey oat cereal bowl|220|4|44|4", "--item", "latte|130|8|13|5",
               "--source", "mcp:food-tracker", "--coach", "--date", "2026-06-15"])
    out = capsys.readouterr().out
    assert "✓ Logged breakfast" in out
    assert "📊" in out and "kcal" in out and "protein" in out.lower()   # coach nudge present
    # pantry auto-filled with both items (so next time they're hits)
    import food_pantry
    assert food_pantry.lookup("honey oat cereal") is not None
    assert food_pantry.lookup("latte")["kcal"] == 130


def test_undo_last_meal_reverses_food_log_and_daily(tmp_path):
    mod = _load_into(tmp_path)
    note = _seed_note(mod, date="2026-06-15")
    _run(mod, ["food", "--meal-type", "lunch", "--kcal", "500", "--protein", "30",
               "--item", "wrap|500|30|40|20", "--date", "2026-06-15"])
    _run(mod, ["food", "--meal-type", "dinner", "--kcal", "800", "--protein", "50",
               "--item", "steak|800|50|10|45", "--date", "2026-06-15"])
    assert _fm(note)["kcal"] == "1300"
    _run(mod, ["undo-last-meal", "--date", "2026-06-15"])    # removes the dinner
    fm = _fm(note)
    assert fm["kcal"] == "500" and fm["protein_g"] == "30"   # back to lunch-only
    foodlog = (mod.FOODLOG_DIR / "2026-06-15.md").read_text()
    assert foodlog.count("**Macros:**") == 1                 # dinner section gone
    assert "total_kcal: 500" in foodlog                       # file totals re-summed


# ===================================================================== 2026-11-16
# Every test below pins a defect found in the Nov 15 audit and fixed Nov 16. Each was
# mutation-tested: put the old behaviour back, the test goes red.
import json as _json

import yaml as _yaml


def _yaml_fm(path: Path) -> dict:
    """Parse the frontmatter the way Obsidian/Dataview does: as real YAML."""
    text = path.read_text(encoding="utf-8")
    return _yaml.safe_load(text.split("---", 2)[1])


EX_PUSH = [{"name": "Incline DB Press", "sets": [{"weight_lb": 30, "reps": 10}, {"weight_lb": 30, "reps": 9}]},
           {"name": "Farmer's walk", "notes": 'said "heavy": 2 laps', "sets": [{"weight_lb": 50, "reps": 1, "side": "L, R"}]}]


def _workout(mod, tmp_path, exercises, *extra, split="Push", date="2026-06-15"):
    f = tmp_path / "ex.json"
    f.write_text(_json.dumps(exercises), encoding="utf-8")
    return _run(mod, ["workout", "--exercises-file", str(f), "--split", split, "--date", date, *extra])


def test_split_with_a_colon_keeps_the_daily_note_valid_yaml(tmp_path):
    mod = _load_into(tmp_path)
    note = _seed_note(mod)
    _workout(mod, tmp_path, EX_PUSH, split="Push: chest/shoulders")
    fm = _yaml_fm(note)                                    # ScannerError before the fix
    assert fm["lifted"] == "Push: chest/shoulders"
    assert fm["mood"] == "happy"
    # a second, different session the same day appends and stays valid
    _workout(mod, tmp_path, [{"name": "Treadmill", "sets": [{"weight_lb": None, "reps": 1}]}],
             "--append", split="Cardio")
    assert _yaml_fm(note)["lifted"] == "Push: chest/shoulders + Cardio"


def test_workout_file_round_trips_through_yaml_and_our_own_parser(tmp_path):
    mod = _load_into(tmp_path)
    _seed_note(mod)
    _workout(mod, tmp_path, EX_PUSH)
    wf = mod.WORKOUTS_DIR / "2026-06-15.md"
    assert _yaml_fm(wf)["exercises"][1]["name"] == "Farmer's walk"
    assert _yaml_fm(wf)["exercises"][1]["sets"][0]["side"] == "L, R"
    exists, parsed = mod.read_workout_exercises("2026-06-15")
    assert exists and parsed == EX_PUSH


def test_workout_replace_that_drops_logged_sets_is_refused(tmp_path):
    mod = _load_into(tmp_path)
    _seed_note(mod)
    _workout(mod, tmp_path, EX_PUSH)
    before = (mod.WORKOUTS_DIR / "2026-06-15.md").read_text()
    with pytest.raises(SystemExit) as e:                    # forgot to re-send the earlier work
        _workout(mod, tmp_path, [{"name": "Lateral Raise", "sets": [{"weight_lb": 15, "reps": 12}]}])
    assert "REFUSED" in str(e.value) and "Incline DB Press" in str(e.value)
    with pytest.raises(SystemExit):                         # fewer sets of a logged exercise
        _workout(mod, tmp_path, [dict(EX_PUSH[0], sets=EX_PUSH[0]["sets"][:1]), EX_PUSH[1]])
    assert (mod.WORKOUTS_DIR / "2026-06-15.md").read_text() == before
    # a deliberate correction still works
    _workout(mod, tmp_path, [EX_PUSH[0]], "--allow-shrink")
    assert mod.read_workout_exercises("2026-06-15")[1] == [EX_PUSH[0]]


def test_workout_append_merges_and_backs_up(tmp_path):
    mod = _load_into(tmp_path)
    _seed_note(mod)
    _workout(mod, tmp_path, EX_PUSH)
    _workout(mod, tmp_path, [{"name": "incline db press", "sets": [{"weight_lb": 30, "reps": 8}]},
                             {"name": "Lateral Raise", "sets": [{"weight_lb": 15, "reps": 12}]}], "--append")
    got = mod.read_workout_exercises("2026-06-15")[1]
    assert [e["name"] for e in got] == ["Incline DB Press", "Farmer's walk", "Lateral Raise"]
    assert [s["reps"] for s in got[0]["sets"]] == [10, 9, 8]
    assert len(list(mod.WORKOUT_BACKUPS.glob("2026-06-15.*.md"))) == 1   # the pre-append file


def test_unparseable_workout_file_is_never_treated_as_empty(tmp_path):
    mod = _load_into(tmp_path)
    _seed_note(mod)
    mod.WORKOUTS_DIR.mkdir(parents=True)
    hand = mod.WORKOUTS_DIR / "2026-06-15.md"
    hand.write_text("---\ntype: workout\nexercises:\n  - Bench 3x8\n---\nmy notes\n")
    with pytest.raises(SystemExit):
        _workout(mod, tmp_path, EX_PUSH)
    with pytest.raises(SystemExit):
        _workout(mod, tmp_path, EX_PUSH, "--append")
    assert "my notes" in hand.read_text()


def test_vitamins_merge_across_calls_and_stay_valid_yaml(tmp_path):
    mod = _load_into(tmp_path)
    note = _seed_note(mod)
    _run(mod, ["vitamins", "--supplements", "Zinc, Iron", "--date", "2026-06-15"])
    _run(mod, ["vitamins", "--supplements", "Magnesium: 5 g, iron", "--date", "2026-06-15"])
    fm = _yaml_fm(note)
    assert fm["supplements_today"] == ["Zinc", "Iron", "Magnesium: 5 g"]   # was [Magnesium…] only
    assert fm["vitamins_taken"] is True
    _run(mod, ["vitamins", "--date", "2026-06-15"])                              # bare call keeps the list
    assert _yaml_fm(note)["supplements_today"] == ["Zinc", "Iron", "Magnesium: 5 g"]


def _three_meals(mod):
    for t, mt, k in (("8:00 AM", "breakfast", 400), ("1:30 PM", "lunch", 700), ("7:00 PM", "dinner", 900)):
        _run(mod, ["food", "--meal-type", mt, "--time", t, "--kcal", str(k), "--protein", "30",
                   "--carbs", "40", "--fat", "10", "--date", "2026-06-15", "--no-remember"])


def test_remove_meal_by_time_fixes_a_meal_that_is_not_the_last(tmp_path):
    mod = _load_into(tmp_path)
    note = _seed_note(mod)
    _three_meals(mod)
    _run(mod, ["remove-meal", "--time", "1:30 pm", "--date", "2026-06-15"])
    text = (mod.FOODLOG_DIR / "2026-06-15.md").read_text()
    assert "lunch" not in text and "breakfast" in text and "dinner" in text
    assert "total_kcal: 1300" in text
    assert _fm(note)["kcal"] == "1300" and _fm(note)["protein_g"] == "60"
    _run(mod, ["remove-meal", "--index", "1", "--date", "2026-06-15"])
    assert _fm(note)["kcal"] == "900"
    assert "breakfast" not in (mod.FOODLOG_DIR / "2026-06-15.md").read_text()


def test_remove_meal_refuses_an_ambiguous_or_missing_target(tmp_path):
    mod = _load_into(tmp_path)
    note = _seed_note(mod)
    _three_meals(mod)
    for argv in (["--time", "3:00 PM"], ["--index", "9"], []):
        with pytest.raises(SystemExit):
            _run(mod, ["remove-meal", *argv, "--date", "2026-06-15"])
    assert _fm(note)["kcal"] == "2000"


def test_pantry_failure_is_reported_not_swallowed(tmp_path, capsys):
    mod = _load_into(tmp_path)
    _seed_note(mod)
    (mod.FOODLOG_DIR / "pantry.json").write_text("{broken")
    _run(mod, ["food", "--meal-type", "snack", "--item", "egg|78|6|0|5", "--date", "2026-06-15"])
    out = capsys.readouterr().out
    assert "✓ Logged snack" in out and "food memory was not updated" in out
    assert (mod.FOODLOG_DIR / "pantry.json").read_text() == "{broken"
