"""The status vocabulary and its buckets. ONE definition, zero dependencies.

Split out of build_curated_xlsx.py on 2026-09-25. That module is the historical home of
STATUS_OPTS, but it imports openpyxl at module scope, and openpyxl is NOT installed for
the VPS's `/usr/bin/python3` — which is the interpreter the coach crons run under. So
`board_facts.py`, the module that answers "what has he applied to", could not import the
vocabulary and RETYPED it instead. That copy then rotted exactly as a copy does: it held
"oa", a status that ceased to exist on 2026-09-16 when it split into "OA - To Do" /
"OA - Done", and it never learned "Technical Interview" (added 09-12). The 7 PM nudge was
reading a vocabulary two revisions out of date.

Anything that needs the vocabulary imports it FROM HERE. build_curated_xlsx re-exports
these names so every existing consumer keeps working unchanged; STATUS_FILL stays there
because its values are openpyxl colour objects.

🔴 Keep this file free of third-party imports. Its whole purpose is to be importable by
the most dependency-poor interpreter on the box.
"""
from __future__ import annotations

# "OA" split into "OA - To Do" / "OA - Done" on 2026-09-16 (Sparsh: "make it more clear
# in the status which OA has been done and which one nah and like requires action").
# One value was covering two opposite states: on that date 8 rows read "OA" and 5 of them
# needed nothing — the 3 outstanding ones (DRW, Snowflake, Intact) were indistinguishable
# from the 5 already sat. "To Do" carries the To Apply orange; "Done" keeps the OA sky.
# "Final Round - Sat" added 2026-09-30, Sparsh's call. "Sat" is the past tense of "sit an
# interview", NOT Saturday — it is the "- Done" idea from OA - Done, kept in his wording.
# It exists because "Onsite" was covering two opposite states exactly as bare "OA" did:
# on 2026-09-30 the Microsoft Cloud & Distributed Backend row read "Onsite" AFTER he had
# already sat the final round, and his reaction was "why did you put onsite for microsoft,
# that makes no sense… the interview i did today was already the final round, there is no
# next step, we're just waiting to hear back." A status that reads as an upcoming event,
# on a row whose event is over, is the same defect one stage up.
# "Onsite" now means the final round is REACHED (booked, or happening).
# "Final Round - Sat" means it is BEHIND him and the decision is theirs — nothing owed.
# "Recruiter Outreach" added 2026-09-30, Sparsh's call, same night as "Final Round - Sat"
# and for a related reason -- but this one also fixed a REGRESSION. The Tesla Factory
# Firmware row (283395) was put on "Networking" that evening to mark that a recruiter had
# sourced him; his push-back was "idk if we should add a quick status for like recruiter
# reach out or something just so it's not sitting as just 'applied'". Checking it showed
# "Networking" RANKS 8, BELOW plain "Applied" at 7 -- and STATUS_RANK is the My
# Applications sort key in BOTH renderers. So labelling it Networking had buried the one
# live Tesla thread underneath 23 dormant Tesla applications: the exact opposite of why
# it was labelled.
# "Networking" means a warm lead with NO application (the Mercor row). "Recruiter
# Outreach" means an EXISTING application that a recruiter has picked up and is actively
# moving -- further along than cold Applied, short of any booked screen.
STATUS_OPTS = ["To Apply", "Applied", "Recruiter Outreach", "OA - To Do", "OA - Done",
               "Phone Screen", "Technical Interview", "Onsite", "Final Round - Sat",
               "Offer", "Rejected", "Rejected after OA", "Rejected after Interview",
               "Networking", "On Hold", "Skip", "Not a Fit", "Closed"]
# Every terminal-no status, for consumers that need "he was turned down" as one bucket.
REJECTED_STATUSES = tuple(s for s in STATUS_OPTS if s.startswith("Rejected"))
# Statuses that mean "I looked at this and I'm not applying" -> Reviewed sheet, not the
# active queue and NOT the applications sheet. Skip / Not a Fit = your judgment call;
# Closed = dead (missed deadline / role pulled) that you never applied to.
REVIEWED_STATUSES = {"skip", "not a fit", "closed"}
# Statuses that mean a real application is IN FLIGHT (he has acted on the row). curate's
# stale-check exempts these from the strike rule: an employer's board often drops a req
# the moment they stop accepting candidates, which is usually right after he applies.
PIPELINE_STATUSES = {"Applied", "Recruiter Outreach", "OA - To Do", "OA - Done",
                     "Phone Screen", "Technical Interview", "Onsite",
                     "Final Round - Sat", "Offer", "Networking", "On Hold"}
# The interview funnel proper, for the "In process (OA+)" summary tile.
IN_PROCESS_STATUSES = ("OA - To Do", "OA - Done", "Phone Screen",
                       "Technical Interview", "Onsite", "Final Round - Sat")
# Lower = further along. "Final Round - Sat" outranks "Onsite" because a round that is
# BEHIND him is further than one that is merely booked — the only thing above it is the
# offer itself.
# This is the My Applications SORT KEY in both renderers -- lower renders higher.
# 2026-09-30, Sparsh: "networking can be shown like near the top, under the OAs and
# stuff, so mercor and tesla can go there too, instead of being down near the rejected
# section." So BOTH relationship-driven states sit directly under the OA block and ABOVE
# plain Applied. Before this, Networking ranked below Applied and sat just above the
# rejections -- which buried a warm lead under 130+ cold applications.
# "Recruiter Outreach" leads "Networking": an application a recruiter is actively moving
# is further along than a lead with no application behind it.
# 🔴 UNKNOWN_RANK is the fallback both renderers use for a status not in this table, and
# it must track Applied. It was a bare literal 7 duplicated in two files; the moment
# Applied moved off 7 that silently made an unknown status outrank every real Applied
# row. Named here so the next insert cannot reintroduce it.
STATUS_RANK = {"Offer": 0, "Final Round - Sat": 1, "Onsite": 2,
               "Technical Interview": 3, "Phone Screen": 4,
               "OA - To Do": 5, "OA - Done": 6,
               "Recruiter Outreach": 7, "Networking": 8,
               "Applied": 9, "On Hold": 10,
               "Rejected after Interview": 11, "Rejected after OA": 12, "Rejected": 13,
               # 2026-10-05: an APPLIED row can now carry a Reviewed status and stay on
               # My Applications (classify_row). Settled-by-him sorts at the very bottom,
               # under the rejections; on Reviewed these ranks are never consulted.
               "Closed": 14, "Skip": 15, "Not a Fit": 16}
UNKNOWN_RANK = STATUS_RANK["Applied"]
