"""turn-context — inject the current date into every turn.

Hermes does NOT tell the agent the current date (its SOUL has a "go run `date +%F`
yourself" rule, which Claude sometimes skips → food logged to the wrong day). This
plugin hands the model the exact date(s) every turn via the `pre_llm_call` hook, so
date handling stops depending on the model remembering to fetch it.

The context is appended to the user message (ephemeral, never the system prompt — so
the prompt-cache prefix is preserved). Fail-open: any error → no injection, never a
broken turn. Times are America/Toronto.
"""
from __future__ import annotations

import datetime

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("America/Toronto")
except Exception:  # noqa: BLE001
    _TZ = None


def _now() -> datetime.datetime:
    return datetime.datetime.now(_TZ) if _TZ else datetime.datetime.now()


def _inject(session_id=None, user_message=None, **kwargs):
    """pre_llm_call: return {context} with today's date + a recent-day map for backfill."""
    try:
        now = _now()
        # Recent named days so "log for Monday / yesterday" resolves to an exact date.
        recent = []
        for i in range(1, 6):
            d = now - datetime.timedelta(days=i)
            recent.append(f"{d.strftime('%a')} {d.strftime('%Y-%m-%d')}")
        ctx = (
            f"[Current date — America/Toronto] Today = {now.strftime('%Y-%m-%d')} "
            f"({now.strftime('%A')}), {now.strftime('%-I:%M %p')}. "
            f"Recent days: {', '.join(recent)}. "
            f"Use these EXACT YYYY-MM-DD strings for any logging or date reference "
            f"(e.g. \"log for Monday\" or \"yesterday\" → the date above). "
            f"Do NOT run `date` or guess the date. Daily notes live at "
            f"'04 - Daily Notes/<YYYY-MM-DD>.md'."
        )
        return {"context": ctx}
    except Exception:  # noqa: BLE001 — fail open: no date context beats a broken turn
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _inject)
