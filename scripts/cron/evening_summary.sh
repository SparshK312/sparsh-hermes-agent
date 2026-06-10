#!/usr/bin/env bash
# Hermes cron job: health-evening-summary-nudge — CONTEXT-AWARE end-of-day check-in.
#
# Fires in the evening (schedule in jobs.json, evaluated in the Hermes-configured
# timezone — see config/cron_additions.json).
#
# Emits the day's logged state + the remaining gaps (dinner / water / vitamins / macros);
# the agent composes an adaptive check-in per the job `prompt` in cron_additions.json,
# nagging ONLY about what's actually missing. Not hard-suppressed — the bed reminder
# always applies — but if nothing's missing the agent keeps it to one line.
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


fm = daily_fm(today)
ml = meals(today)


def fnum(k):
    try:
        return float(fm.get(k))
    except (TypeError, ValueError):
        return None


water = fnum("water_l")
kcal = fnum("kcal")
protein = fnum("protein_g")
dinner = "dinner" in ml
vitamins = fm.get("vitamins_taken", "").lower() == "true"

missing = []
if not dinner:
    missing.append("dinner")
if water is None or water < 2.5:
    missing.append("water")
if not vitamins:
    missing.append("vitamins")

L = [
    f"[evening-nudge state · {today}]",
    f"dinner_logged: {'yes' if dinner else 'no'}",
    f"water_l: {water if water is not None else 'none'} (target 2.5)",
    f"vitamins_taken: {'yes' if vitamins else 'no'}",
    f"kcal_so_far: {int(kcal) if kcal is not None else 0} (target 2400)",
    f"protein_so_far_g: {int(protein) if protein is not None else 0} (target 140)",
    f"missing: {', '.join(missing) or 'nothing'}",
]
print("\n".join(L))
PY
