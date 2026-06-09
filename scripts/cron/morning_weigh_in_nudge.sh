#!/usr/bin/env bash
# Hermes cron job: health-morning-nudge
# Fires at 07:45 America/Toronto (after the 07:40 hae-sync lands last night's sleep).
# Script-mode (no LLM) — stdout is delivered verbatim to Telegram by Hermes.
#
# Phase 3: reports last night's Apple Watch sleep (+ stages, RHR, HRV, 7-day avg)
# pulled from 07 - Health/Metrics/metrics.csv, then the quick-log reminders.

set -uo pipefail

/usr/bin/python3 - <<'PY'
import csv, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

VAULT = Path("/home/hermes/vault")
CSVP = VAULT / "07 - Health" / "Metrics" / "metrics.csv"
today = datetime.datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")

rows = list(csv.DictReader(CSVP.open())) if CSVP.exists() else []
byd = {r["date"]: r for r in rows}
t = byd.get(today, {})

def fnum(r, k):
    v = r.get(k)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

print("🌅 *Morning.*\n")

sl = fnum(t, "sleep_total_h")
if sl is not None:
    recent = [fnum(r, "sleep_total_h") for r in rows
              if r.get("date", "") < today and fnum(r, "sleep_total_h") is not None][-7:]
    avg = sum(recent) / len(recent) if recent else None
    flag = "✅" if sl >= 7 else "⚠️"
    line = f"{flag} Slept *{sl:.1f}h* last night"
    if avg is not None:
        line += f"  _(7-day avg {avg:.1f}h)_"
    print(line)
    extras = []
    for k, lab, fmt in (("sleep_deep_h", "deep", "{:.1f}h"),
                        ("sleep_rem_h", "REM", "{:.1f}h"),
                        ("resting_hr", "RHR", "{:.0f}"),
                        ("hrv_ms", "HRV", "{:.0f}ms")):
        v = fnum(t, k)
        if v is not None:
            extras.append(f"{lab} " + fmt.format(v))
    if extras:
        print("   " + " · ".join(extras))
    if sl < 7:
        print("   _Under your 7h floor — guard sleep tonight._")
    print()
else:
    print("_(Apple Watch sleep hasn't synced yet — unlock your phone so it pushes, "
          "or `/sleep <hrs>` to log manually.)_\n")

print("Quick logs:")
print("• `/weight <lb>` — bodyweight (after bathroom, before food)")
print("• `/today` — full picture so far")
PY
