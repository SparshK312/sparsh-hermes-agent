#!/usr/bin/env bash
# Hermes cron job: health-lunch-check — CONTEXT-AWARE midday nudge.
#
# Fires around midday (schedule in jobs.json, evaluated in the Hermes-configured
# timezone — see config/cron_additions.json).
#
# If lunch is already logged it hard-suppresses (prints the {"wakeAgent": false} gate →
# no LLM, no nag). Otherwise it emits a STATE block and the agent composes the reminder
# per the job `prompt` in cron_additions.json (so it can reference how he's tracking).
set -uo pipefail

/usr/bin/python3 - <<'PY'
import datetime, os, re
from pathlib import Path
from zoneinfo import ZoneInfo


def vault() -> Path:
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    return vps if vps.exists() else Path.home() / "Documents" / "School Vault - UofT"


V = vault()
today = datetime.datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")


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


def meals(date: str) -> set:
    try:
        txt = (V / "07 - Health" / "Food Log" / f"{date}.md").read_text(encoding="utf-8")
    except Exception:
        return set()
    return {m.lower() for m in re.findall(r"^##\s+.*·\s*(\w+)", txt, re.M)}


ml = meals(today)
if "lunch" in ml:
    print('{"wakeAgent": false}')   # lunch already logged → no nag
    raise SystemExit

fm = daily_fm(today)
L = [
    f"[lunch-nudge state · {today}]",
    "lunch_logged: no",
    f"meals_logged: {', '.join(sorted(ml)) or 'none'}",
    f"kcal_so_far: {fm.get('kcal') or 0}",
    f"protein_so_far_g: {fm.get('protein_g') or 0}",
    "kcal_target: 2400",
    "protein_target_g: 140",
]
print("\n".join(L))
PY
