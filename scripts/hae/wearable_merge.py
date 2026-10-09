#!/usr/bin/env python3
"""
wearable_merge.py — build metrics.csv from the per-source archives. The ONLY writer of
metrics.csv (since 2026-10-08).

    <vault>/07 - Health/Metrics/apple.csv    written only by hae_process.py (Apple Health / HAE)
    <vault>/07 - Health/Metrics/fitbit.csv   written only by fitbit_sync.py (Fitbit Air)
    <vault>/07 - Health/Metrics/metrics.csv  DERIVED here; every reader keeps reading it

WHY one derived file instead of overlaying the Air into metrics.csv in place (independent
review, 2026-10-08): hae_process re-loads metrics.csv as its own base and keeps the MAX of
running totals, so an in-place overlay mixed the sources forever (and raw payloads are
pruned at 30 days, so precedence could never be recomputed). Each source now owns its file,
and this pure function can be re-run any time with the same result in any order.

Merge rules (his calls, 2026-10-08):
  * steps: max(Air, Apple/phone); both raw counts kept (steps_air, steps_apple) + steps_source.
  * sleep: the WHOLE block from one source — the Air's when it recorded that night, else
    Apple's. Never a mix (a total from one source with stages from another).
  * resting_hr: Air when present, else Apple; rhr_source says which (the estimators differ,
    so baselines must be computed per source).
  * hrv_ms stays Apple SDNN ONLY. The Air's nightly RMSSD goes to hrv_rmssd_ms — a different
    measure; mixing them would read a change of device as a change in recovery.
  * respiratory_rate: Air (sleep breathing rate) when present, else Apple.
  * Air-only extras in their own columns. Apple-meaning columns (active_kcal, exercise_min,
    vo2_max, …) are never filled from the Air.

stdlib only, Python 3.9-safe, deployed flat beside hae_process.py.
"""
from __future__ import annotations

import contextlib
import csv
import fcntl
import os
from pathlib import Path


def _default_vault() -> Path:
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    return vps if vps.exists() else Path.home() / "Documents" / "School Vault - UofT"


VAULT = _default_vault()
METRICS_DIR = VAULT / "07 - Health" / "Metrics"
APPLE_CSV = METRICS_DIR / "apple.csv"
FITBIT_CSV = METRICS_DIR / "fitbit.csv"
METRICS_CSV = METRICS_DIR / "metrics.csv"
SEED_MIN_ROWS = 20
LOCK_PATH = Path(os.environ.get("HAE_HEALTH_DIR", str(Path.home() / ".hermes" / "health" / "hae"))) / "metrics.lock"

SLEEP_BLOCK = ["sleep_total_h", "sleep_core_h", "sleep_deep_h", "sleep_rem_h", "sleep_awake_h",
               "sleep_in_bed_h", "sleep_start", "sleep_end", "sleep_stages_valid"]
# Columns that exist only because of the Air (appended after the Apple columns).
AIR_COLUMNS = ["steps_apple", "steps_air", "steps_source", "sleep_source", "rhr_source",
               "hrv_rmssd_ms", "azm", "active_min_air", "total_kcal_air", "distance_air_km",
               "vo2_max_air", "spo2_avg", "skin_temp_delta", "resp_source",
               "exercise_air_count", "exercise_air_min", "fb_last_sync"]

FITBIT_COLUMNS = ["date", "steps", "distance_km", "total_kcal", "active_min", "azm",
                  "resting_hr", "hrv_rmssd_ms", "spo2_avg", "resp_rate", "skin_temp_delta",
                  "vo2_max", "sleep_total_h", "sleep_light_h", "sleep_deep_h", "sleep_rem_h",
                  "sleep_awake_h", "sleep_in_bed_h", "sleep_start", "sleep_end",
                  "sleep_stages_valid", "exercise_count", "exercise_min", "fb_last_sync"]


@contextlib.contextmanager
def locked():
    """One exclusive lock for every writer of apple.csv / fitbit.csv / metrics.csv / the
    ingest state. Blocking: writers are short; a reader never takes it (files are swapped
    in atomically, so a reader sees the old or the new file, never a half-written one)."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    # read-only open: works even if another user created the file (append mode would fail)
    fd = os.open(str(LOCK_PATH), os.O_RDONLY | os.O_CREAT, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_rows(path: Path) -> dict:
    """{date: {col: value}} with empty cells dropped; values kept exactly as stored."""
    out = {}
    if path.exists():
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                d = row.get("date")
                if d:
                    out[d] = {k: v for k, v in row.items() if k and v not in ("", None)}
    return out


def write_rows(path: Path, columns: list, rows: dict) -> None:
    """Atomic: write a temp file then os.replace — readers never see a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for d in sorted(rows):
            w.writerow(rows[d])
    os.replace(str(tmp), str(path))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def merge_day(apple: dict, air: dict) -> dict:
    """Pure: one date's merged row from its Apple row and its Air row (either may be {})."""
    row = dict(apple)
    # steps: the larger device total; keep both raw counts
    sa, sx = _num(apple.get("steps")), _num(air.get("steps"))
    if apple.get("steps") is not None:
        row["steps_apple"] = apple["steps"]
    if air.get("steps") is not None:
        row["steps_air"] = air["steps"]
    if sx is not None and (sa is None or sx > sa):
        row["steps"], row["steps_source"] = air["steps"], "air"
    elif sa is not None:
        row["steps_source"] = "apple"
    # sleep: whole block from one source
    if air.get("sleep_total_h") is not None:
        for c in SLEEP_BLOCK:
            row.pop(c, None)
        mapping = {"sleep_total_h": "sleep_total_h", "sleep_core_h": "sleep_light_h",
                   "sleep_deep_h": "sleep_deep_h", "sleep_rem_h": "sleep_rem_h",
                   "sleep_awake_h": "sleep_awake_h", "sleep_in_bed_h": "sleep_in_bed_h",
                   "sleep_start": "sleep_start", "sleep_end": "sleep_end",
                   "sleep_stages_valid": "sleep_stages_valid"}
        for out_col, air_col in mapping.items():
            if air.get(air_col) is not None:
                row[out_col] = air[air_col]
        row["sleep_source"] = "air"
    elif apple.get("sleep_total_h") is not None:
        row["sleep_source"] = "apple"
    # resting HR: Air when present
    if air.get("resting_hr") is not None:
        row["resting_hr"], row["rhr_source"] = air["resting_hr"], "air"
    elif apple.get("resting_hr") is not None:
        row["rhr_source"] = "apple"
    # breathing rate: Air (sleep) when present
    if air.get("resp_rate") is not None:
        row["respiratory_rate"], row["resp_source"] = air["resp_rate"], "air"
    elif apple.get("respiratory_rate") is not None:
        row["resp_source"] = "apple"
    # Air-only extras (never into Apple-meaning columns)
    for out_col, air_col in (("hrv_rmssd_ms", "hrv_rmssd_ms"), ("azm", "azm"),
                             ("active_min_air", "active_min"), ("total_kcal_air", "total_kcal"),
                             ("distance_air_km", "distance_km"), ("vo2_max_air", "vo2_max"),
                             ("spo2_avg", "spo2_avg"), ("skin_temp_delta", "skin_temp_delta"),
                             ("exercise_air_count", "exercise_count"),
                             ("exercise_air_min", "exercise_min"), ("fb_last_sync", "fb_last_sync")):
        if air.get(air_col) is not None:
            row[out_col] = air[air_col]
    return row


def build(apple_rows: dict, air_rows: dict) -> dict:
    """Pure: {date: merged row} over the union of dates."""
    out = {}
    for d in sorted(set(apple_rows) | set(air_rows)):
        r = merge_day(apple_rows.get(d, {}), air_rows.get(d, {}))
        r["date"] = d
        out[d] = r
    return out


def apple_only(row: dict, apple_cols: list) -> dict:
    """Strip anything the merge took from the Air, so seeding apple.csv from a merged
    metrics.csv can never turn Air values into "Apple" ones."""
    r = {k: v for k, v in row.items() if k in apple_cols}
    if row.get("steps_source") == "air":
        if row.get("steps_apple") is not None:
            r["steps"] = row["steps_apple"]
        else:
            r.pop("steps", None)
    if row.get("sleep_source") == "air":
        for c in SLEEP_BLOCK:
            r.pop(c, None)
    if row.get("rhr_source") == "air":
        r.pop("resting_hr", None)
    if row.get("resp_source") == "air":
        r.pop("respiratory_rate", None)
    return r


def ensure_apple_seeded(apple_cols: list) -> None:
    """First run after the split, WHICHEVER script runs first: create apple.csv from the
    (until now Apple-only) metrics.csv. Without this, a Fitbit run before the first Apple
    rebuild would derive metrics.csv from fitbit.csv alone and drop every Apple day."""
    if not APPLE_CSV.exists() and METRICS_CSV.exists():
        src = read_rows(METRICS_CSV)
        if len(src) < SEED_MIN_ROWS:
            # A truncated metrics.csv (the OLD hae_process rewrote it in place) must never
            # become the permanent Apple history (review M4). Refuse loudly instead.
            raise RuntimeError(f"refusing to seed apple.csv from metrics.csv with only {len(src)} rows "
                               f"(< {SEED_MIN_ROWS}); restore metrics.csv from the backup first")
        rows = {d: apple_only(r, apple_cols) for d, r in src.items()}
        write_rows(APPLE_CSV, apple_cols, rows)


def apple_columns() -> list:
    """The Apple column list, owned by hae_process (imported lazily: hae_process imports us)."""
    import hae_process  # noqa: PLC0415
    return list(hae_process.COLUMNS)


def rebuild(apple_cols: list = None) -> int:
    """Re-derive metrics.csv from apple.csv + fitbit.csv. Caller must hold locked()."""
    cols = list(apple_cols or apple_columns())
    ensure_apple_seeded(cols)
    rows = build(read_rows(APPLE_CSV), read_rows(FITBIT_CSV))
    write_rows(METRICS_CSV, cols + [c for c in AIR_COLUMNS if c not in cols], rows)
    return len(rows)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    with locked():
        n = rebuild()
    print(f"metrics.csv rebuilt: {n} days")
