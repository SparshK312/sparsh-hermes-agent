#!/usr/bin/env python3
"""
board.py — the ONE supported way to change a role's status on the board.

🔴 READ THIS FIRST, IT IS THE WHOLE POINT OF THE FILE:

    THE GOOGLE SHEET IS THE SOURCE OF TRUTH FOR HUMAN FIELDS.
    EDITING curated_postings.json DIRECTLY DOES NOT WORK.

`write_board()` performs a LATE read-back from the Sheet and merges it over the store
immediately before rendering. So a status written into the JSON is silently reverted by
the very next refresh — the Sheet's older value wins. This was discovered the hard way
on 2026-08-28: a Roblox rejection was written into the JSON, the board still said `OA`
afterwards, and the JSON edit had been overwritten within seconds.

Human fields (Sheet owns): status · applied_date · notes · priority_override
Machine fields (store owns): everything else — never hand-edit those.

USAGE
  board.py applied  <match> [--date YYYY-MM-DD] [--notes "..."]
  board.py status   <match> "<Status>" [--notes "..."]
  board.py note     <match> "<text>"
  board.py show     <match>
  board.py list-live

  <match> is any distinctive fragment of the URL, company, or role. Ambiguous matches
  are REFUSED rather than guessed — writing a status onto the wrong posting is worse
  than not writing it.

Valid Status values (they are a dropdown on the Sheet; anything else will look broken):
  To Apply · Applied · OA · Phone Screen · Onsite · Offer · Rejected ·
  Networking · On Hold · Skip · Not a Fit · Closed

AFTER RUNNING: the change is live on the Sheet immediately. The JSON store catches up
on the next `curate.py` run, which is also when the row re-routes between tabs (setting
`Applied` moves it from Apply Now to My Applications).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

VAULT_SCRIPTS = Path("/Users/sparshk/Documents/School Vault - UofT/Scripts")
sys.path.insert(0, str(VAULT_SCRIPTS))

import build_curated_gsheet as G  # noqa: E402

VALID = ["To Apply", "Applied", "OA", "Phone Screen", "Onsite", "Offer", "Rejected",
         "Networking", "On Hold", "Skip", "Not a Fit", "Closed"]
TABS = (G.TAB_QUEUE, G.TAB_APPS, G.TAB_REVIEWED)


def _find(needle: str):
    """-> (tab, row_number, headers, row). Refuses an ambiguous match.

    Prefix `id:` forces an EXACT match on the hidden _id column instead of the
    substring-anywhere search. Added 2026-09-05 because substring matching cannot
    address a row whose _id is a PREFIX of another row's: one Amazon Robotics req
    (10529525) occupies five rows sourced from five places, and
    "www.amazon.jobs/jobs/10529525" is a substring of ".../10529525/apply", so every
    fragment was refused as ambiguous and the row could not be marked at all.
    Usage:  board.py status "id:www.amazon.jobs/jobs/10529525" "On Hold" --notes "..."
    """
    if needle.startswith("id:"):
        want = needle[3:].strip().lower()
        for tab in TABS:
            h = G._HEADERS[tab]
            rows = G.values_get(G.SHEET_ID_DEFAULT,
                                f"{G._q(tab)}!A1:{G._col_letter(len(h))}500")
            for i, r in enumerate(rows[1:], start=2):
                if r and str(r[0]).strip().lower() == want:
                    return (tab, i, h, r)
        sys.exit(f"no row has _id exactly {want!r}. Try: board.py show <fragment>")
    n = needle.lower()
    hits = []
    for tab in TABS:
        h = G._HEADERS[tab]
        rows = G.values_get(G.SHEET_ID_DEFAULT,
                            f"{G._q(tab)}!A1:{G._col_letter(len(h))}500")
        for i, r in enumerate(rows[1:], start=2):
            blob = " ".join(str(c) for c in r).lower()
            if n in blob:
                hits.append((tab, i, h, r))
    if not hits:
        sys.exit(f"no row matches {needle!r}. Try: board.py list-live")
    if len(hits) > 1:
        print(f"{needle!r} matches {len(hits)} rows — be more specific:", file=sys.stderr)
        for tab, i, h, r in hits[:8]:
            g = lambda k: (r[h.index(k)] if k in h and h.index(k) < len(r) else "")  # noqa: E731
            print(f"    [{tab} row {i}] {g('Company')} — {g('Role')[:52]}", file=sys.stderr)
        sys.exit(1)
    return hits[0]


def _set(tab: str, row: int, headers: list, field: str, value: str) -> None:
    if field not in headers:
        return
    col = G._col_letter(headers.index(field) + 1)
    G.values_update(G.SHEET_ID_DEFAULT, f"{G._q(tab)}!{col}{row}", [[value]])


def _arg(flag: str, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]

    if cmd == "list-live":
        h = G._HEADERS[G.TAB_QUEUE]
        rows = G.values_get(G.SHEET_ID_DEFAULT,
                            f"{G._q(G.TAB_QUEUE)}!A1:{G._col_letter(len(h))}60")
        for i, r in enumerate(rows[1:], start=2):
            g = lambda k: (r[h.index(k)] if h.index(k) < len(r) else "")  # noqa: E731
            print(f"  #{i-1:<4} Fit={g('Fit'):<4} {g('Company')[:16]:<16} {g('Role')[:50]}")
        return 0

    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    needle = sys.argv[2]
    tab, row, headers, r = _find(needle)
    g = lambda k: (r[headers.index(k)] if k in headers and headers.index(k) < len(r) else "")  # noqa: E731

    if cmd == "show":
        print(f"  tab      : {tab} (row {row})")
        for k in ("Company", "Role", "Status", "Fit", "Applied", "Notes"):
            if k in headers:
                print(f"  {k:<9}: {g(k)}")
        return 0

    if cmd == "applied":
        _set(tab, row, headers, "Status", "Applied")
        _set(tab, row, headers, "Applied", _arg("--date", date.today().isoformat()))
        if _arg("--notes"):
            _set(tab, row, headers, "Notes", _arg("--notes"))
        print(f"✅ Applied — {g('Company')} — {g('Role')[:54]}")
        print("   Moves to My Applications on the next curate.py run.")
        return 0

    if cmd == "status":
        if len(sys.argv) < 4:
            sys.exit("usage: board.py status <match> \"<Status>\"")
        val = sys.argv[3]
        if val not in VALID:
            sys.exit(f"{val!r} is not a valid Status.\nValid: {' · '.join(VALID)}")
        _set(tab, row, headers, "Status", val)
        if _arg("--notes"):
            _set(tab, row, headers, "Notes", _arg("--notes"))
        print(f"✅ {val} — {g('Company')} — {g('Role')[:54]}")
        return 0

    if cmd == "note":
        if len(sys.argv) < 4:
            sys.exit("usage: board.py note <match> \"<text>\"")
        _set(tab, row, headers, "Notes", sys.argv[3])
        print(f"✅ note set — {g('Company')} — {g('Role')[:54]}")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
