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
import datetime
import glob
import json
import os
import re
import sys

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("America/Toronto")
except Exception:  # noqa: BLE001 — fall back to naive UTC rather than crash the pipeline
    _TZ = None
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
    # --- provenance / confidence (not measurements) ---
    # sleep_stages_valid: "false" when HAE reported a real total with an all-zero
    #   stage breakdown, so downstream can say "stages unavailable" instead of
    #   reporting a confident zero for deep sleep.
    # last_export_utc / day_complete: HAE exports a RUNNING day-to-date total, so a
    #   day's value is only final once an export arrives after that day ended. Without
    #   this, a 13:14 partial (e.g. 3,838 steps) is indistinguishable from a finished
    #   day, and the coach reasons off it as fact. day_complete=false means "so far",
    #   not "this is the total".
    "sleep_stages_valid", "last_export_utc", "day_complete",
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

# Cumulative daily-TOTAL metrics: they only grow through the day, so the right value
# for a date is the LARGEST one seen, not the last one written. Taking the MAX makes
# the archive robust to (a) a late same-day partial that would otherwise clobber a
# fuller value, and (b) re-sends — given HAE is configured to aggregate by DAY (one
# total per day). The rest (heart rates, rates/averages, sleep) are snapshots and
# keep last-write-wins. NOTE: this only yields correct totals when HAE exports a daily
# total; partial intraday windows can't be reconstructed here (fix is in the HAE app).
CUMULATIVE_COLS = {
    "steps", "active_kcal", "basal_kcal", "exercise_min", "flights_climbed",
    "stand_min", "stand_hours", "walk_distance_km",
}


def _day(s: str) -> str:
    """'2026-04-01 00:00:00 -0400' -> '2026-04-01'."""
    return str(s)[:10]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _export_stamp(path) -> str:
    """The export instant, from the payload FILENAME (e.g.
    '2026-08-10T17-14-08-910203Z.json' -> '2026-08-10T17:14:08Z').

    It cannot come from the data: with Aggregate=Day every point is stamped
    00:00 local, so the point carries no information about WHEN it was sent."""
    stem = Path(path).stem
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})", stem)
    return f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}Z" if m else ""


def _export_local_day(stamp_utc: str) -> str:
    """'2026-08-10T01:52:26Z' -> the America/Toronto CALENDAR DAY it fell on.

    The payload FILENAME is UTC (trailing Z) but every day key in this archive is
    Toronto-local (HAE stamps its points '... -0400'). Comparing the two directly
    marks a day complete up to 4 hours early: an export at 01:52Z is 21:52 the
    PREVIOUS evening in Toronto, i.e. the day was still running. 2026-08-09 hit
    exactly this — last export 2026-08-10T01:52:26Z, genuinely a same-day partial
    (~2h of activity missing), but a naive prefix compare called it complete."""
    if not stamp_utc:
        return ""
    try:
        dt = datetime.datetime.strptime(stamp_utc, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return ""
    return (dt.astimezone(_TZ) if _TZ else dt).date().isoformat()


def process_payload(path: Path, days: dict) -> None:
    payload = json.loads(Path(path).read_text())
    data = payload.get("data", payload)
    stamp = _export_stamp(path)
    for m in data.get("metrics", []):
        name = m.get("name")
        for p in m.get("data", []):
            d = _day(p.get("date", ""))
            if not d:
                continue
            row = days.setdefault(d, {"date": d})
            # Latest export that carried data for this day -> completeness signal.
            if stamp and stamp > str(row.get("last_export_utc") or ""):
                row["last_export_utc"] = stamp
            if name in SIMPLE:
                col, fn = SIMPLE[name]
                q = _f(p.get("qty"))
                if q is not None:
                    v = fn(q)
                    if col in CUMULATIVE_COLS:
                        cur = _f(row.get(col))           # keep the largest daily total seen
                        row[col] = v if cur is None else max(cur, v)
                    else:
                        row[col] = v                     # snapshot → last write wins
            elif name == "heart_rate":
                # Min/Max are daily EXTREMES, so they must aggregate as min()/max()
                # across a day's payloads — not last-write-wins, which let a late
                # partial window (say an evening at rest) overwrite the true daily
                # max recorded during a lift. Avg stays last-write: HAE's own daily
                # average is already computed over the full day, and we have no
                # sample counts to re-weight a mean correctly.
                for k, col, agg in (("Min", "hr_min", min), ("Avg", "hr_avg", None),
                                    ("Max", "hr_max", max)):
                    q = _f(p.get(k))
                    if q is None:
                        continue
                    v = round(q)
                    cur = _f(row.get(col))
                    row[col] = v if (agg is None or cur is None) else agg(round(cur), v)
            elif name == "sleep_analysis":
                # A single night is re-sent across payloads with SHRINKING look-back
                # windows — e.g. 6.25h (full night) → 5.07h → 1.93h → 0.13h (last 40min).
                # Last-write-wins grabbed the 0.13h scrap → garbage sleep. Instead keep
                # the FULLEST record (largest total) for the day, and take its whole stage
                # breakdown together so the stages stay consistent with the headline total.
                # (Summing would be wrong — it'd multi-count the same night.)
                total = _f(p.get("totalSleep"))
                if total is None:  # HAE-version fallback: derive from stages, then asleep
                    stages = [_f(p.get(k)) for k in ("core", "deep", "rem")]
                    stages = [s for s in stages if s is not None]
                    total = sum(stages) if stages else _f(p.get("asleep"))
                prev = _f(row.get("sleep_total_h"))
                # Strictly greater, not >=. On a tie the block re-entered and each
                # stage wrote only `if q is not None`, so a later equal-total record
                # that OMITS a stage key leaves the earlier record's stage in place —
                # a row whose total/start/end come from one record and whose `deep`
                # comes from another. Latent today (observed duplicates are identical)
                # but it is exactly the kind of mixed-provenance row that is
                # impossible to debug later.
                if total is not None and (prev is None or total > prev):
                    row["sleep_total_h"] = round(total, 2)
                    stages = {}
                    for k, col in (("core", "sleep_core_h"), ("deep", "sleep_deep_h"),
                                   ("rem", "sleep_rem_h"), ("awake", "sleep_awake_h"),
                                   ("inBed", "sleep_in_bed_h")):
                        q = _f(p.get(k))
                        if q is not None:
                            stages[col] = round(q, 2)
                    # HAE sometimes reports a real total with every stage at 0 (e.g.
                    # 2026-07-28: totalSleep 2.34 with core=deep=rem=0). Writing those
                    # zeros produces an arithmetically self-contradictory row that reads
                    # downstream as "zero deep sleep" rather than "stages unavailable".
                    # Keep the headline total; drop the bogus breakdown and say so.
                    scored = sum(stages.get(c, 0) for c in
                                 ("sleep_core_h", "sleep_deep_h", "sleep_rem_h"))
                    if scored <= 0 and total > 0:
                        for c in ("sleep_core_h", "sleep_deep_h", "sleep_rem_h"):
                            stages.pop(c, None)
                        row["sleep_stages_valid"] = "false"
                    else:
                        row["sleep_stages_valid"] = "true"
                    row.update(stages)
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


def mark_completeness(days: dict) -> None:
    """Set day_complete per row.

    HAE (Date Range = 'Since Last Sync') exports a RUNNING day-to-date total, so a
    day's numbers are only final once an export arrived AFTER that day ended. A day
    whose last contributing export still falls on the same calendar day is a
    partial — that is why 2026-08-10 reads 3,838 steps at 13:14 and why 14 of the
    last 20 days understate. Rather than guess a correction, label it, so every
    downstream surface can say "so far today" instead of stating a total."""
    for d, row in days.items():
        local_day = _export_local_day(str(row.get("last_export_utc") or ""))
        row["day_complete"] = "true" if (local_day and local_day > d) else "false"


def write_csv(days: dict) -> None:
    mark_completeness(days)
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
