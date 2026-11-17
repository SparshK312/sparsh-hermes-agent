"""spend-guard — the chat agent cannot run up a bill past a fixed ceiling.

WHY (his call, 2026-11-16): no single message may run up an unreasonable cost, and
nothing may keep spending unnoticed. Until now the
only cost control on the chat agent was an hourly ALERT (scripts/monitor/cost_monitor.py).
An alert tells him after the money is spent. This stops the spending.

THREE LAYERS, each independent:
  1. One message: `agent.max_turns` in config.yaml caps the model calls a single message
     can trigger (30). Sub-agents are disabled on Telegram. That is the per-message cap.
  2. One day:  when today's spend >= DAILY_HARD_USD, a new Telegram message does NOT start
     an agent turn. This plugin answers it with a fixed notice sent straight through the
     Bot API (zero model calls, zero cost) and drops the message.
  3. One month: the same at MONTHLY_HARD_USD.

"Spend" = every session in the framework's database since local midnight / the 1st (chat
and scheduled agent jobs) + the assistant scripts' own record (assistant/spend.jsonl).

HE IS NEVER LOCKED OUT. Replying "override limit" lifts the pause until local midnight
(also answered without a model call). The cost monitor's alerts keep running.

FAILS OPEN, LOUDLY: if the spend cannot be read, the message goes through and the reason
is written to ~/.hermes/logs/spend_guard.log. A guard bug must never silence the agent.
The limits are meant to sit far above a normal day (about $1-2) and far below damage.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
DAILY_HARD_USD = 5.00
MONTHLY_HARD_USD = 60.00
OVERRIDE = re.compile(r"^\s*/?override[ _-]?limit\s*[.!]?\s*$", re.I)

HOME = Path.home() / ".hermes"
STATE_DB = HOME / "state.db"
LOG = HOME / "logs" / "spend_guard.log"


def _dir() -> Path:
    return Path(os.environ.get("HERMES_ASSISTANT_DIR", str(HOME / "assistant")))


def _log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def now() -> datetime.datetime:
    return datetime.datetime.now(TZ)


def spend(at: datetime.datetime | None = None, db: Path | None = None) -> tuple[float, float]:
    """(today, month-to-date) in USD. Raises if the database cannot be read."""
    n = at or now()
    sod = n.replace(hour=0, minute=0, second=0, microsecond=0)
    som = sod.replace(day=1)
    con = sqlite3.connect(f"file:{db or STATE_DB}?mode=ro", uri=True, timeout=5)
    try:
        q = "select coalesce(sum(coalesce(actual_cost_usd, estimated_cost_usd, 0)), 0) from sessions where started_at >= ?"
        day = float(con.execute(q, (sod.timestamp(),)).fetchone()[0])
        month = float(con.execute(q, (som.timestamp(),)).fetchone()[0])
    finally:
        con.close()
    try:                                              # the assistant scripts' direct API spend
        for ln in (_dir() / "spend.jsonl").read_text().splitlines():
            try:
                row = json.loads(ln)
                if str(row["day"]).startswith(n.strftime("%Y-%m")):
                    month += float(row["usd"])
                    if row["day"] == n.strftime("%Y-%m-%d"):
                        day += float(row["usd"])
            except Exception:  # noqa: BLE001
                continue
    except OSError:
        pass
    return day, month


def override_active(at: datetime.datetime | None = None) -> bool:
    try:
        return (_dir() / "spend_override_day").read_text().strip() == (at or now()).strftime("%Y-%m-%d")
    except OSError:
        return False


def set_override(at: datetime.datetime | None = None) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    (_dir() / "spend_override_day").write_text((at or now()).strftime("%Y-%m-%d"))


def decide(text: str, day: float, month: float, overridden: bool) -> tuple[str, str]:
    """('allow' | 'block' | 'override', notice). Pure."""
    over_day, over_month = day >= DAILY_HARD_USD, month >= MONTHLY_HARD_USD
    if OVERRIDE.match(text or ""):
        if over_day or over_month:
            return "override", (f"Limit lifted until midnight. Today so far: ${day:.2f}; this month: ${month:.2f}. "
                                f"Send your message again.")
        return "override", f"No limit is active. Today so far: ${day:.2f} of ${DAILY_HARD_USD:.2f}."
    if (over_day or over_month) and not overridden:
        which = (f"today's spend is ${day:.2f} (limit ${DAILY_HARD_USD:.2f})" if over_day
                 else f"this month's spend is ${month:.2f} (limit ${MONTHLY_HARD_USD:.2f})")
        return "block", (f"⏸ Hermes is paused: {which}. Your message was NOT processed and nothing was logged. "
                         f"Reply \"override limit\" to keep going today, or wait until "
                         f"{'midnight' if over_day else 'the 1st'}.")
    return "allow", ""


def _env(key: str):
    v = os.environ.get(key)
    if v:
        return v
    try:
        for ln in (HOME / ".env").read_text().splitlines():
            if ln.startswith(f"{key}="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def notify(chat_id, text: str) -> bool:
    """Plain Bot API message. No model, no cost."""
    token = _env("TELEGRAM_BOT_TOKEN")
    if not (token and chat_id):
        return False
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                     data=urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode())
        with urllib.request.urlopen(req, timeout=15) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception as e:  # noqa: BLE001
        _log(f"notify failed: {type(e).__name__}: {e}")
        return False


def _guard(event=None, read_spend=spend, send=notify, **kwargs):
    """pre_gateway_dispatch. Returns {"action": "skip"} to drop a message, else None."""
    try:
        src = getattr(event, "source", None)
        if "telegram" not in str(getattr(src, "platform", "")).lower():
            return None
        text = (getattr(event, "text", "") or "")
        chat_id = getattr(src, "chat_id", None)
        day, month = read_spend()
        action, notice = decide(text, day, month, override_active())
        if action == "allow":
            return None
        if action == "override":
            set_override()
            _log(f"override set (day ${day:.2f}, month ${month:.2f})")
            send(chat_id, notice)
            return {"action": "skip", "reason": "spend-guard: override command handled"}
        _log(f"BLOCKED a message (day ${day:.2f}, month ${month:.2f})")
        if not send(chat_id, notice):
            # He must never be dropped in silence: if the notice cannot be delivered, let
            # the message through (the turn is bounded by agent.max_turns) and say why.
            _log("notice could not be sent; letting the message through")
            return None
        return {"action": "skip", "reason": "spend-guard: over the spend limit"}
    except Exception as e:  # noqa: BLE001 — fail open, loudly
        _log(f"FAILED OPEN: {type(e).__name__}: {e}")
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _guard)
