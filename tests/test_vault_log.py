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
    _run(mod, ["weight", "--lb", "117.8", "--date", "2026-06-15"])
    _run(mod, ["weight", "--lb", "118.2", "--date", "2026-06-15"])  # replace, not add
    _run(mod, ["sleep", "--hours", "5.2", "--quality", "6", "--date", "2026-06-15"])
    fm = _fm(note)
    assert fm["weight"] == "118.2"
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
               "--item", "lucky charms bowl|220|4|44|4", "--item", "latte|130|8|13|5",
               "--source", "mcp:food-tracker", "--coach", "--date", "2026-06-15"])
    out = capsys.readouterr().out
    assert "✓ Logged breakfast" in out
    assert "📊" in out and "kcal" in out and "protein" in out.lower()   # coach nudge present
    # pantry auto-filled with both items (so next time they're hits)
    import food_pantry
    assert food_pantry.lookup("lucky charms") is not None
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
