"""Pure row-matching for board.py. No imports, no network, so it is testable on any
machine (board.py itself imports the vault's Sheets helpers and only loads on the Mac).

Added 2026-09-12 after `board.py status "id:jobs.ashbyhq.com/sierra/<uuid>" Skip`
marked the row whose _id was `jobs.ashbyhq.com/Sierra/<uuid>` (capital S). The `id:`
branch lowercased both sides and RETURNED THE FIRST HIT, so two rows whose _ids differ
only by case were indistinguishable and the promise in _find's docstring ("refuses an
ambiguous match") did not hold for the one branch built to be precise. The wrong row
ended up carrying Skip, P0 and the staging note at the same time.
"""
from __future__ import annotations


def find_by_id(want: str, rows_by_tab: dict) -> list:
    """-> [(tab, row_number, row), ...] whose _id (column A) matches `want`.

    Case-SENSITIVE exact matches win outright. Only if there are none does the
    case-insensitive comparison apply, and then every such row is returned so the
    caller can refuse the ambiguity instead of guessing. `rows_by_tab` maps tab name to
    the sheet's rows INCLUDING the header row (row 1), as values_get returns them.
    """
    want = want.strip()
    exact, loose = [], []
    for tab, rows in rows_by_tab.items():
        for i, r in enumerate(rows[1:], start=2):
            if not r:
                continue
            cell = str(r[0]).strip()
            if cell == want:
                exact.append((tab, i, r))
            elif cell.lower() == want.lower():
                loose.append((tab, i, r))
    return exact if exact else loose
