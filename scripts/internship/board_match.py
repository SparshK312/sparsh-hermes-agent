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


# ── fragment matching (2026-09-21) ───────────────────────────────────────────
# A Telegram turn looped for 15 minutes on `board.py show "<fragment>"`: the pasted
# titles never matched the rows' exact strings ("BS MS" vs "BS/MS", "SWE" vs "Software
# Engineer"), every miss came back as exit 1 + "no row matches … Try: board.py
# list-live", and the agent kept rewording the fragment — 41 Sonnet calls before the
# 40-tool-turn guardrail ended it. The tool handed back an error where an answer was
# available: the rows that are NEARLY the fragment, with their status. This function
# is that answer. Refusal stays the rule for WRITES (board._find); reads get the list.

# Words that appear on most rows and therefore say nothing about WHICH row is meant.
# A fragment whose only matches are these ("Software Engineer Intern Summer 2027")
# names no company and no team, and must not surface random rows as "nearest".
GENERIC_TOKENS = frozenset("""
    intern interns internship internships co-op coop software engineer engineers
    engineering developer development sde swe eng summer winter fall spring
    2026 2027 2028 bs ms phd bachelor bachelors master masters undergraduate
    graduate new grad the a an of and for in at to
""".split())


IDENTITY_COLUMNS = ("Company", "Role", "Location", "Cycle", "_id")


def _tokens(text: str) -> list:
    """Lower-cased words. Splits on the punctuation rows format differently from how
    a human retypes them: `BS/MS` -> bs ms, `Intern - Web` -> intern web, `Intern,` ->
    intern. Tokens shorter than 2 chars are dropped (they match everything)."""
    import re
    return [t for t in re.split(r"[\s/,\-–—()\[\]\"'.:;|]+", text.lower()) if len(t) >= 2]


def find_rows(needle: str, rows_by_tab: dict, headers_by_tab: dict, limit: int = 12) -> tuple:
    """-> (mode, hits). hits = [(tab, row_number, headers, row), ...].

    mode is how the rows were found, most to least exact:
      'exact'   the whole needle is a substring of the row (the historical rule)
      'tokens'  every word of the needle is somewhere in the row, any order/punctuation
                ("Waymo 2027 Summer Intern BS MS Software Engineer" -> the BS/MS row)
      'nearest' the row shares at least one NON-generic word with the needle
                ("Figma SWE Intern" -> every Figma row), ranked by overlap, capped
      'none'    no row shares a distinctive word — the company/team is not on the board

    `rows_by_tab` maps tab -> rows INCLUDING the header row, as values_get returns
    them; `headers_by_tab` maps tab -> that tab's header list. Never raises on a miss:
    a miss is an answer ("not on the board"), not an error to retry.
    """
    n = needle.strip().lower()
    toks = _tokens(needle)
    distinct = [t for t in toks if t not in GENERIC_TOKENS]
    exact, alltok, near = [], [], []
    for tab, rows in rows_by_tab.items():
        h = headers_by_tab[tab]
        for i, r in enumerate(rows[1:], start=2):
            if not r:
                continue
            blob = " ".join(str(c) for c in r).lower()
            # Word matching reads only the columns that IDENTIFY a posting. Notes and
            # Why are free text that name other companies ("same JD as Figma's"), and
            # matching them made "Figma SWE Intern" return an Amazon row. The whole-
            # needle substring keeps the historical any-column behaviour so a URL or
            # note fragment still addresses a row.
            ident = " ".join(str(r[h.index(k)]) for k in IDENTITY_COLUMNS
                             if k in h and h.index(k) < len(r)).lower()
            rtoks = set(_tokens(ident))
            hit = (tab, i, h, r)
            if n and n in blob:
                exact.append(hit)
                continue
            if toks and all(t in rtoks for t in toks):
                alltok.append(hit)
                continue
            d_hit = sum(1 for t in distinct if t in rtoks)
            if d_hit:
                near.append((d_hit, sum(1 for t in toks if t in rtoks), hit))
    if exact:
        return "exact", exact
    if alltok:
        return "tokens", alltok
    if near:
        near.sort(key=lambda x: (-x[0], -x[1], x[2][0], x[2][1]))
        return "nearest", [x[2] for x in near[:limit]]
    return "none", []
