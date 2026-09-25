#!/usr/bin/env python3
"""Invariants for the nudge scripts. One test per false claim that actually shipped.

WHY THIS FILE EXISTS. 2026-09-25, Sparsh: "i constantly think it's not actually reading
the right files that are actually being updated on a daily basis. it'll say no
applications or no prep done or whatever even though the vault actually does have
progress on that stuff." He was right, in two places at once, and both were a nudge
reading a file that had stopped being the source of truth:

  • prep-nudge parsed a re-solve table out of `00 - Dashboard/Interview Prep.md`. That
    section had been REPLACED on 2026-09-15 by a warning that opens, in bold, "DO NOT
    MAINTAIN A TABLE HERE. IT ROTS." The parse matched nothing, so `redo_due` was the
    literal string "none" on every run since — while eleven problems sat overdue in the
    scorecard. The prompt's entire spaced-repetition branch had never once fired.
  • prep-nudge took "when did he last do a rep" from that file's hand-curated Session
    log, which lags. Measured that day: the table's newest row was Sep 22 while Log.md
    — appended by every session as it happens — carried prep entries on the 24th and
    four on the 25th. The nudge was about to tell him he was three days dark.

These run the REAL script against fixture vaults, so they test behaviour, not wording.
Pure: no network, no live vault. Exit 0 = all pass.

Run:  python3 test_nudge_invariants.py
"""
import json
import re
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ❌ {label}: got {got!r}, want {want!r}")


TRACKER = """---
type: prep-tracker
---
# Interview Prep

### 1 · DS&A survival core
- [x] Two Sum
- [ ] Validate BST

### 🔁 Redo list — 🔴 DO NOT MAINTAIN A TABLE HERE. IT ROTS.

The live queue is the scorecard app. This section deliberately holds no table.

### 📝 Session log

| Date | What | | Time | Notes |
|---|---|---|---|---|
| {sess} | some reps | — | — | x |

## 🗄️ [ARCHIVED — old plan]
| 2020-01-01 | archived row that must never be read | — | — | x |
"""

SNAPSHOT = """---
type: reference
generated: {gen}T09:00:00
overdue_count: {n}
---
# Prep Queue Snapshot (generated)

| id | problem | pattern | rating | rated | re-solve due | overdue |
|---|---|---|---|---|---|---|
| a2 | Contains Duplicate | Arrays / Hash | AMBER | 2026-09-12 | 2026-09-16 | YES |
| a5 | Group Anagrams | Arrays / Hash | RED | 2026-09-14 | 2026-09-16 | YES |
| a7 | Container With Most Water | Two Pointers | GREEN | 2026-09-12 | 2026-09-26 |  |
"""


def build(sess_days_ago: int, log_days_ago=None, snap_days_ago=None,
          logmd_age_days: int = 0) -> Path:
    """A throwaway vault.

    `log_days_ago=None` -> Log.md holds no PREP entry.
    `logmd_age_days`    -> how old Log.md's newest entry of ANY kind is. A real Log.md
                           gets entries daily about everything, so 0 is the normal case;
                           raise it to simulate the file not arriving (sync broken).
    """
    v = Path(tempfile.mkdtemp(prefix="nudge-fixture-"))
    (v / "00 - Dashboard").mkdir(parents=True)
    (v / "09 - Systems" / "Hermes").mkdir(parents=True)
    sess = (date.today() - timedelta(days=sess_days_ago)).isoformat()
    (v / "00 - Dashboard" / "Interview Prep.md").write_text(TRACKER.format(sess=sess))
    marker = (date.today() - timedelta(days=logmd_age_days)).isoformat()
    log = f"## [{marker}] update | Action Items — unrelated day-to-day entry\n"
    if log_days_ago is not None:
        d = (date.today() - timedelta(days=log_days_ago)).isoformat()
        log += f"## [{d}] update | Interview Prep / Microsoft — did a rep\n"
    (v / "Log.md").write_text(log)
    if snap_days_ago is not None:
        gen = (date.today() - timedelta(days=snap_days_ago)).isoformat()
        (v / "09 - Systems" / "Hermes" / "prep-queue-snapshot.md").write_text(
            SNAPSHOT.format(gen=gen, n=2))
    return v


def state(vault: Path) -> dict:
    out = subprocess.run(["bash", str(HERE / "prep_nudge.sh")],
                         capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HERMES_VAULT": str(vault)})
    d = {}
    for ln in out.stdout.splitlines():
        if ":" in ln:
            k, _, val = ln.partition(":")
            d[k.strip()] = val.strip()
    d["_raw"] = out.stdout
    return d


def test_prep_nudge_reads_the_files_that_are_actually_updated():
    print("N1. prep-nudge: Log.md counts as activity; the Session log alone does not")
    # He has not ticked the Session log in 5 days but worked on prep TODAY (Log.md).
    s = state(build(sess_days_ago=5, log_days_ago=0))
    check("activity is seen from Log.md", s.get("days_since_prep_activity", "").split()[0], "0")
    check("the rep counter still reports the real rep gap", s.get("days_since_rep"), "5")
    check("🔴 it does NOT escalate on a day he did prep", s.get("escalate"), "no")
    check("both sources are shown so a disagreement is visible",
          "last_session_log_row" in s and "last_prep_entry_in_Log_md" in s, True)

    # Genuinely dark on both channels -> it SHOULD escalate. A fix that just silences
    # the nudge forever would pass the test above and be useless.
    s = state(build(sess_days_ago=9, log_days_ago=9))
    check("still escalates when BOTH channels are quiet", s.get("escalate"), "yes")

    # The streak must come from reps only. Counting Log.md prep entries as reps would
    # have minted an 18-day streak across a week the tracker records as "DARK".
    s = state(build(sess_days_ago=6, log_days_ago=0))
    check("a planning entry today does not create a rep streak",
          s.get("current_streak_days"), "0")

    # The archived section below the "## 🗄️" marker must never be read.
    check("the archived plan's dates are ignored", "2020-01-01" in s["_raw"], False)


def test_prep_nudge_validates_its_own_input():
    print("N1b. prep-nudge: a stale Log.md is UNKNOWN activity, not zero activity")
    # Obsidian Sync has been erroring "Vault limit exceeded" since 2026-09-14; on 09-25
    # the VPS copy of Log.md was 40 entries behind the Mac's. If the file stops arriving,
    # "no prep entry" means nothing — and must never become "you have done nothing".
    v = build(sess_days_ago=9, log_days_ago=40, logmd_age_days=40)  # nothing arriving
    s = state(v)
    check("staleness of Log.md is detected", "STALE" in s.get("log_md_newest_entry", ""), True)
    check("🔴 it refuses to escalate on an input it cannot see", s.get("escalate"), "no")
    check("it names the likely cause", "Obsidian Sync" in s.get("log_md_newest_entry", ""), True)
    # and a FRESH Log.md with both channels quiet must still escalate — the guard must
    # not become a blanket excuse that silences the nudge forever.
    s = state(build(sess_days_ago=9, log_days_ago=9))
    check("a fresh Log.md still allows escalation", s.get("escalate"), "yes")


def test_prep_nudge_never_calls_an_unread_queue_empty():
    print("N2. prep-nudge: an unread re-solve queue is UNKNOWN, never 'none'")
    # (a) no snapshot at all
    s = state(build(sess_days_ago=1, log_days_ago=0))
    check("redo_due says UNKNOWN when there is no snapshot",
          s.get("redo_due", "").startswith("UNKNOWN"), True)
    check("🔴 it never reports the queue as empty when it could not look",
          re.search(r"^redo_due:\s*none\s*$", s["_raw"], re.M) is None, True)
    check("it names the remedy", "catchup.py" in s.get("redo_due", ""), True)

    # (b) a STALE snapshot is also unknown — an old answer is not a current one
    s = state(build(sess_days_ago=1, log_days_ago=0, snap_days_ago=30))
    check("a 30-day-old snapshot is not treated as current",
          s.get("redo_due", "").startswith("UNKNOWN"), True)
    check("staleness is reported with the date", "stale" in s.get("redo_source", ""), True)

    # (c) a FRESH snapshot is used, and only the overdue rows are named
    s = state(build(sess_days_ago=1, log_days_ago=0, snap_days_ago=0))
    check("a fresh snapshot is used", s.get("redo_source"), "fresh")
    check("overdue problems are named", s.get("redo_due"),
          "Contains Duplicate, Group Anagrams")
    check("a NOT-overdue row is not named",
          "Container With Most Water" in s.get("redo_due", ""), False)


def test_the_prompt_cannot_assert_an_unknown_queue_is_clear():
    print("N3. the cron prompt forbids turning UNKNOWN into 'nothing due'")
    cfg = json.loads((HERE.parent.parent / "config" / "cron_additions.json").read_text())
    job = next(j for j in cfg["jobs_to_append"] if j["name"] == "prep-nudge")
    p = job["prompt"]
    check("the prompt distinguishes reps from activity",
          "days_since_prep_activity" in p and "days_since_rep" in p, True)
    check("the prompt forbids 'you did nothing' on an active day",
          "NEVER tell him he has done nothing" in p, True)
    check("the prompt handles redo_source", "redo_source" in p, True)
    check("the prompt forbids claiming a clear queue when unknown",
          "NEVER say the queue is clear" in p, True)


for fn in (test_prep_nudge_reads_the_files_that_are_actually_updated,
           test_prep_nudge_validates_its_own_input,
           test_prep_nudge_never_calls_an_unread_queue_empty,
           test_the_prompt_cannot_assert_an_unknown_queue_is_clear):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        print(f"  ❌ {fn.__name__} RAISED {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
