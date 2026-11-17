#!/usr/bin/env python3
"""
llm.py — the one model client for the assistant scripts, with a spend guard in front.

WHY (2026-11-16, his call): the system must not be able to burn through API credits
by accident. The older scripts each carry their own urllib client, none counts what
it spends, and the cost monitor only sees the chat agent's database, so direct API calls
were invisible to it. A loop that wakes every hour and decides for itself whether to call
a model needs a ceiling that does not depend on the loop behaving.

Every call goes through call():
  1. spend.allow(): refused when today's calls or today's dollars are at the ceiling, or
     this process already made MAX_CALLS_PER_RUN calls. A refusal returns None, which every
     caller already treats as "no model": a code-written template, or silence.
  2. the HTTP call (text block picked by TYPE, so a thinking block cannot break it).
  3. the tokens the API reports are priced and appended to spend.jsonl. A call that fails
     is still counted as an attempt, so a failure loop cannot run free.

The ceilings are meant to be far above normal use and far below damage: a normal day of
the check-in loop is a handful of calls costing a few cents.

FAILS CLOSED: a spend file that exists and cannot be read refuses the call.
Stdlib only.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ENV_FILE = Path.home() / ".hermes" / ".env"

HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-4-6"

# Ceilings. Per Eastern day, across every assistant script.
DAILY_CALL_CEILING = 60
DAILY_USD_CEILING = 1.50
MAX_CALLS_PER_RUN = 6            # one process (one tick, one extraction) can never loop
MAX_TOKENS_CEILING = 4000        # no assistant call may ask for a longer reply than this
MAX_PROMPT_CHARS = 120_000       # ~30k tokens; a bigger prompt is a bug, not a need

# USD per MILLION tokens: (input, output, cache_read, cache_write). Same table as
# scripts/monitor/cost_monitor.py. An unknown model is priced as the dearest row, so a
# typo in a model name can make the guard stricter, never looser.
PRICING = {
    "haiku": (1.0, 5.0, 0.10, 1.25),
    "sonnet": (3.0, 15.0, 0.30, 3.75),
    "opus": (15.0, 75.0, 1.50, 18.75),
}

_calls_this_run = 0


def log(msg: str) -> None:
    print(f"[llm] {msg}", file=sys.stderr)


def spend_path() -> Path:
    base = os.environ.get("HERMES_ASSISTANT_DIR", str(Path.home() / ".hermes" / "assistant"))
    return Path(base) / "spend.jsonl"


class SpendError(RuntimeError):
    """spend.jsonl exists and cannot be read. Never treated as 'nothing spent'."""


def now() -> datetime.datetime:
    return datetime.datetime.now(TZ)


def price(model: str, usage: dict) -> float:
    row = next((v for k, v in PRICING.items() if k in (model or "").lower()), PRICING["opus"])
    return round((usage.get("input_tokens", 0) * row[0]
                  + usage.get("output_tokens", 0) * row[1]
                  + usage.get("cache_read_input_tokens", 0) * row[2]
                  + usage.get("cache_creation_input_tokens", 0) * row[3]) / 1_000_000, 6)


def today_rows(at: datetime.datetime | None = None) -> list[dict]:
    p = spend_path()
    if not p.exists():
        return []
    day = (at or now()).astimezone(TZ).date().isoformat()
    try:
        rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        for r in rows:
            float(r["usd"]); str(r["day"])
    except Exception as e:  # noqa: BLE001
        raise SpendError(f"{p} unreadable: {type(e).__name__}: {e}") from e
    return [r for r in rows if r["day"] == day]


def today_totals(at: datetime.datetime | None = None) -> dict:
    rows = today_rows(at)
    return {"calls": len(rows), "usd": round(sum(float(r["usd"]) for r in rows), 4),
            "by_purpose": {p: sum(1 for r in rows if r.get("purpose") == p)
                           for p in sorted({r.get("purpose", "") for r in rows})}}


def allow(at: datetime.datetime | None = None) -> tuple[bool, str]:
    """May one more model call be made? (ok, why)."""
    if _calls_this_run >= MAX_CALLS_PER_RUN:
        return False, f"this run already made {_calls_this_run} model calls (cap {MAX_CALLS_PER_RUN})"
    try:
        t = today_totals(at)
    except SpendError as e:
        return False, f"spend record unreadable, refusing: {e}"
    if t["calls"] >= DAILY_CALL_CEILING:
        return False, f"daily call ceiling reached ({t['calls']}/{DAILY_CALL_CEILING})"
    if t["usd"] >= DAILY_USD_CEILING:
        return False, f"daily spend ceiling reached (${t['usd']:.2f}/${DAILY_USD_CEILING:.2f})"
    return True, "ok"


def tripped(at: datetime.datetime | None = None) -> str:
    """'' while under the daily ceilings, else the reason (for a once-a-day alert)."""
    try:
        t = today_totals(at)
    except SpendError as e:
        return str(e)
    if t["calls"] >= DAILY_CALL_CEILING:
        return f"{t['calls']} model calls today (ceiling {DAILY_CALL_CEILING})"
    if t["usd"] >= DAILY_USD_CEILING:
        return f"${t['usd']:.2f} spent today (ceiling ${DAILY_USD_CEILING:.2f})"
    return ""


def _record(purpose: str, model: str, usage: dict, ok: bool, at: datetime.datetime | None = None) -> None:
    n = (at or now()).astimezone(TZ)
    row = {"at": n.isoformat(), "day": n.date().isoformat(), "purpose": purpose, "model": model,
           "ok": ok, "in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0),
           "usd": price(model, usage)}
    p = spend_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _env(key: str):
    v = os.environ.get(key)
    if v:
        return v
    try:
        for ln in ENV_FILE.read_text().splitlines():
            if ln.startswith(f"{key}="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def _post(body: bytes, key: str, timeout: int) -> dict:
    req = urllib.request.Request(ANTHROPIC_URL, data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def call(system: str, user: str, *, purpose: str, model: str = HAIKU, max_tokens: int = 800,
         timeout: int = 45, post=_post):
    """One Messages call behind the spend guard. Returns the reply text, or None when the
    guard refused, the key is missing, the reply was cut off, or the call failed. Every
    None is logged with its reason. Never raises."""
    global _calls_this_run
    ok, why = allow()
    if not ok:
        log(f"REFUSED ({purpose}): {why}")
        return None
    if len(system) + len(user) > MAX_PROMPT_CHARS:
        log(f"REFUSED ({purpose}): prompt is {len(system) + len(user)} chars (cap {MAX_PROMPT_CHARS})")
        return None
    key = _env("ANTHROPIC_API_KEY")
    if not key:
        log(f"({purpose}) no ANTHROPIC_API_KEY")
        return None
    body = json.dumps({"model": model, "max_tokens": min(int(max_tokens), MAX_TOKENS_CEILING),
                       "system": system, "messages": [{"role": "user", "content": user}]}).encode()
    _calls_this_run += 1
    try:
        raw = post(body, key, timeout)
    except Exception as e:  # noqa: BLE001
        # Count the attempt (at the price of the prompt alone) so a failing loop still hits the ceiling.
        _record(purpose, model, {"input_tokens": (len(system) + len(user)) // 4}, ok=False)
        log(f"({purpose}) {type(e).__name__}: {e}")
        return None
    _record(purpose, model, raw.get("usage") or {}, ok=True)
    if raw.get("stop_reason") == "max_tokens":
        log(f"({purpose}) reply cut off at max_tokens")
        return None
    text = next((b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text"), "")
    return text.strip() or None


if __name__ == "__main__":
    try:
        t = today_totals()
        print(f"today: {t['calls']} calls, ${t['usd']:.4f}  (ceilings {DAILY_CALL_CEILING} calls / "
              f"${DAILY_USD_CEILING:.2f})  by purpose: {t['by_purpose']}")
    except SpendError as e:
        print(f"SPEND RECORD UNREADABLE: {e}")
        sys.exit(2)
