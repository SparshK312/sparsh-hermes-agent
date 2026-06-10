#!/usr/bin/env python3
"""
hae_process.py — turn HAE daily-grouped payloads into a tidy per-day metrics CSV.

Reads one or more HAE REST payloads (Summarize ON + Time Grouping = Day, so each
metric already carries one value per day) and upserts a row-per-day archive at:

    <VAULT>/07 - Health/Metrics/metrics.csv

Idempotent: re-running merges by date (a newer payload's value for a given day
overwrites the older one). Safe to run on every cron tick.

USAGE
    hae_process.py                 # process every raw payload in HAE_HEALTH_DIR/raw
    hae_process.py <file.json> ... # process specific payload file(s)

Unit handling
    active_energy / basal_energy are exported in kJ -> converted to kcal (/4.184).
    Everything else is kept in HAE's native unit.

This script writes ONLY the CSV archive (surface-agnostic: feeds charts, a web
app, a Telegram bot, whatever). Daily-note frontmatter is written separately by
the morning/evening ingest so the two concerns stay decoupled.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys
from pathlib import Path

KJ_PER_KCAL = 4.184
HEALTH_DIR = Path(os.environ.get("HAE_HEALTH_DIR", str(Path.home() / ".hermes" / "health" / "hae")))


def _default_vault() -> Path:
    """HERMES_VAULT wins; else the VPS path if it exists (production), else the
    Mac dev path — same code in both places, no split-brain."""
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    if vps.exists():
        return vps
    return Path.home() / "Documents" / "School Vault - UofT"


VAULT = _default_vault()
CSV_PATH = VAULT / "07 - Health" / "Metrics" / "metrics.csv"

# Column order for the CSV. date first, then the Tier-1 accountability core,
# then the secondary archive metrics. Adding a metric here is the only change
# needed to start capturing it.
COLUMNS = [
    "date",
    # --- Tier 1: sleep ---
    "sleep_total_h", "sleep_core_h", "sleep_deep_h", "sleep_rem_h", "sleep_awake_h",
    "sleep_in_bed_h", "sleep_start", "sleep_end",
    # --- Tier 1: activity / cardio ---
    "steps", "active_kcal", "basal_kcal", "exercise_min",
    "resting_hr", "hrv_ms", "vo2_max",
    # --- heart rate band ---
    "hr_min", "hr_avg", "hr_max", "walking_hr_avg", "cardio_recovery",
    # --- secondary archive ---
    "respiratory_rate", "flights_climbed", "stand_min", "stand_hours",
    "walk_distance_km", "walk_speed_kmh", "walk_step_len_cm",
    "walk_asymmetry_pct", "walk_double_support_pct",
    "stair_speed_up", "stair_speed_down", "physical_effort",
    "env_audio_db", "headphone_audio_db", "six_min_walk_m",
]

# Simple metrics: HAE metric name -> (csv column, transform(qty) -> value)
def _kj_to_kcal(q):
    return round(q / KJ_PER_KCAL)

SIMPLE = {
    "step_count":                        ("steps", lambda q: round(q)),
    "active_energy":                     ("active_kcal", _kj_to_kcal),
    "basal_energy_burned":               ("basal_kcal", _kj_to_kcal),
    "apple_exercise_time":               ("exercise_min", lambda q: round(q)),
    "resting_heart_rate":                ("resting_hr", lambda q: round(q)),
    "heart_rate_variability":            ("hrv_ms", lambda q: round(q)),
    "vo2_max":                           ("vo2_max", lambda q: round(q, 1)),
    "walking_heart_rate_average":        ("walking_hr_avg", lambda q: round(q)),
    "cardio_recovery":                   ("cardio_recovery", lambda q: round(q)),
    "respiratory_rate":                  ("respiratory_rate", lambda q: round(q, 1)),
    "flights_climbed":                   ("flights_climbed", lambda q: round(q)),
    "apple_stand_time":                  ("stand_min", lambda q: round(q)),
    "apple_stand_hour":                  ("stand_hours", lambda q: round(q)),
    "walking_running_distance":          ("walk_distance_km", lambda q: round(q, 2)),
    "walking_speed":                     ("walk_speed_kmh", lambda q: round(q, 2)),
    "walking_step_length":               ("walk_step_len_cm", lambda q: round(q, 1)),
    "walking_asymmetry_percentage":      ("walk_asymmetry_pct", lambda q: round(q, 1)),
    "walking_double_support_percentage": ("walk_double_support_pct", lambda q: round(q, 1)),
    "stair_speed_up":                    ("stair_speed_up", lambda q: round(q, 2)),
    "stair_speed_down":                  ("stair_speed_down", lambda q: round(q, 2)),
    "physical_effort":                   ("physical_effort", lambda q: round(q, 2)),
    "environmental_audio_exposure":      ("env_audio_db", lambda q: round(q, 1)),
    "headphone_audio_exposure":          ("headphone_audio_db", lambda q: round(q, 1)),
    "six_minute_walking_test_distance":  ("six_min_walk_m", lambda q: round(q, 1)),
}


def _day(s: str) -> str:
    """'2026-04-01 00:00:00 -0400' -> '2026-04-01'."""
    return str(s)[:10]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def process_payload(path: Path, days: dict) -> None:
    payload = json.loads(Path(path).read_text())
    data = payload.get("data", payload)
    for m in data.get("metrics", []):
        name = m.get("name")
        for p in m.get("data", []):
            d = _day(p.get("date", ""))
            if not d:
                continue
            row = days.setdefault(d, {"date": d})
            if name in SIMPLE:
                col, fn = SIMPLE[name]
                q = _f(p.get("qty"))
                if q is not None:
                    row[col] = fn(q)
            elif name == "heart_rate":
                for k, col in (("Min", "hr_min"), ("Avg", "hr_avg"), ("Max", "hr_max")):
                    q = _f(p.get(k))
                    if q is not None:
                        row[col] = round(q)
            elif name == "sleep_analysis":
                for k, col, nd in (
                    ("totalSleep", "sleep_total_h", 2), ("core", "sleep_core_h", 2),
                    ("deep", "sleep_deep_h", 2), ("rem", "sleep_rem_h", 2),
                    ("awake", "sleep_awake_h", 2), ("inBed", "sleep_in_bed_h", 2),
                ):
                    q = _f(p.get(k))
                    if q is not None:
                        row[col] = round(q, nd)
                if p.get("sleepStart"):
                    row["sleep_start"] = str(p["sleepStart"])
                if p.get("sleepEnd"):
                    row["sleep_end"] = str(p["sleepEnd"])


def load_existing() -> dict:
    days = {}
    if CSV_PATH.exists():
        with CSV_PATH.open(newline="") as fh:
            for row in csv.DictReader(fh):
                d = row.get("date")
                if d:
                    days[d] = {k: v for k, v in row.items() if v not in ("", None)}
    return days


def write_csv(days: dict) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for d in sorted(days):
            w.writerow(days[d])


def main(argv) -> int:
    files = [Path(a) for a in argv] or sorted(
        Path(p) for p in glob.glob(str(HEALTH_DIR / "raw" / "*.json"))
    )
    if not files:
        print("no payloads to process", file=sys.stderr)
        return 1
    days = load_existing()
    before = len(days)
    skipped = 0
    for f in files:
        try:
            process_payload(f, days)
        except Exception as e:  # noqa: BLE001 — one bad payload must not abort the rebuild
            skipped += 1
            print(f"skip {Path(f).name}: {e}", file=sys.stderr)
    write_csv(days)
    print(f"processed {len(files) - skipped} payload(s) -> {CSV_PATH}"
          + (f" ({skipped} skipped)" if skipped else ""))
    print(f"days in archive: {len(days)} (was {before})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
