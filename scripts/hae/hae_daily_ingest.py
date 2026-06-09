#!/usr/bin/env python3
"""
hae_daily_ingest.py — write a day's HAE metrics into its daily-note frontmatter.

Reads the per-day archive (metrics.csv) and upserts the Tier-1 health fields into
the daily note's YAML frontmatter, preserving every existing field + the body.

Design:
  * LINE-TARGETED edit (not a YAML reflow): only the HAE fields are touched; all
    other frontmatter lines stay byte-identical (quoted date, blank lines, manual
    entries). No pyyaml dependency -> runs on system python3.
  * Idempotent: re-running sets the same values. Safe to call many times a day.
  * Sleep is the watch's source of truth, but is only written on nights the watch
    actually recorded sleep -> watch-less nights keep any manual /log_sleep value.
  * Activity (steps/active_kcal/exercise) accumulates through the day; running this
    in the evening captures the full-day totals.

USAGE
  hae_daily_ingest.py            # target = today (America/Toronto)
  hae_daily_ingest.py 2026-06-01 # target a specific date (for backfill)
"""
from __future__ import annotations

import csv
import datetime
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

VAULT = Path(os.environ.get("HERMES_VAULT", "/home/hermes/vault"))
CSV_PATH = VAULT / "07 - Health" / "Metrics" / "metrics.csv"
DAILY_DIR = VAULT / "04 - Daily Notes"
TZ = ZoneInfo("America/Toronto")

# metrics.csv column -> daily-note frontmatter field
FIELD_MAP = {
    "sleep_total_h": "sleep_hours",
    "sleep_rem_h":   "sleep_rem_h",
    "sleep_deep_h":  "sleep_deep_h",
    "sleep_core_h":  "sleep_core_h",
    "sleep_awake_h": "sleep_awake_h",
    "steps":         "steps",
    "active_kcal":   "active_kcal",
    "exercise_min":  "exercise_min",
    "resting_hr":    "resting_hr",
    "hrv_ms":        "hrv_ms",
    "vo2_max":       "vo2_max",
}
SLEEP_FIELDS = {"sleep_hours", "sleep_rem_h", "sleep_deep_h", "sleep_core_h", "sleep_awake_h"}


def target_date() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    return datetime.datetime.now(TZ).strftime("%Y-%m-%d")


def load_row(date: str) -> dict | None:
    if not CSV_PATH.exists():
        return None
    with CSV_PATH.open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("date") == date:
                return r
    return None


def update_frontmatter(note_path: Path, updates: dict) -> int:
    """Line-targeted frontmatter update. Returns # of fields changed."""
    text = note_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return 0  # no frontmatter — skip rather than risk corruption
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return 0
    fm = lines[1:end]
    # map existing top-level keys -> their line index
    existing = {}
    for idx, ln in enumerate(fm):
        if ln and not ln.startswith((" ", "\t", "#")) and ":" in ln:
            existing[ln.split(":", 1)[0].strip()] = idx
    changed = 0
    for k, v in updates.items():
        if v is None or v == "":
            continue
        newline = f"{k}: {v}"
        if k in existing:
            if fm[existing[k]] != newline:
                fm[existing[k]] = newline
                changed += 1
        else:
            fm.append(newline)
            changed += 1
    if not changed:
        return 0
    note_path.write_text("\n".join([lines[0]] + fm + lines[end:]), encoding="utf-8")
    return changed


def main() -> int:
    date = target_date()
    row = load_row(date)
    if not row:
        print(f"{date}: no metrics row in archive; nothing to write")
        return 0
    has_sleep = bool(row.get("sleep_total_h"))
    updates: dict[str, str] = {}
    for col, field in FIELD_MAP.items():
        val = row.get(col, "")
        if val in (None, ""):
            continue
        if field in SLEEP_FIELDS and not has_sleep:
            continue
        updates[field] = val
    if not updates:
        print(f"{date}: archive row has no mappable metrics yet")
        return 0
    updates["hae_synced"] = datetime.datetime.now(TZ).isoformat(timespec="seconds")

    note = DAILY_DIR / f"{date}.md"
    if not note.exists():
        print(f"{date}: daily note does not exist ({note.name}); skipping (CSV archive still has it)")
        return 0
    n = update_frontmatter(note, updates)
    metric_fields = [k for k in updates if k != "hae_synced"]
    print(f"{date}: {'wrote' if n else 'no change'} ({len(metric_fields)} HAE fields, sleep={has_sleep})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
