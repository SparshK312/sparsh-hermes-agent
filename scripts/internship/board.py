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
  board.py status   <match> "<Status>" [--due YYYY-MM-DD|clear] [--notes "..."]
  board.py note     <match> "<text>"
  board.py priority <match> <P0|P1|P2|P3|clear> [--notes "..."]
  board.py show     <match> [<match> ...]
  board.py list-live

  <match> is any distinctive fragment of the URL, company, or role. For a WRITE an
  ambiguous match is REFUSED rather than guessed — writing a status onto the wrong
  posting is worse than not writing it — and the refusal lists every candidate with
  its `id:` so the next attempt is exact. `show` is a READ and never refuses: it
  prints every row that matches (or nearly matches) each fragment, with its status,
  and "not on the board" when nothing does. `show "Figma" "Waymo" "Tesla"` answers
  "did I apply to these?" in ONE call — one Sheet fetch, one line per row.
  (2026-09-21: an agent turn spent 41 model calls rewording fragments against the old
  "no row matches … be more specific" errors. A miss is an answer, not an error.)

PRIORITY ranks WITHIN a tier (since 2026-09-15; it was the first key 2026-09-05 -> 09-15):
the queue reads S -> A -> B -> C, and inside each tier a P0 row -- and its whole company
block -- floats to the top of that tier. Convention set
2026-09-05, because the column had sat ENTIRELY EMPTY and there was no way to tell a
row that had been vetted from one nobody had ever opened:

  P0      reviewed, strong fit -- apply
  P1      reviewed, viable
  P2      reviewed, marginal -- Sparsh's call
  (blank) NOT YET REVIEWED

Valid Status values (they are a dropdown on the Sheet; anything else will look broken):
  To Apply · Applied · OA · Phone Screen · Technical Interview · Onsite · Offer ·
  Rejected · Rejected after OA · Rejected after Interview · Networking · On Hold ·
  Skip · Not a Fit · Closed
  (the list is STATUS_OPTS in build_curated_xlsx.py — one vocabulary, imported here)

AFTER RUNNING: the change is live on the Sheet immediately. The JSON store catches up
on the next `curate.py` run, which is also when the row re-routes between tabs (setting
`Applied` moves it from Apply Now to My Applications).
"""
from __future__ import annotations

import sys
import re
from datetime import date
from pathlib import Path

# The vocabulary comes from THIS directory's build_curated_xlsx (the one the VPS renders
# with), and is imported BEFORE the vault path is prepended so the vault's stale copy of
# the same module cannot shadow it.
from build_curated_xlsx import STATUS_OPTS  # noqa: E402
from board_match import find_by_id, find_rows  # noqa: E402

VAULT_SCRIPTS = Path("/Users/sparshk/Documents/School Vault - UofT/Scripts")
sys.path.insert(0, str(VAULT_SCRIPTS))

import build_curated_gsheet as G  # noqa: E402

VALID = list(STATUS_OPTS)
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
        # 🔴 Case-sensitive first, and an ambiguous id is REFUSED like any other match.
        # Two rows can carry _ids that differ only by case (jobs.ashbyhq.com/Sierra/...
        # and .../sierra/... are one posting picked up twice); the old code lowercased
        # both sides and returned the first hit, and on 2026-09-12 that put Skip on the
        # row that was meant to be kept. Matching lives in board_match.py so it is
        # testable without the vault import above.
        want = needle[3:].strip()
        rows_by_tab = {}
        for tab in TABS:
            h = G._HEADERS[tab]
            rows_by_tab[tab] = G.values_get(G.SHEET_ID_DEFAULT,
                                            f"{G._q(tab)}!A1:{G._col_letter(len(h))}500")
        hits = find_by_id(want, rows_by_tab)
        if not hits:
            sys.exit(f"no row has _id exactly {want!r}. Try: board.py show <fragment>")
        if len(hits) > 1:
            print(f"{needle!r} matches {len(hits)} rows (case-insensitively) — give the exact case:",
                  file=sys.stderr)
            for tab, i, r in hits[:8]:
                print(f"    [{tab} row {i}] _id={r[0]}", file=sys.stderr)
            sys.exit(1)
        tab, i, r = hits[0]
        return (tab, i, G._HEADERS[tab], r)
    rows_by_tab, headers_by_tab = _fetch_tabs()
    mode, hits = find_rows(needle, rows_by_tab, headers_by_tab)
    # A unique row that contains the whole fragment, or every word of it, is the row
    # he means. Anything looser ('nearest') is a guess, and writes do not guess.
    if mode in ("exact", "tokens") and len(hits) == 1:
        return hits[0]
    if mode == "none":
        sys.exit(f"no row on any tab mentions {needle!r} — nothing to write to. "
                 f"Check the company name (board.py show \"<company>\").")
    # 🔴 Refuse — but hand over what the next attempt needs. The old message was
    # "be more specific" with company/role only, and the agent's way of being more
    # specific was to reword the fragment 35 times. Each candidate now carries its
    # status and its id:, so the write can be re-issued exactly, once.
    why = ("matches" if mode == "exact" else
           "matches every word of" if mode == "tokens" else "has no exact match; nearest to")
    print(f"{needle!r} {why} {len(hits)} rows — pick one by id: and re-run:", file=sys.stderr)
    for line in _lines(hits):
        print("    " + line, file=sys.stderr)
    sys.exit(1)


def _fetch_tabs():
    """One read per tab; every lookup in a call shares it."""
    rows_by_tab, headers_by_tab = {}, {}
    for tab in TABS:
        h = G._HEADERS[tab]
        rows_by_tab[tab] = G.values_get(G.SHEET_ID_DEFAULT,
                                        f"{G._q(tab)}!A1:{G._col_letter(len(h))}500")
        headers_by_tab[tab] = h
    return rows_by_tab, headers_by_tab


def _lines(hits, cap: int = 40) -> list:
    """One line per row: where it is, what it is, what state it is in, how to address
    it exactly. Capped so `show "Tesla"` (76 rows) stays readable; the cap is announced."""
    out = []
    for tab, i, h, r in hits[:cap]:
        g = lambda k: (r[h.index(k)] if k in h and h.index(k) < len(r) else "")  # noqa: E731
        state = f"Status={g('Status') or '(blank)'}"
        if g("Applied"):
            state += f"  Applied={g('Applied')}"
        if g("Due"):
            state += f"  Due={g('Due')}"
        out.append(f"[{tab} row {i}] {g('Company')} — {g('Role')[:60]}  ·  {state}  ·  id:{g('_id')}")
    if len(hits) > cap:
        out.append(f"… and {len(hits) - cap} more (showing {cap}). Add a role word to narrow.")
    return out


def _set(tab: str, row: int, headers: list, field: str, value: str) -> None:
    if field not in headers:
        # 🔴 Say it. This used to return silently, which is how "Applied" dates
        # vanished: the Apply Now tab has no "Applied" column, so the write was
        # dropped and the command still printed ✅. A write that reports success and
        # does nothing is the worst failure mode in this whole system.
        # (curate.py step 1c backfills the date on the next refresh.)
        print(f"   ⚠️  '{field}' is not a column on {tab} — value not written here "
              f"({field}={value!r}).", file=sys.stderr)
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

    if cmd == "show":
        # A read. It answers every fragment it is given, from one Sheet fetch, and it
        # does not exit 1 on ambiguity or on a miss: "these 4 Figma rows, all Applied"
        # and "nothing on the board mentions Together AI" are both the answer to the
        # question that was asked. (Before 2026-09-21 it refused like a write and the
        # agent retried for 15 minutes.)
        rows_by_tab, headers_by_tab = _fetch_tabs()
        for needle in sys.argv[2:]:
            if needle.startswith("id:"):
                hits = [(t, i, G._HEADERS[t], r) for t, i, r in
                        find_by_id(needle[3:], rows_by_tab)]
                mode = "exact" if hits else "none"
            else:
                mode, hits = find_rows(needle, rows_by_tab, headers_by_tab)
            if mode == "none":
                print(f"{needle!r} → NOT ON THE BOARD (no row on any tab shares a "
                      f"distinctive word with it). If the company is not named, it was "
                      f"never picked up — see the tier table / coverage digest.")
                continue
            head = {"exact":   f"{len(hits)} row(s) match",
                    "tokens":  f"{len(hits)} row(s) contain every word",
                    "nearest": f"no exact match; {len(hits)} nearest row(s)"}[mode]
            print(f"{needle!r} → {head}:")
            if len(hits) == 1:
                tab, row, headers, r = hits[0]
                g = lambda k: (r[headers.index(k)] if k in headers and headers.index(k) < len(r) else "")  # noqa: E731
                print(f"  tab      : {tab} (row {row})")
                for k in ("Company", "Role", "Status", "Fit", "Applied", "Due", "Notes"):
                    if k in headers:
                        print(f"  {k:<9}: {g(k)}")
                print(f"  id       : id:{g('_id')}")
            else:
                for line in _lines(hits):
                    print("  " + line)
        return 0

    needle = sys.argv[2]
    tab, row, headers, r = _find(needle)
    g = lambda k: (r[headers.index(k)] if k in headers and headers.index(k) < len(r) else "")  # noqa: E731

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
        # --due writes the deadline for anything he now owes (an OA window, a form).
        # Only on the tabs that carry the column; _set warns rather than silently
        # dropping the write when a tab has no such column (2026-09-05 rule).
        due = _arg("--due")
        if due:
            # `--due clear` empties the cell, matching `priority <m> clear`. The Sheet is
            # authoritative including blanks (2026-09-05), so a cleared Due clears in the
            # store too. Needed because an "OA - Done" row must NOT carry a deadline.
            _set(tab, row, headers, "Due", "" if due.lower() == "clear" else due)
        # 🔴 An application with no date is a silent data loss (added 2026-09-05).
        # `board.py applied` stamps today's date; `board.py status <m> "Applied"` did
        # not — and "Applied" is in the status vocabulary, so it is the natural thing
        # to type. Result: 15 real applications (9 Tesla, 6 Microsoft) sat on the board
        # undated, which breaks every "when did I apply / how long has it been" answer
        # and cannot be reconstructed later without digging through Gmail.
        # Only fills an EMPTY cell, so re-running never rewrites a real date.
        if val == "Applied" and not (g("Applied") or "").strip():
            _set(tab, row, headers, "Applied", _arg("--date", date.today().isoformat()))
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

    if cmd == "date":
        # Set the Applied date on its own — for rows whose Status is not "Applied" (OA,
        # Phone Screen, Rejected …) where `applied` would clobber the status. Added
        # 2026-09-06 when a TikTok screenshot supplied a date the board had lost.
        if len(sys.argv) < 4:
            sys.exit("usage: board.py date <match> YYYY-MM-DD")
        val = sys.argv[3].strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
            sys.exit("date must be YYYY-MM-DD")
        _set(tab, row, headers, "Applied", val)
        print(f"✅ Applied date {val} — {g('Company')} — {g('Role')[:54]}")
        return 0

    if cmd == "priority":
        if len(sys.argv) < 4:
            sys.exit('usage: board.py priority <match> <P0|P1|P2|P3|clear>')
        val = sys.argv[3].strip().upper()
        if val in ("CLEAR", "NONE"):
            val = ""
        elif val not in ("P0", "P1", "P2", "P3"):
            sys.exit(f"priority must be P0, P1, P2, P3 or clear -- got {sys.argv[3]!r}. "
                     "Anything else renders as broken (it is a dropdown on the Sheet).")
        _set(tab, row, headers, "Priority", val)
        # --notes in the same call, like `status` and `applied`: a priority without the
        # reason it was set is the "reviewed vs never opened" ambiguity the column exists
        # to remove, and a second round trip per row doubles a 280-row triage pass
        # (added 2026-09-13).
        if _arg("--notes"):
            _set(tab, row, headers, "Notes", _arg("--notes"))
        print(f"\u2705 priority {val or '(cleared)'} -- {g('Company')} -- {g('Role')[:48]}")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
