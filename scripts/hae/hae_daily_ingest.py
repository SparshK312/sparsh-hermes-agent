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
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo


def _default_vault() -> Path:
    """Resolve the vault root. HERMES_VAULT wins; else the VPS path if it exists
    (production), else the Mac dev path — so the same code runs in both places
    with no env var and read/write paths never split-brain."""
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    if vps.exists():
        return vps
    return Path.home() / "Documents" / "School Vault - UofT"


VAULT = _default_vault()
CSV_PATH = VAULT / "07 - Health" / "Metrics" / "metrics.csv"
DAILY_DIR = VAULT / "04 - Daily Notes"
# Per-date record of the values HAE itself last wrote, so a later hand-edit is
# detectable and never silently reverted on the next sync.
STATE_PATH = Path(
    os.environ.get("HAE_HEALTH_DIR", str(Path.home() / ".hermes" / "health" / "hae"))
) / "ingest_state.json"
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


DEFAULT_BACKFILL_DAYS = 7


def target_dates() -> list[str]:
    """Dates to ingest, newest first.

    Was `target_date()` — today only. That was the single biggest data-integrity
    bug in the pipeline: the last hae-sync of the day runs at 21:40 local, so any
    payload arriving after it updated metrics.csv (which reprocesses every raw
    payload) but never reached the note, which was frozen forever at whatever the
    21:40 partial happened to be. Across 103 days, 32 notes disagreed with the
    archive and the notes understated steps by 18.5% — and coach_engine reads the
    NOTE layer, so the coaching reasoned off the low numbers.

    Ingest is idempotent and manual-edit-protected (see update_frontmatter), so
    re-walking recent days is safe and self-healing.

      hae_daily_ingest.py                -> last DEFAULT_BACKFILL_DAYS days
      hae_daily_ingest.py 2026-08-04     -> that date only (back-compat)
      hae_daily_ingest.py --days 30      -> last 30 days
    """
    args = [a for a in sys.argv[1:] if a]
    if "--days" in args:
        i = args.index("--days")
        try:
            n = max(1, int(args[i + 1]))
        except (IndexError, ValueError):
            n = DEFAULT_BACKFILL_DAYS
    elif args and not args[0].startswith("-"):
        return [args[0]]                      # explicit single date
    else:
        n = DEFAULT_BACKFILL_DAYS
    today = datetime.datetime.now(TZ).date()
    return [(today - datetime.timedelta(days=i)).isoformat() for i in range(n)]


def load_row(date: str) -> dict | None:
    if not CSV_PATH.exists():
        return None
    with CSV_PATH.open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("date") == date:
                return r
    return None


def _field_value(line: str) -> str:
    """The value part of a 'key: value' frontmatter line."""
    return line.split(":", 1)[1].strip() if ":" in line else ""


def update_frontmatter(note_path: Path, updates: dict, prev: dict, protected: set) -> tuple[int, dict]:
    """Line-targeted frontmatter update that never clobbers a manual edit.

    `protected` fields (sleep — the realistic hand-correction surface) are
    overwritten only when absent or still equal to what HAE itself last wrote
    (`prev`); a value that differs from HAE's last write is a user correction and
    is left alone. Non-protected fields (steps/active_kcal/RHR/HRV — wearable-
    authoritative and accumulating) always take the latest archive value, so
    same-day activity totals keep climbing. Returns (#changed, {field: value HAE
    now owns}).
    """
    text = note_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return 0, {}  # no frontmatter — skip rather than risk corruption
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return 0, {}
    fm = lines[1:end]
    # map existing top-level keys -> their line index
    existing = {}
    for idx, ln in enumerate(fm):
        if ln and not ln.startswith((" ", "\t", "#")) and ":" in ln:
            existing[ln.split(":", 1)[0].strip()] = idx

    # Sleep is ATOMIC. If the headline `sleep_hours` is a manual value HAE doesn't own
    # (e.g. the user reported a real 10.23h night the watch missed), do NOT write ANY
    # sleep stage fields either — a 4.64h watch-fragment breakdown would contradict the
    # manual total and leave the note self-inconsistent (which is what made the bot
    # thrash trying to reconcile). Either HAE owns the whole sleep block, or it stays out.
    if "sleep_hours" in existing:
        cur = _field_value(fm[existing["sleep_hours"]])
        last = prev.get("sleep_hours")
        if cur != "" and (last is None or cur != last):
            updates = {k: v for k, v in updates.items() if k not in protected}

    changed = 0
    written: dict[str, str] = {}
    for k, v in updates.items():
        if v is None or v == "":
            continue
        val = str(v)
        if k in existing:
            current = _field_value(fm[existing[k]])
            if current == val:
                written[k] = val          # already correct; HAE still owns it
                continue
            if k in protected:
                last = prev.get(k)
                if last is None and current != "":
                    continue  # pre-existing value HAE never recorded → treat as manual
                if last is not None and current != last:
                    continue  # user edited HAE's value since we wrote it → manual wins
            fm[existing[k]] = f"{k}: {val}"
            written[k] = val
            changed += 1
        else:
            fm.append(f"{k}: {val}")
            written[k] = val
            changed += 1
    if changed:
        note_path.write_text("\n".join([lines[0]] + fm + lines[end:]), encoding="utf-8")
    return changed, written


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:  # noqa: BLE001 — missing/corrupt state is non-fatal
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state))
    except Exception:  # noqa: BLE001
        pass


def ingest_one(date: str, state: dict) -> tuple[int, str]:
    """Ingest a single date. Returns (fields_written, one-line status).
    `state` is mutated in place; the caller persists it once."""
    row = load_row(date)
    if not row:
        return 0, f"{date}: no metrics row in archive; nothing to write"
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
        return 0, f"{date}: archive row has no mappable metrics yet"
    updates["hae_synced"] = datetime.datetime.now(TZ).isoformat(timespec="seconds")

    note = DAILY_DIR / f"{date}.md"
    if not note.exists():
        return 0, f"{date}: daily note does not exist ({note.name}); skipping (CSV archive still has it)"
    prev = state.get(date, {})
    n, written = update_frontmatter(note, updates, prev, SLEEP_FIELDS)
    # Remember what HAE now owns for this date, so a later manual edit is detected
    # next run (current value != last-written → skip).
    merged = dict(prev)
    merged.update(written)
    state[date] = merged
    metric_fields = [k for k in updates if k != "hae_synced"]
    return n, f"{date}: {'wrote' if n else 'no change'} ({len(metric_fields)} HAE fields, sleep={has_sleep})"


def main() -> int:
    dates = target_dates()
    bad = [d for d in dates if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
    if bad:
        print(f"refusing malformed date(s): {bad!r}", file=sys.stderr)
        return 1
    # Load state ONCE and persist ONCE, so a multi-day walk is a single atomic
    # update rather than one rewrite per day.
    state = _load_state()
    total_written = 0
    for date in dates:
        n, line = ingest_one(date, state)
        total_written += n
        print(line)
    _save_state(state)
    if len(dates) > 1:
        print(f"backfill: {len(dates)} day(s) walked, {total_written} field(s) written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
