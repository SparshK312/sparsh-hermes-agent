#!/usr/bin/env python3
"""Hermes cost monitor — Anthropic spend guardrail.

Reads ~/.hermes/state.db (Hermes' own per-session token accounting), computes
estimated Anthropic API spend for *today* and *month-to-date*, and fires a
Telegram alert via `hermes send` when a threshold is crossed. Dedupes via a
small JSON state file so each alert is sent at most once per day / per month —
never a notification cannon.

Pure stdlib (sqlite3/json/os/subprocess/datetime) so it runs under system
python3 in cron with no venv. The cron wrapper prints {"wakeAgent": false},
so this never wakes the agent — it just sends text.

Cost is computed from token columns × current Anthropic per-MTok pricing rather
than the DB's estimated_cost_usd field (which can be 0 / provider-dependent).
Reasoning tokens bill as output. Cache reads/writes priced at Anthropic rates.

Usage:
  cost_monitor.py            # normal threshold check (for cron)
  cost_monitor.py --test     # force a sample alert to verify Telegram delivery
  cost_monitor.py --dry-run  # compute + print, never send, never touch state
"""
import sqlite3
import json
import os
import subprocess
import sys
from datetime import datetime

STATE_DB = os.path.expanduser("~/.hermes/state.db")
ALERT_STATE = os.path.expanduser("~/.hermes/cost_monitor_state.json")
HERMES = os.path.expanduser("~/.local/bin/hermes")
TELEGRAM_TARGET = "telegram"  # home channel

# Thresholds (USD). Steady-state is ~$1.60/day, ~$50/mo — these leave headroom
# while still catching a runaway same-day.
DAILY_LIMIT = 5.0
MTD_WARN = 75.0
MTD_URGENT = 150.0

# Anthropic pricing per MILLION tokens: (input, output, cache_read, cache_write)
PRICING = {
    "haiku": (1.0, 5.0, 0.10, 1.25),
    "sonnet": (3.0, 15.0, 0.30, 3.75),
    "opus": (5.0, 25.0, 0.50, 6.25),
}
DEFAULT_PRICE = (3.0, 15.0, 0.30, 3.75)  # unknown model → assume sonnet-ish (conservative)


def price_for(model):
    m = (model or "").lower()
    for key, val in PRICING.items():
        if key in m:
            return val
    return DEFAULT_PRICE


def session_cost(model, inp, out, cr, cw, reas):
    pi, po, pcr, pcw = price_for(model)
    inp, out, cr, cw, reas = (x or 0 for x in (inp, out, cr, cw, reas))
    return (inp * pi + (out + reas) * po + cr * pcr + cw * pcw) / 1_000_000


def load_state():
    try:
        with open(ALERT_STATE) as f:
            return json.load(f)
    except Exception:
        return {"daily": {}, "mtd": {}}


def save_state(state):
    tmp = ALERT_STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, ALERT_STATE)


def send(subject, body):
    try:
        subprocess.run(
            [HERMES, "send", "-t", TELEGRAM_TARGET, "-q", "-s", subject, body],
            check=True, timeout=30,
        )
        return True
    except Exception as e:
        print(f"send failed: {e}", file=sys.stderr)
        return False


def compute():
    now = datetime.now()  # VPS local time
    sod = datetime(now.year, now.month, now.day).timestamp()
    som = datetime(now.year, now.month, 1).timestamp()
    con = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
    # Only count real Anthropic spend. Pre-migration sessions ran on free Codex
    # (gpt-5.4-mini) and cost $0 — including them would price free usage at Claude
    # rates and false-alarm. Anthropic spend == Claude-model sessions.
    rows = con.execute(
        "SELECT model,input_tokens,output_tokens,cache_read_tokens,"
        "cache_write_tokens,reasoning_tokens,started_at,estimated_cost_usd "
        "FROM sessions WHERE started_at >= ? "
        "AND (lower(model) LIKE 'claude%' "
        "OR lower(coalesce(billing_provider,'')) = 'anthropic')", (som,)
    ).fetchall()
    con.close()

    def cost_of(r):
        # Trust Hermes' own per-session estimate (more complete than the raw token
        # columns for Anthropic — e.g. cached/system-prompt input isn't fully in
        # input_tokens). Fall back to token-math only if the estimate is missing.
        est = r[7]
        if est and est > 0:
            return est
        return session_cost(*r[:6])

    daily = sum(cost_of(r) for r in rows if r[6] and r[6] >= sod)
    mtd = sum(cost_of(r) for r in rows)
    return now, daily, mtd


def main():
    test = "--test" in sys.argv
    dry = "--dry-run" in sys.argv

    if test:
        ok = send("🧪 Hermes cost monitor",
                  "Test alert — the cost monitor is wired up and can reach Telegram.")
        print("test send:", "ok" if ok else "FAILED")
        return

    now, daily, mtd = compute()
    day_key = now.strftime("%Y-%m-%d")
    mon_key = now.strftime("%Y-%m")
    print(f"{now.isoformat(timespec='seconds')}  today=${daily:.2f}  MTD=${mtd:.2f}")

    if dry:
        return

    state = load_state()
    state.setdefault("daily", {})
    state.setdefault("mtd", {})
    fired = []

    # Daily spike — once per day
    if daily > DAILY_LIMIT and "spike" not in state["daily"].get(day_key, []):
        if send("⚠️ Hermes daily spend spike",
                f"Today's estimated Anthropic spend is ${daily:.2f} "
                f"(alert at ${DAILY_LIMIT:.0f}). Steady-state is ~$1.60/day — "
                f"check for a stuck/looping session."):
            state["daily"].setdefault(day_key, []).append("spike")
            fired.append("daily-spike")

    # Month-to-date urgent — once per month (takes priority over warn)
    if mtd > MTD_URGENT and "urgent" not in state["mtd"].get(mon_key, []):
        if send("🚨 Hermes spend — urgent",
                f"Month-to-date Anthropic spend is ${mtd:.2f} "
                f"(urgent at ${MTD_URGENT:.0f}). You're approaching your Float comfort zone."):
            state["mtd"].setdefault(mon_key, []).append("urgent")
            fired.append("mtd-urgent")
    # Month-to-date warning — once per month
    elif mtd > MTD_WARN and "warn" not in state["mtd"].get(mon_key, []):
        if send("⚠️ Hermes spend — heads up",
                f"Month-to-date Anthropic spend is ${mtd:.2f} "
                f"(warning at ${MTD_WARN:.0f}). Still well inside the $1k Float budget."):
            state["mtd"].setdefault(mon_key, []).append("warn")
            fired.append("mtd-warn")

    # Prune old state (keep current month only)
    state["daily"] = {k: v for k, v in state["daily"].items() if k.startswith(mon_key)}
    state["mtd"] = {k: v for k, v in state["mtd"].items() if k == mon_key}
    save_state(state)
    print("fired:", fired or "none")


if __name__ == "__main__":
    main()
