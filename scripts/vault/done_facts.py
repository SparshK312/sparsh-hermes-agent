#!/usr/bin/env python3
"""
done_facts.py — what has he already DONE, and is this copy of the vault current?

WHY (2026-10-08): Hermes kept telling him to do things he had already done. The 06:40
email triage turned "submit your interview availability" into a task the morning after he
submitted it — the vault log on this very machine already said "✅ … SUBMITTED". Nothing that prompts him ever looked at what was done. This module is the
one place that answers it, for every job that prompts him.

Two rules learned designing it (independent review, 2026-10-08):
  * A `decision` entry is a PLAN, never a completion ("his list for Wed: submit the
    interview availability…" was written the evening BEFORE). Only explicit completion
    markers count, and the model is told so.
  * There is NO deterministic fuzzy matching of free text here — a false "done" silently
    hides a real task, which is worse than a stale one. Callers give these lines to the
    model as evidence; deterministic drops are only for hard facts (a reply in the same
    email thread, an exact board row in a terminal state).

stdlib only and Python 3.9-safe: imported by the hermes venv (3.11) AND /usr/bin/python3
(3.9 on the Mac test runner, 3.12 on the VPS).
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HEADER = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})[^\]]*\]\s*(\w+)\s*\|\s*(.*?)\s+—\s+(.*)$")
# Scopes written by machines, not by him or his Mac sessions: Hermes jobs ("hermes:…") and
# the VPS logging skills ("log-food", "log-water", …).
MACHINE_SCOPE = re.compile(r"^\s*(hermes:|log-)", re.I)
# Explicit completion words. Used ONLY to label lines for the model, never to drop a task.
# Case-insensitive ("reply sent" counts, review 2026-10-08) EXCEPT "SAT", which must stay
# upper-case: lower-case "Sat" is Saturday ("closes Sat Oct 10"), not "sat the round".
DONE_WORD = re.compile(r"✅|(?i:\b(sent|submitted|applied|done|booked|confirmed|accepted|paid|"
                       r"filed|replied|scheduled|completed)\b)|\bSAT\b")

WATCHDOG_STATE = Path(os.environ.get("SYNC_WATCHDOG_STATE",
                                     str(Path.home() / ".hermes" / "sync_watchdog_state.json")))
# Problem kinds that mean "this copy of the vault is not receiving/sending changes".
# A stale Mac heartbeat is NOT one of them: a closed laptop makes no changes, so the VPS
# copy is as current as it can be (his call, 2026-10-08: only say "behind" when sync is
# actually broken).
BROKEN_KINDS = {"pending", "errors", "service", "nostate"}


def _day_files(vault: Path, start: date, end: date) -> list[Path]:
    out, d = [], start
    while d <= end:
        p = vault / "Log" / f"{d:%Y}" / f"{d:%Y-%m}" / f"{d:%Y-%m-%d}.md"
        if p.is_file():
            out.append(p)
        d += timedelta(days=1)
    return out


def recent_entries(vault: Path, days: int = 4, today: date | None = None,
                   include_machine: bool = False) -> list[dict]:
    """Log entries from the last `days` days (today included), oldest first, in file order."""
    today = today or date.today()
    out = []
    for p in _day_files(Path(vault), today - timedelta(days=days - 1), today):
        for ln in p.read_text("utf-8", "ignore").splitlines():
            m = HEADER.match(ln)
            if not m:
                continue
            d, action, scope, summary = m.groups()
            if d.startswith("9999"):
                continue
            machine = bool(MACHINE_SCOPE.match(scope))
            if machine and not include_machine:
                continue
            out.append({"date": d, "action": action.lower(), "scope": scope.strip(),
                        "summary": summary.strip(), "machine": machine})
    return out


def done_lines(vault: Path, days: int = 4, today: date | None = None,
               line_chars: int = 400, max_total: int = 24000) -> tuple[list[str], int]:
    """Compact evidence lines for a prompt.

    Each line is tagged so the model can tell a record of completion from a plan:
      [DONE?] the line carries an explicit completion word (✅/SENT/SUBMITTED/…)
      [PLAN]  a `decision` entry — a plan or a choice, NEVER evidence that a step happened
      [NOTE]  anything else
    Busy days carry 35+ entries (2026-10-07/08), so a plain newest-first cut covered only
    ~1.5 days and dropped the very completions this exists for. The budget is filled by
    priority — every [DONE?] line first, then [PLAN], then [NOTE] — each newest first,
    and the result is printed newest first. Returns (lines, omitted_count); callers must
    say so when omitted_count > 0.
    """
    tagged = []
    for i, e in enumerate(recent_entries(vault, days, today)):
        text = f"{e['scope']} — {e['summary']}"
        # tag on the FULL text, then cut (a completion word past the cut used to read NOTE)
        tag = "PLAN" if e["action"] == "decision" else ("DONE?" if DONE_WORD.search(text) else "NOTE")
        if len(text) > line_chars:
            text = text[:line_chars].rstrip() + "…"
        tagged.append((i, tag, f"{e['date']} [{tag}] {text}"))
    keep, total, omitted = [], 0, 0
    for want in ("DONE?", "PLAN", "NOTE"):
        for i, tag, ln in sorted((t for t in tagged if t[1] == want), key=lambda t: -t[0]):
            if total + len(ln) + 1 > max_total:
                omitted += 1
                continue
            keep.append((i, ln))
            total += len(ln) + 1
    return [ln for _, ln in sorted(keep, key=lambda t: -t[0])], omitted


def freshness(now: datetime | None = None, state_path: Path | None = None) -> dict:
    """Is the VPS copy of the vault receiving changes? Derived from the sync watchdog.

    state: "ok" | "sync_broken" | "unknown" (the watchdog itself has not run lately).
    """
    now = now or datetime.now(timezone.utc)
    sp = Path(state_path or WATCHDOG_STATE)
    try:
        st = json.loads(sp.read_text())
    except (OSError, ValueError):
        return {"state": "unknown", "why": "no sync-watchdog state"}
    try:
        checked = datetime.fromisoformat(st.get("checked", ""))
    except (TypeError, ValueError):
        return {"state": "unknown", "why": "sync-watchdog state has no timestamp"}
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    age_h = (now - checked).total_seconds() / 3600
    if age_h > 2:
        return {"state": "unknown", "why": f"sync watchdog last ran {age_h:.0f} h ago"}
    kinds = set(st.get("kinds") or [])
    if kinds & BROKEN_KINDS:
        return {"state": "sync_broken", "why": ", ".join(sorted(kinds & BROKEN_KINDS))}
    return {"state": "ok", "why": ""}


if __name__ == "__main__":
    import sys
    v = Path(os.environ.get("HERMES_VAULT", "/home/hermes/vault"))
    ls, om = done_lines(v, int(sys.argv[1]) if len(sys.argv) > 1 else 4)
    print("\n".join(ls))
    if om:
        print(f"({om} older entries omitted for size)")
    print(json.dumps(freshness()))
