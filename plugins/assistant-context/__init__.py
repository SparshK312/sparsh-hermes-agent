"""assistant-context — what makes "reply and it continues" work.

THE PROBLEM. Hermes's follow-ups are sent by scripts. The chat agent only knows what is in
its session transcript, and a session ends after an idle period (30 minutes since 2026-11-18; it was 180) and at 04:00 every day.
So when he answers "yeah she replied this morning", the agent has no idea what was asked.

THE FIX. Two hooks, both reading the assistant's own files (scripts/assistant/), never the
framework's database:

  pre_gateway_dispatch   every message he sends on Telegram: remember which follow-ups
                         were still unanswered at that moment, THEN stamp "he wrote"
                         (outbox.note_inbound). The stamp is what the nudge budget uses
                         for "is he replying?".
  pre_llm_call           on Telegram turns only: add a short background block to the turn
                         with those follow-ups and, on the first turn of a session, his
                         top open loops with their ids (so the open-loops skill can act).

Bounded (MAX_CHARS), labelled as background, and FAIL-OPEN: any error means no injection,
never a broken turn. Cron and CLI turns get nothing.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

MAX_CHARS = 1200
KEEP_MINUTES = 30          # a conversation keeps its follow-up context for this long
MAX_FOLLOWUPS = 3
MAX_LOOPS = 3
FOLLOWUP_CHARS = 280

_pending: list[dict] = []
_pending_at: datetime.datetime | None = None


def _assistant():
    """(outbox, loops) from ~/.hermes/scripts/assistant (or the repo, in tests)."""
    here = Path(__file__).resolve().parent
    for d in (Path.home() / ".hermes" / "scripts" / "assistant", here.parent.parent / "scripts" / "assistant"):
        if (d / "outbox.py").exists():
            if str(d) not in sys.path:
                sys.path.insert(0, str(d))
            break
    import loops  # noqa: PLC0415
    import outbox  # noqa: PLC0415
    return outbox, loops


def _is_telegram(x) -> bool:
    return "telegram" in str(x or "").lower()


def _on_inbound(event=None, **kwargs):
    """pre_gateway_dispatch. Never blocks or rewrites a message (always returns None)."""
    global _pending, _pending_at
    try:
        src = getattr(event, "source", None)
        if not _is_telegram(getattr(src, "platform", None)) or not (getattr(event, "text", "") or "").strip():
            return None
        outbox, _loops = _assistant()
        now = outbox.now()
        fresh = outbox.unanswered(at=now)                 # BEFORE the stamp: what he may be answering
        if fresh or _pending_at is None or (now - _pending_at).total_seconds() > KEEP_MINUTES * 60:
            _pending, _pending_at = fresh[-MAX_FOLLOWUPS:], now
        outbox.note_inbound(now)
    except Exception:  # noqa: BLE001 — fail open
        pass
    return None


def build_context(pending: list[dict], open_rows: list, first_turn: bool) -> str:
    parts = []
    if pending:
        parts.append("Follow-ups YOU (Hermes) sent him recently that he had not answered. His message "
                     "may be a reply to one of them:")
        for r in pending:
            when = str(r.get("at", ""))[:16].replace("T", " ")
            tag = f" [loop:{r['loop_id']}]" if r.get("loop_id") else ""
            text = " ".join(str(r.get("text", "")).split())[:FOLLOWUP_CHARS]
            parts.append(f"- {when}{tag}: {text}")
    if first_turn and open_rows:
        parts.append("His open loops (things being tracked; ids are for the open-loops skill):")
        for lid, lp in open_rows[:MAX_LOOPS]:
            step = f" | next: {lp.get('next_step')}" if lp.get("next_step") else ""
            parts.append(f"- [loop:{lid}] {lp.get('title')}{step}"[:240])
    if not parts:
        return ""
    head = ("[Assistant background — for context only. Do NOT bring these up unless his message is "
            "about them. If he answers a follow-up, act on it with the open-loops skill.]")
    return "\n".join([head, *parts])[:MAX_CHARS]


def _inject(platform=None, is_first_turn=None, **kwargs):
    """pre_llm_call."""
    try:
        if not _is_telegram(platform):
            return None
        outbox, loops = _assistant()
        now = outbox.now()
        pending = _pending if (_pending_at and (now - _pending_at).total_seconds() <= KEEP_MINUTES * 60) else []
        rows = loops.open_loops(loops.load()) if is_first_turn else []
        ctx = build_context(pending, rows, bool(is_first_turn))
        return {"context": ctx} if ctx else None
    except Exception:  # noqa: BLE001 — fail open: no context beats a broken turn
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _on_inbound)
    ctx.register_hook("pre_llm_call", _inject)
