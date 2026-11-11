#!/usr/bin/env python3
"""
board_tabs.py — the Google Sheet's tab names. THE single source.

Dependency-free on purpose (like status_vocab.py): board_facts.py runs under the
VPS's /usr/bin/python3 for the coach crons, which has no openpyxl, so it cannot
import build_curated_gsheet / build_curated_xlsx. Before this file existed it kept
its own literal "Apply Now!A1:Z", which is how a rename would have silently blanked
the morning brief and the evening nudge.

2026-10-04 (Microsoft Summer 2027 accepted the day before): the single "Apply Now"
queue became three tabs, his call:
  Apply - Winter  -> Winter/Spring 2027 rows at companies over the bar (his priority)
  Apply - Summer  -> everything else over the bar, including rows with NO stated cycle
                     (an unlabelled "2027 intern" req is almost always summer)
  Below Bar       -> rows that pass every hard gate but whose company is below the bar:
                     "we kinda considered them and they wouldn't be bad but the aim is
                     just we're aiming for something better". NOT Reviewed — Reviewed
                     keeps hard-gate failures and his own Skip / Not a Fit.
The bar lives in target_bar.py.

2026-11-11, his call: a fourth queue tab.
  Apply - Fall    -> Fall 2027 (Sep-Dec) rows at a TOP-TIER company (target_bar FALL bar),
                     located in Toronto/Canada/remote, to take alongside capstone. A Fall
                     row below that bar, or clearly on-site outside Canada, goes to Below
                     Bar. Created on the first refresh after deploy (_ensure_tabs adds any
                     tab in TABS that is missing, then ensure_format formats it).
"""
TAB_WINTER = "Apply - Winter"
TAB_SUMMER = "Apply - Summer"
TAB_FALL = "Apply - Fall"
TAB_BELOW = "Below Bar"
TAB_APPS = "My Applications"
TAB_REVIEWED = "Reviewed"
TAB_META = "_meta"

# The pre-2026-10-04 queue tab. write_board RENAMES it in place to TAB_WINTER (keeping
# its sheetId, formatting, dropdowns and filter), and read_back_human still reads it
# first, at lowest precedence, whenever it exists — so an edit he typed on it before
# the migration refresh is never lost, whichever order deploy and refresh happen in.
LEGACY_QUEUE = "Apply Now"

# Every queue-shaped tab (same headers, "To Apply" default, Priority column).
QUEUE_TABS = (TAB_WINTER, TAB_SUMMER, TAB_FALL, TAB_BELOW)
# The tabs he applies from. Below Bar is deliberately NOT here: the nudges and
# list-live surface targets, not the "aiming higher" pile. (board.py list-live reads
# this, so it shows Apply - Fall too; board_facts, which feeds the nudges, reads Winter
# and Summer BY NAME and does not read Fall — unchanged on 2026-11-11, his call later.)
APPLY_TABS = (TAB_WINTER, TAB_SUMMER, TAB_FALL)

# Render order (also tab order on a fresh sheet; an added tab lands at the END of an
# existing sheet, since addSheet takes no index here).
TABS = (TAB_WINTER, TAB_SUMMER, TAB_FALL, TAB_BELOW, TAB_APPS, TAB_REVIEWED)
