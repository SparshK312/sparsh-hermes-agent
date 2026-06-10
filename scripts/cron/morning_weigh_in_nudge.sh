#!/usr/bin/env bash
# Hermes cron job: health-morning-nudge — CONTEXT-AWARE morning message.
#
# Fires in the morning, AFTER the hae-sync that lands last night's sleep (confirm the
# ordering + timezone in jobs.json; cron exprs run in the Hermes-configured tz — see
# config/cron_additions.json).
#
# This is an AGENT job BY DESIGN: the script reads today's logged state (sleep from
# metrics.csv, weight from the daily note) and emits a compact STATE block; the agent
# composes the actual nudge from it per the job's `prompt` (in cron_additions.json),
# so it can adapt — e.g. drop the weigh-in line if weight is already logged. The script
# hard-suppresses (prints the {"wakeAgent": false} gate → no LLM, nothing sent) only
# when there's genuinely nothing to say.
set -uo pipefail

/usr/bin/python3 - <<'PY'
import csv, datetime, os
from pathlib import Path
from zoneinfo import ZoneInfo


def vault() -> Path:
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    return vps if vps.exists() else Path.home() / "Documents" / "School Vault - UofT"


V = vault()
TZ = ZoneInfo("America/Toronto")
today = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
CSVP = V / "07 - Health" / "Metrics" / "metrics.csv"


def fnum(d, k):
    try:
        return float(d.get(k))
    except (TypeError, ValueError):
        return None


def daily_fm(date: str) -> dict:
    try:
        txt = (V / "04 - Daily Notes" / f"{date}.md").read_text(encoding="utf-8")
    except Exception:
        return {}
    parts = txt.split("---")
    if len(parts) < 3:
        return {}
    fm = {}
    for ln in parts[1].splitlines():
        if ":" in ln and not ln.startswith((" ", "\t", "#")):
            k, v = ln.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


rows = []
if CSVP.exists():
    with CSVP.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
byd = {r["date"]: r for r in rows}
t = byd.get(today, {})
sl = fnum(t, "sleep_total_h")
recent = [v for r in rows
          if r.get("date", "") < today and (v := fnum(r, "sleep_total_h")) is not None][-7:]
avg = round(sum(recent) / len(recent), 1) if recent else None

fm = daily_fm(today)
weight_logged = bool(fm.get("weight"))

# Nothing useful to say: sleep didn't sync AND weight is already logged.
if sl is None and weight_logged:
    print('{"wakeAgent": false}')
    raise SystemExit

L = [f"[morning-nudge state · {today}]"]
if sl is not None:
    L.append("sleep_synced: yes")
    L.append(f"sleep_hours: {sl:.2f}")
    for k, lab in (("sleep_deep_h", "deep_h"), ("sleep_rem_h", "rem_h"),
                   ("resting_hr", "resting_hr"), ("hrv_ms", "hrv_ms")):
        v = fnum(t, k)
        if v is not None:
            L.append(f"{lab}: {v:g}")
    if avg is not None:
        L.append(f"sleep_7day_avg_h: {avg}")
else:
    L.append("sleep_synced: no")
L.append("sleep_floor_h: 7")
L.append("weight_logged_today: " + (f"yes ({fm['weight']} lb)" if weight_logged else "no"))
print("\n".join(L))
PY
