#!/usr/bin/env python3
"""
today_actions.py — print ONLY the parts of Action Items.md that concern today.

WHY: measured 2026-08-29, `read_file` on `00 - Dashboard/Action Items.md` accounted for
**474,390 tokens — 46% of ALL tool content across the last ~4,000 messages.** The file is
26 KB and the daily-note-prefill cron read it whole, 73 times.

It did that because the cron prompt told it to *"lift bullets under the Hard Deadlines
section"* — and **there is no Hard Deadlines section.** The file is organised into dated
day sections. So the agent had no choice but to pull all 26 KB and reason over it.

This prints the always-relevant section plus any section dated today, typically a few
hundred bytes. Deterministic, no model involved.

  today_actions.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

VAULT = Path(os.environ.get("HERMES_VAULT", "/home/hermes/vault"))
SRC = VAULT / "00 - Dashboard" / "Action Items.md"
ALWAYS = re.compile(r"runs every day|every day", re.I)


def _wanted(header: str, d: date) -> bool:
    h = header.lower()
    if ALWAYS.search(h):
        return True
    if "today" in h:
        return True   # caller re-checks staleness; see _stale_note()
    # "### 📅 THU AUG 27", "### 📅 FRI 28 → PERU", "Wed Aug 26"
    day_abbr = d.strftime("%a").lower()          # thu
    mon_abbr = d.strftime("%b").lower()          # aug
    dnum = str(d.day)
    if day_abbr in h and (mon_abbr in h or re.search(rf"\b{dnum}\b", h)):
        return True
    if mon_abbr in h and re.search(rf"\b{dnum}\b", h):
        return True
    if d.isoformat() in h:
        return True
    return False


_MON = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def _stale_note(header: str, d: date) -> str:
    """A section titled TODAY that carries a DIFFERENT explicit date is stale.

    Without this the extractor cheerfully hands the agent a block headed
    "TODAY — Wed Aug 26" on Aug 29 and it reads as current. Flag it rather than
    dropping it: the tasks may still matter, but they are not today's.
    """
    h = header.lower()
    if "today" not in h:
        return ""
    m = re.search(r"\b(" + "|".join(_MON) + r")\w*\s+(\d{1,2})\b", h)
    if not m:
        return ""
    mon = _MON.index(m.group(1)) + 1
    day = int(m.group(2))
    if (mon, day) != (d.month, d.day):
        return (f"   ⚠️ STALE: this section is labelled TODAY but dated "
                f"{m.group(1).title()} {day}; today is {d.strftime('%b %-d')}. "
                f"Treat as carry-over, not as today's plan.")
    return ""


def main() -> int:
    d = date.today()
    if "--date" in sys.argv:
        d = datetime.strptime(sys.argv[sys.argv.index("--date") + 1], "%Y-%m-%d").date()
    if not SRC.is_file():
        print(f"(Action Items.md not found at {SRC})")
        return 1

    lines = SRC.read_text().splitlines()
    out, keep = [], False
    for ln in lines:
        if re.match(r"^#{2,4}\s", ln):
            keep = _wanted(ln, d)
            if keep:
                out.append("")
                out.append(ln + _stale_note(ln, d))
            continue
        if keep:
            out.append(ln)

    text = "\n".join(out).strip()
    if not text:
        print(f"(no Action Items section matches {d.isoformat()} — nothing due today)")
        return 0
    print(f"# Action Items relevant to {d.isoformat()}  "
          f"(extracted from Action Items.md — do NOT read the whole file)")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
