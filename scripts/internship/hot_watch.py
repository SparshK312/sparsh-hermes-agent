#!/usr/bin/env python3
"""
hot_watch.py — ping Telegram the moment a top-tier company opens an intern role.

WHY: the board refreshes twice a day, so a Google or Microsoft req that opened at 09:05
sat unseen until 18:00. Sparsh's whole thesis is brand-first and applying EARLY — "it is
very very crucial that on a daily basis we are applying to the top rated ones as soon as
possible, as soon as they open." Twice a day is not "as soon as they open."

This is deliberately NARROW. It does not re-harvest the world: it polls only the
tier-S/A boards already configured in company_boards.py, compares against a seen-set,
and pings on anything genuinely new. No LLM, no JD fetch, no scoring — the 8am/6pm
refresh still does all of that. This exists purely to say "Google just opened X" fast.

  hot_watch.py                 # poll, alert on anything new
  hot_watch.py --seed          # record everything currently open WITHOUT alerting
  hot_watch.py --dry-run       # print what it would send, send nothing
  hot_watch.py --tiers S       # narrow the watchlist

🔴 FIRST RUN MUST BE --seed. Without it the first poll treats every currently-open role
as new and sends one enormous alert.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import brand_first_source  # noqa: E402
import company_boards  # noqa: E402

STATE = Path.home() / ".hermes" / "internship" / "hot_watch_state.json"
ENV_FILE = Path.home() / ".hermes" / ".env"
# The chat id is not an env var anywhere — health_morning_brief_gate.py hardcodes it
# the same way. Env still wins if it is ever set.
CHAT_ID_KEYS = ("TELEGRAM_CHAT_ID", "HERMES_TELEGRAM_CHAT_ID")
CHAT_ID_DEFAULT = "696500863"   # Sparsh
WATCH_TIERS = {"S", "A"}
MAX_ALERT = 8          # never send more than this many in one message
QUIET_START, QUIET_END = 23, 7    # local hours: hold alerts overnight


def _env(key: str) -> str | None:
    v = os.environ.get(key)
    if v:
        return v
    try:
        for ln in ENV_FILE.read_text().splitlines():
            if ln.startswith(f"{key}="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return None


def _log(m: str) -> None:
    print(f"[hot-watch] {m}", file=sys.stderr)


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:  # noqa: BLE001
        return {"seen": [], "seeded": False, "last_run": None}


def _save(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    st["seen"] = st["seen"][-20000:]
    STATE.write_text(json.dumps(st))


def send(text: str) -> bool:
    token = _env("TELEGRAM_BOT_TOKEN")
    chat = next((_env(k) for k in CHAT_ID_KEYS if _env(k)), None) or CHAT_ID_DEFAULT
    if not (token and chat):
        _log("no TELEGRAM_BOT_TOKEN / chat id — cannot send")
        return False
    # PLAIN TEXT FIRST, deliberately. Every alert carries job URLs, and Telegram's
    # legacy Markdown chokes on the underscores in them (gh_jid=...), so a Markdown
    # attempt 400s on essentially every message. Bold is not worth a wasted request
    # and a log line that looks like a failure.
    for pm in (None, "Markdown"):
        payload = {"chat_id": chat, "text": text}
        if pm:
            payload["parse_mode"] = pm
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=urllib.parse.urlencode(payload).encode())
            with urllib.request.urlopen(req, timeout=20) as r:
                if json.loads(r.read()).get("ok"):
                    return True
        except Exception as e:  # noqa: BLE001
            _log(f"send ({pm}) failed: {e}")
    return False


async def poll(tiers: set[str]) -> list[dict]:
    """Only the watched boards. Monkey-patches boards() so we reuse the harvest code
    without pulling the other 20 and without duplicating its parsing."""
    watch = [b for b in company_boards.BOARDS if (b.get("tier") or "").upper() in tiers]
    _log(f"polling {len(watch)} tier-{'/'.join(sorted(tiers))} boards")
    orig = brand_first_source.boards
    brand_first_source.boards = lambda: watch
    try:
        return await brand_first_source.collect()
    finally:
        brand_first_source.boards = orig


def _fmt(rows: list[dict]) -> str:
    lines = ["🚨 NEW ROLES JUST OPENED", ""]
    for r in rows[:MAX_ALERT]:
        co = r.get("company", "?")
        role = (r.get("role") or "")[:64]
        cyc = r.get("cycle") or ""
        loc = (r.get("location") or "")[:34]
        lines.append(f"• {co} — {role}")
        bits = [x for x in (cyc, loc) if x]
        if bits:
            lines.append(f"  {' · '.join(bits)}")
        if r.get("url"):
            lines.append(f"  {r['url']}")
    extra = len(rows) - MAX_ALERT
    if extra > 0:
        lines.append(f"\n…and {extra} more on the board.")
    lines.append("\nApply early — this is the whole point of watching.")
    return "\n".join(lines)


def main() -> int:
    seed = "--seed" in sys.argv
    dry = "--dry-run" in sys.argv
    tiers = set(sys.argv[sys.argv.index("--tiers") + 1].upper()) if "--tiers" in sys.argv \
        else set(WATCH_TIERS)

    st = _load()
    seen = set(st.get("seen", []))

    try:
        rows = asyncio.run(poll(tiers))
    except Exception as e:  # noqa: BLE001 - never let a poll failure kill the cron
        _log(f"poll failed: {type(e).__name__}: {e}")
        return 1

    fresh = [r for r in rows if r.get("canonical_id") and r["canonical_id"] not in seen]
    _log(f"{len(rows)} live on watched boards · {len(fresh)} not seen before")

    for r in rows:
        if r.get("canonical_id"):
            seen.add(r["canonical_id"])
    st["seen"] = sorted(seen)
    st["last_run"] = datetime.now().isoformat(timespec="seconds")

    if seed or not st.get("seeded"):
        st["seeded"] = True
        _save(st)
        _log(f"seeded {len(seen)} ids — no alert sent (this is the first run)")
        return 0

    if not fresh:
        _save(st)
        return 0

    hour = datetime.now().hour
    if QUIET_START <= hour or hour < QUIET_END:
        _log(f"{len(fresh)} new, but it is {hour:02d}:00 — holding until morning")
        # do NOT record them as seen, so the next daytime run still alerts
        st["seen"] = sorted(seen - {r["canonical_id"] for r in fresh})
        _save(st)
        return 0

    msg = _fmt(fresh)
    if dry:
        print(msg)
        return 0
    if send(msg):
        _log(f"alerted on {len(fresh)} new role(s)")
        _save(st)
        return 0
    _log("send failed — NOT recording as seen, will retry next run")
    return 1


if __name__ == "__main__":
    sys.exit(main())
