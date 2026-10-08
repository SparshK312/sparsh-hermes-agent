#!/usr/bin/env python3
"""
today_actions.py — print ONLY the parts of Action Items.md that concern today.

WHY: measured 2026-08-29, `read_file` on `00 - Dashboard/Action Items.md` accounted for
**474,390 tokens — 46% of ALL tool content across the last ~4,000 messages.** The file is
26 KB and the daily-note-prefill cron read it whole, 73 times.

It did that because the cron prompt told it to *"lift bullets under the Hard Deadlines
section"* — and **there is no Hard Deadlines section.** The file is organised into dated
day sections. So the agent had no choice but to pull all 26 KB and reason over it.

This prints the open rows of the THIS WEEK table, each tagged [TODAY] or [WEEK], capped at
4,000 characters (loudly). Deterministic, no model involved. (Rewritten 2026-10-08: it was printing
~30 KB of stale sections a day.)

  today_actions.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

VAULT = Path(os.environ.get("HERMES_VAULT", "/home/hermes/vault"))
SRC = VAULT / "00 - Dashboard" / "Action Items.md"
ALWAYS = re.compile(r"runs every day|every day", re.I)
# The live task list is the table under "### 🔥 THIS WEEK …" (since late Sep 2026); its
# header never carries today's date. Anchor on the words, not the emoji (anchors rot).
# "THIS WEEK" must OPEN the heading (after an optional emoji): the archived "🥇 THE STACK"
# header mentions THIS WEEK deep in its text and was matched first (caught in testing).
THIS_WEEK = re.compile(r"^#{2,4}\s+(?:[^\w\s]+\s*)?THIS WEEK\b")
# A section or row marked like this is finished/archived and must not become a task.
ARCHIVED = re.compile(r"🗄️|\bSPENT\b|superseded|SUPERSEDED|\(was \"TODAY\"\)")
DONE_LEAD = re.compile(r"^\s*(✅|🗄️|~~)")
OPEN_MARK = "⬜"
CELL_MAX = 260
TOTAL_MAX = 4000

_MON = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def _wanted(header: str, d: date) -> bool:
    """A non-table section is relevant only when it carries TODAY'S date (or runs every day).

    2026-10-08: the old rules accepted a bare "today" anywhere in a header and weekday+month
    without the day number, so "Thu" + "Oct" matched every Thursday in October and
    "🗄️ Thu Sep 17 (was TODAY)" / "APPLICATIONS — today's wave" were served as today's plan
    (30 KB a day into the prefill). Archived headers never match."""
    h = header.lower()
    if ARCHIVED.search(header):
        return False
    if ALWAYS.search(h):
        return True
    if d.isoformat() in h:
        return True
    dnum = rf"\b{d.day}\b"
    day_abbr = d.strftime("%a").lower()
    mon_abbr = d.strftime("%b").lower()
    if re.search(rf"\b{mon_abbr}\w*\s+{d.day}\b", h):          # "oct 8", "october 8"
        return True
    if re.search(rf"\b{day_abbr}\w*\s+{d.day}\b", h) and not re.search(  # "thu 8" with no other month
            r"\b(" + "|".join(m for m in _MON if m != mon_abbr) + r")\w*\s+\d", h):
        return True
    return False


def _stale_note(text: str, d: date) -> str:
    """'TODAY <date>' with a date that is not today is carry-over, not today's plan."""
    t = text.lower()
    m = re.search(r"\btoday\b[^|]{0,20}?\b(" + "|".join(_MON) + r")\w*\s+(\d{1,2})\b", t)
    if not m:
        return ""
    mon, day = _MON.index(m.group(1)) + 1, int(m.group(2))
    if (mon, day) != (d.month, d.day):
        return f" ⚠️ STALE (labelled TODAY {m.group(1).title()} {day}; today is {d.strftime('%b %-d')})"
    return ""


def _cell(c: str) -> str:
    c = c.strip()
    return c if len(c) <= CELL_MAX else c[:CELL_MAX].rstrip() + "…"


_DATE_RE = re.compile(r"\b(" + "|".join(_MON) + r")\w*\.?\s+(\d{1,2})\b", re.I)
SOON_DAYS = 2


def _dates(text: str, d: date) -> list[date]:
    out = []
    for m in _DATE_RE.finditer(text):
        try:
            out.append(date(d.year, _MON.index(m.group(1).lower()[:3]) + 1, int(m.group(2))))
        except ValueError:
            pass
    return out


def _when_tag(when: str, what: str, d: date) -> str:
    """TODAY or WEEK, deterministically, so the prefill adds only today's work as tasks
    (review, 2026-10-08: "After passing…" and "~early Nov" rows were becoming today's
    tasks). TODAY = the When names today, a DAILY range that covers today, a deadline within
    SOON_DAYS, or a When cell that opens with ⬜ (a decision waiting on him). A ⬜ elsewhere
    ("⬜ he reviews it when he applies") is open but not necessarily today's."""
    w = when.split("🗄️")[0]
    if w.strip().startswith(OPEN_MARK):            # "⬜ His yes/no" — a decision waiting on him
        return "TODAY"
    ds = _dates(w, d)
    if "daily" in w.lower():
        return "TODAY" if (not ds or max(ds) >= d) and (not ds or min(ds) <= d) else "WEEK"
    if any(x == d for x in ds) or any(x == d for x in _dates(what.split("🗄️")[0], d)):
        return "TODAY"                              # an event dated today, in either cell
    if re.search(r"\b(by|due|closes|deadline)\b", w, re.I) and any(d <= x <= d + timedelta(days=SOON_DAYS) for x in ds):
        return "TODAY"
    return "WEEK"


def this_week_rows(lines: list[str], d: date) -> tuple[bool, list[str]]:
    """(found_table, open rows) from the THIS WEEK table, each tagged [TODAY] or [WEEK].

    A row is open unless its When cell leads with ✅ / 🗄️ / ~~. Such a row is still kept
    when its When or What carries ⬜ ("✅ test SUBMITTED · ⬜ voice round still to do") —
    but NOT for a ⬜ in the Note column (review: a stale "⬜ open decision" note revived a
    finished row). The ⬜ step is always printed, even past the cell cut."""
    found, inside, rows = False, False, []
    for ln in lines:
        if re.match(r"^#{1,4}\s", ln):
            if inside and rows:
                break
            inside = bool(THIS_WEEK.match(ln)) and not ARCHIVED.search(ln)
            found = found or inside
            continue
        if not inside or not ln.startswith("|"):
            continue
        cells = [c for c in ln.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0].strip()) <= set("-: ") or cells[0].strip() == "When":
            continue
        when, what = cells[0], cells[1]
        has_open = OPEN_MARK in when or OPEN_MARK in what
        if DONE_LEAD.match(when) and not has_open:
            continue
        text = f"{_cell(when)} — {_cell(what)}"
        if has_open and OPEN_MARK not in text:
            text += " · " + " · ".join(f.strip() for f in re.findall(r"⬜[^·|]{0,160}", when + " " + what))
        tag = _when_tag(when, what, d)
        rows.append(f"- [{tag}] {text}{_stale_note(when.split('🗄️')[0], d)}")
    return found, rows


def build(text: str, d: date) -> str:
    lines = text.splitlines()
    found, rows = this_week_rows(lines, d)
    parts = [f"# Action Items relevant to {d.isoformat()}  "
             f"(extracted from Action Items.md — do NOT read the whole file)"]
    if not found:
        parts.append("⚠️ No 'THIS WEEK' table found in Action Items.md — its format changed. "
                     "Open tasks are UNKNOWN (not none); say so instead of inventing or omitting tasks.")
    else:
        parts.append("## Open rows from THIS WEEK (finished ✅/🗄️ rows removed; ⬜ = still to do; "
                     "[TODAY] = due or planned today, [WEEK] = this week, not a task for today)")
        today_rows = [r for r in rows if r.startswith("- [TODAY]")]
        week_rows = [r for r in rows if r.startswith("- [WEEK]")]
        parts += (today_rows + week_rows) or ["(every row in THIS WEEK is marked done)"]
    # Dated non-table sections are no longer read (review, 2026-10-08: on a deadline date
    # they revived stale sections like a Sep 27 "OA STATE … DUE OCT 10–11").
    out = "\n".join(parts).strip()
    if len(out) > TOTAL_MAX:
        out = (out[:TOTAL_MAX].rstrip() +
               f"\n… [TRUNCATED at {TOTAL_MAX} chars of {len(out)} — some open rows are not shown]")
    return out


def main() -> int:
    d = date.today()
    if "--date" in sys.argv:
        d = datetime.strptime(sys.argv[sys.argv.index("--date") + 1], "%Y-%m-%d").date()
    if not SRC.is_file():
        print(f"(Action Items.md not found at {SRC} — open tasks UNKNOWN)")
        return 1
    print(build(SRC.read_text(), d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
