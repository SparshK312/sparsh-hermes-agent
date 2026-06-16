"""Tests for the HAE wearable-bridge pipeline (Phase 3).

Covers the logic that would silently corrupt data if it regressed:
  * hae_process: kJ->kcal conversion, sleep-stage extraction, heart-rate min/avg/max,
    daily-grouped point -> per-day row.
  * hae_daily_ingest: line-targeted frontmatter edit preserves the body + every
    existing field and keeps exactly two frontmatter delimiters.

Syntax/compile of the scripts is already covered by test_scripts.py (rglob).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HAE_DIR = REPO_ROOT / "scripts" / "hae"


def _load(mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, HAE_DIR / f"{mod_name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_process_payload_parses_daily_metrics(tmp_path):
    proc = _load("hae_process")
    payload = {
        "data": {
            "metrics": [
                {"name": "step_count", "units": "count",
                 "data": [{"date": "2026-04-01 00:00:00 -0400", "qty": 12374.1}]},
                {"name": "active_energy", "units": "kJ",
                 "data": [{"date": "2026-04-01 00:00:00 -0400", "qty": 1569.25}]},
                {"name": "heart_rate", "units": "count/min",
                 "data": [{"date": "2026-04-01 00:00:00 -0400", "Min": 48, "Avg": 76.4, "Max": 142}]},
                {"name": "sleep_analysis", "units": "hr",
                 "data": [{"date": "2026-04-14 00:00:00 -0400", "totalSleep": 5.64,
                           "core": 4.69, "deep": 0.125, "rem": 0.825, "awake": 1.1,
                           "inBed": 5.64, "sleepStart": "2026-04-14 00:16 -0400",
                           "sleepEnd": "2026-04-14 07:00 -0400"}]},
            ]
        }
    }
    f = tmp_path / "p.json"
    f.write_text(json.dumps(payload))
    days: dict = {}
    proc.process_payload(f, days)

    a = days["2026-04-01"]
    assert a["steps"] == 12374                       # round
    assert a["active_kcal"] == round(1569.25 / 4.184)  # kJ -> kcal == 375
    assert a["hr_min"] == 48 and a["hr_avg"] == 76 and a["hr_max"] == 142

    s = days["2026-04-14"]
    assert s["sleep_total_h"] == 5.64
    assert s["sleep_deep_h"] == 0.12 or s["sleep_deep_h"] == 0.13  # round(0.125, 2)
    assert s["sleep_rem_h"] == 0.82 or s["sleep_rem_h"] == 0.83
    assert s["sleep_start"].startswith("2026-04-14")


def test_cumulative_metrics_take_max_not_last(tmp_path):
    """Steps/active-energy are daily TOTALS — across same-day payloads the archive must
    keep the LARGEST (a late partial must not clobber a fuller value). Snapshots (RHR)
    still take the last value."""
    proc = _load("hae_process")

    def payload(steps, akcal_kj, rhr):
        return {"data": {"metrics": [
            {"name": "step_count", "data": [{"date": "2026-06-15 10:00:00 -0400", "qty": steps}]},
            {"name": "active_energy", "units": "kJ", "data": [{"date": "2026-06-15 10:00:00 -0400", "qty": akcal_kj}]},
            {"name": "resting_heart_rate", "data": [{"date": "2026-06-15 10:00:00 -0400", "qty": rhr}]},
        ]}}

    days = {}
    import json as _j
    for steps, kj, rhr in [(8000, 4000, 52), (14000, 7000, 55), (12000, 6500, 50)]:
        f = tmp_path / f"p_{steps}.json"
        f.write_text(_j.dumps(payload(steps, kj, rhr)))
        proc.process_payload(f, days)

    row = days["2026-06-15"]
    assert row["steps"] == 14000                       # MAX, not the last (12000)
    assert row["active_kcal"] == round(7000 / 4.184)   # MAX active energy
    assert row["resting_hr"] == 50                      # snapshot → last write wins


def test_daily_ingest_frontmatter_is_line_targeted(tmp_path):
    ing = _load("hae_daily_ingest")
    note = tmp_path / "2026-04-01.md"
    note.write_text(
        '---\n'
        'date: "2026-04-01"\n'
        'type: daily\n'
        'sleep_hours: \n'
        'kcal: 1308\n'
        '\n'                       # a blank line mid-frontmatter (real notes have these)
        'water_l: 0.5\n'
        '---\n'
        '\n# Body\n\nsome text with --- a dash inside\n'
    )
    changed, _ = ing.update_frontmatter(
        note, {"sleep_hours": "6.46", "steps": "8420"}, {}, ing.SLEEP_FIELDS)
    assert changed == 2
    out = note.read_text()
    # existing values preserved
    assert 'date: "2026-04-01"' in out
    assert "kcal: 1308" in out
    assert "water_l: 0.5" in out
    # sleep_hours updated in place (not duplicated)
    assert out.count("sleep_hours:") == 1
    assert "sleep_hours: 6.46" in out
    # new field appended
    assert "steps: 8420" in out
    # body preserved
    assert "# Body" in out and "some text" in out
    # exactly two frontmatter delimiters (the body dash is not line-anchored '---')
    assert out.count("\n---\n") == 1 and out.startswith("---\n")

    # idempotent: re-running with the same values changes nothing
    changed2, _ = ing.update_frontmatter(
        note, {"sleep_hours": "6.46", "steps": "8420"}, {}, ing.SLEEP_FIELDS)
    assert changed2 == 0


def test_daily_ingest_never_clobbers_manual_sleep_edit(tmp_path):
    """A hand-corrected sleep value must survive the next sync; accumulating
    activity must still update."""
    ing = _load("hae_daily_ingest")
    note = tmp_path / "2026-04-02.md"
    note.write_text("---\ntype: daily\nsleep_hours: 7.5\nsteps: 5000\n---\n\n# Body\n")

    # HAE last wrote sleep 6.5 but the note now says 7.5 (user corrected it);
    # archive still says 6.5 and steps climbed to 8000.
    prev = {"sleep_hours": "6.5"}
    changed, written = ing.update_frontmatter(
        note, {"sleep_hours": "6.5", "steps": "8000"}, prev, ing.SLEEP_FIELDS)
    out = note.read_text()
    assert "sleep_hours: 7.5" in out          # manual correction protected
    assert "steps: 8000" in out               # activity still accumulates
    assert "sleep_hours" not in written       # HAE no longer owns the edited field

    # HAE refreshes its OWN unchanged sleep value (no manual edit since last write)
    note.write_text("---\ntype: daily\nsleep_hours: 6.5\n---\n\n# Body\n")
    ing.update_frontmatter(note, {"sleep_hours": "6.7"}, {"sleep_hours": "6.5"}, ing.SLEEP_FIELDS)
    assert "sleep_hours: 6.7" in note.read_text()
