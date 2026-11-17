#!/usr/bin/env python3
"""
outbox.py — the one record of what Hermes has sent him, and the one budget for
messages he did not ask for.

WHY (2026-11-16). Messages left from about thirteen places through four mechanisms.
Only the coach.py modes shared a budget, and that budget FAILED OPEN: coach_engine's
_load_state returned {} on a corrupt file and _save_state swallowed write errors, so a
broken state file meant no limit at all. Nothing recorded what was said, so nothing could
avoid repeating it, and the chat agent could not know what a nudge had asked.

Two functions, deliberately separate:

  record(...)  the LEDGER. Every sender calls it after a confirmed send. Append-only JSONL.
  allow(...)   the POLICY, for unprompted messages only ("nudge"). It reads the ledger.

Kinds:
  nudge     unprompted, counted against the daily cap (rescues, the check-in tick)
  digest    scheduled summaries he expects (morning brief, weekly/monthly check-in, cards)
  reactive  a reply to something he just did (session review, chat)
  alert     a system failure he must see; never blocked, always recorded

Policy (his calls, 2026-11-16): at most 4 nudges a day; quiet from 22:00 until the morning
brief has gone out (or MORNING_FALLBACK if it has not); one TOPIC once per day across every
kind, so the brief and the tick cannot both raise it; at most MAX_ASKS_PER_LOOP asks about
one open loop without a reply; a question still unanswered blocks another QUESTION for
QUESTION_LOCK_H hours; after BACKOFF_STREAK nudges in a row with no reply, nudges stop
until he writes (the brief still goes).

FAILS CLOSED. A ledger that exists and cannot be read blocks nudges (and says why). The
reply-dependent rules apply only once the assistant-context plugin has written
last_inbound.json; without it "has he replied" is UNKNOWN, never "no".

Stdlib only; runs on the system python and under the framework venv.

  outbox.py record --kind alert --topic sync-watchdog --text "…"    (for shell senders)
  outbox.py show [--hours 24]
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
KINDS = ("nudge", "digest", "reactive", "alert")

DAILY_NUDGE_CAP = 4
QUIET_START = datetime.time(22, 0)
MORNING_FLOOR = datetime.time(7, 0)        # never before this, brief or not
MORNING_FALLBACK = datetime.time(11, 0)    # the brief's latest cutoff; quiet ends here regardless
MAX_ASKS_PER_LOOP = 2
QUESTION_LOCK_H = 24
BACKOFF_STREAK = 5
BRIEF_TOPIC = "morning-brief"
TEXT_MAX = 4000


def base_dir() -> Path:
    return Path(os.environ.get("HERMES_ASSISTANT_DIR", str(Path.home() / ".hermes" / "assistant")))


def ledger_path() -> Path:
    return base_dir() / "outbox.jsonl"


def inbound_path() -> Path:
    return base_dir() / "last_inbound.json"


class LedgerError(RuntimeError):
    """The ledger exists and cannot be read. Never treated as 'nothing sent'."""


def now() -> datetime.datetime:
    return datetime.datetime.now(TZ)


@contextlib.contextmanager
def _locked():
    base_dir().mkdir(parents=True, exist_ok=True)
    import fcntl
    with open(base_dir() / "outbox.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def read(since: datetime.datetime | None = None) -> list[dict]:
    """Every ledger row (optionally only those at/after `since`), oldest first.
    Missing file = empty ledger. An unparseable line raises LedgerError."""
    p = ledger_path()
    if not p.exists():
        return []
    rows = []
    try:
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not ln.strip():
                continue
            row = json.loads(ln)
            row["_at"] = datetime.datetime.fromisoformat(row["at"])
            if not isinstance(row.get("kind"), str) or not isinstance(row.get("topic"), str):
                raise ValueError("row without kind/topic")
            rows.append(row)
    except Exception as e:  # noqa: BLE001
        raise LedgerError(f"{p} line {i if 'i' in dir() else '?'} unreadable: {type(e).__name__}: {e}") from e
    return [r for r in rows if since is None or r["_at"] >= since]


def record(kind: str, topic: str, text: str = "", *, loop_id: str | None = None,
           expects_reply: bool = False, topics: list[str] | None = None,
           at: datetime.datetime | None = None) -> dict:
    """Append one SENT message to the ledger. Call only after the send was confirmed.
    `topics` lets a digest claim several topics at once (the brief names what it raised)."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    row = {"at": (at or now()).isoformat(), "kind": kind, "topic": str(topic),
           "text": (text or "")[:TEXT_MAX]}
    if loop_id:
        row["loop_id"] = str(loop_id)
    if expects_reply:
        row["expects_reply"] = True
    if topics:
        row["topics"] = [str(t) for t in topics]
    with _locked():
        with open(ledger_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def last_inbound() -> datetime.datetime | None:
    """When he last wrote to Hermes, or None when that is UNKNOWN (plugin not installed
    yet, file unreadable). Unknown is never read as 'he has not replied'."""
    try:
        return datetime.datetime.fromisoformat(json.loads(inbound_path().read_text())["at"])
    except Exception:  # noqa: BLE001
        return None


def note_inbound(at: datetime.datetime | None = None) -> None:
    """Stamp 'he just wrote'. Called by the assistant-context plugin on every inbound message."""
    base_dir().mkdir(parents=True, exist_ok=True)
    tmp = inbound_path().with_name("last_inbound.json.tmp")
    tmp.write_text(json.dumps({"at": (at or now()).isoformat()}))
    os.replace(tmp, inbound_path())


def _row_topics(row: dict) -> set[str]:
    return {row["topic"], *row.get("topics", [])}


def topic_used_today(topic: str, at: datetime.datetime | None = None) -> bool:
    n = at or now()
    day = n.astimezone(TZ).date()
    return any(topic in _row_topics(r) for r in read() if r["_at"].astimezone(TZ).date() == day)


def unanswered(max_age_h: float = 18, at: datetime.datetime | None = None) -> list[dict]:
    """Nudges/digests sent in the last `max_age_h` hours that he has not written after.
    With no inbound stamp at all, everything in the window counts as unanswered."""
    n = at or now()
    lb = last_inbound()
    out = []
    for r in read(since=n - datetime.timedelta(hours=max_age_h)):
        if r["kind"] in ("nudge", "digest") and (lb is None or r["_at"] > lb):
            out.append(r)
    return out


def allow(kind: str, topic: str, *, loop_id: str | None = None, expects_reply: bool = False,
          at: datetime.datetime | None = None) -> tuple[bool, str]:
    """May this message go out now? (ok, why). Only 'nudge' is ever refused."""
    if kind not in KINDS:
        return False, f"unknown kind {kind!r}"
    if kind != "nudge":
        return True, "not budgeted"
    n = (at or now()).astimezone(TZ)
    try:
        rows = read()
    except LedgerError as e:
        return False, f"ledger unreadable, refusing to nudge: {e}"
    today = [r for r in rows if r["_at"].astimezone(TZ).date() == n.date()]

    t = n.time()
    if t >= QUIET_START or t < MORNING_FLOOR:
        return False, "quiet hours"
    brief_out = any(BRIEF_TOPIC in _row_topics(r) for r in today)
    if not brief_out and t < MORNING_FALLBACK:
        return False, "quiet until the morning brief"

    if sum(1 for r in today if r["kind"] == "nudge") >= DAILY_NUDGE_CAP:
        return False, f"daily cap {DAILY_NUDGE_CAP} reached"
    if any(topic in _row_topics(r) for r in today):
        return False, f"topic {topic!r} already raised today"

    lb = last_inbound()
    if lb is not None:                       # reply-dependent rules need a known reply state
        nudges = [r for r in rows if r["kind"] == "nudge"]
        if loop_id:
            asks = [r for r in nudges if r.get("loop_id") == loop_id and r["_at"] > lb]
            if len(asks) >= MAX_ASKS_PER_LOOP:
                return False, f"asked about this {len(asks)} times with no reply"
        if expects_reply:
            open_q = [r for r in nudges if r.get("expects_reply") and r["_at"] > lb
                      and (n - r["_at"]).total_seconds() < QUESTION_LOCK_H * 3600]
            if open_q:
                return False, "a question is still unanswered"
        if len(nudges) >= BACKOFF_STREAK and all(r["_at"] > lb for r in nudges[-BACKOFF_STREAK:]):
            return False, f"{BACKOFF_STREAK} nudges in a row with no reply; waiting for him"
    return True, "ok"


def record_quiet(kind: str, topic: str, text: str = "", **kw) -> bool:
    """record() for senders that must never fail because of the ledger (alerts, cards).
    Returns False and logs to stderr instead of raising."""
    try:
        record(kind, topic, text, **kw)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[outbox] could not record {kind}/{topic}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="outbox")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="append a sent message (for shell senders)")
    r.add_argument("--kind", required=True, choices=KINDS)
    r.add_argument("--topic", required=True)
    r.add_argument("--text", default="")
    s = sub.add_parser("show", help="print recent ledger rows")
    s.add_argument("--hours", type=float, default=24)
    a = ap.parse_args(argv)
    if a.cmd == "record":
        return 0 if record_quiet(a.kind, a.topic, a.text) else 1
    try:
        rows = read(since=now() - datetime.timedelta(hours=a.hours))
    except LedgerError as e:
        print(f"LEDGER UNREADABLE: {e}")
        return 2
    for row in rows:
        print(f"{row['_at'].astimezone(TZ):%a %H:%M} {row['kind']:<8} {row['topic']:<22} "
              f"{(row.get('text') or '').splitlines()[0][:90] if row.get('text') else ''}")
    lb = last_inbound()
    print(f"-- {len(rows)} in the last {a.hours:g} h; he last wrote: "
          f"{lb.astimezone(TZ):%a %b %d %H:%M}" if lb else f"-- {len(rows)} in the last {a.hours:g} h; he last wrote: unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
