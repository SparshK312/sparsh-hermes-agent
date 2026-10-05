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
          logmd_age_days: int = 0, hermes_today: bool = False) -> Path:
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
    # 2026-10-05: the log is one file per day under Log/ (Log.md is a MOVED stub).
    def _entry(d, text):
        p = v / "Log" / d[:4] / d[:7] / f"{d}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(f"## [{d}] {text}\n")
    (v / "Log.md").write_text("## [9999-12-31] schema | Log.md — MOVED\n")
    marker = (date.today() - timedelta(days=logmd_age_days)).isoformat()
    _entry(marker, "update | Action Items — unrelated day-to-day entry")
    if log_days_ago is not None:
        d = (date.today() - timedelta(days=log_days_ago)).isoformat()
        _entry(d, "update | Interview Prep / Microsoft — did a rep")
    if hermes_today:
        _entry(date.today().isoformat(), "ingest | hermes:daily-note-prefill — seeded")
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




# ── HERMES'S OWN ENTRIES MUST NOT MAKE A STALE VAULT LOOK FRESH ───────────────
# 2026-10-03/04: sync had been jammed for days, but the nudge printed "(fresh)" because
# the newest log entry was Hermes's own daily-note-prefill line, written on the VPS.
def test_hermes_entries_do_not_count_as_freshness():
    print("N5. prep-nudge: a hermes: entry today does not make a 5-day-stale log fresh")
    s = state(build(sess_days_ago=1, log_days_ago=None, snap_days_ago=0,
                    logmd_age_days=5, hermes_today=True))
    line = s.get("log_md_newest_entry", "")
    check("the Mac side is reported STALE despite a hermes: entry today", "STALE" in line, True)
    s2 = state(build(sess_days_ago=1, log_days_ago=None, snap_days_ago=0, logmd_age_days=0))
    check("a genuinely fresh Mac entry still reads fresh",
          "(fresh)" in s2.get("log_md_newest_entry", ""), True)


# ── THE SYNC WATCHDOG ALERTS ON STATE CHANGE AND CATCHES WHAT ACTUALLY JAMMED ──
# 2026-10-05. Obsidian Sync was jammed most of Sep 14 → Oct 5 and nothing noticed; the
# client never logs "limit exceeded" and kept printing "Fully synced". The watchdog reads
# pending uploads + the error rate + the Mac heartbeat instead.
def test_sync_watchdog():
    print("W1. sync_watchdog: pending uploads + error rate detected; alerts only on change")
    import sqlite3, os as _os
    sys.path.insert(0, str(HERE.parent / "monitor"))
    import sync_watchdog as W
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    now = _dt(2026, 10, 5, 20, 0, tzinfo=_tz.utc)
    d = Path(tempfile.mkdtemp())
    db = d / "state.db"
    c = sqlite3.connect(db)
    c.execute("create table local_files (path text primary key, data text not null)")
    old_ms = int((now - _td(hours=5)).timestamp() * 1000)
    new_ms = int((now - _td(minutes=5)).timestamp() * 1000)
    rows = [("Log/2026/2026-10/2026-10-05.md", {"hash": "a", "synchash": "b", "mtime": old_ms}),
            ("fresh.md", {"hash": "a", "synchash": "b", "mtime": new_ms}),
            ("synced.md", {"hash": "a", "synchash": "a", "mtime": old_ms}),
            ("Folder", {"folder": True, "hash": "", "synchash": ""})]
    for p, dd in rows:
        c.execute("insert into local_files values (?,?)", (p, json.dumps(dd)))
    c.commit(); c.close()
    pend = dict(W.pending_uploads(db, now))
    check("a 5-hour-old unsynced change is pending", round(pend.get("Log/2026/2026-10/2026-10-05.md", 0)), 300)
    check("a synced file is not pending", "synced.md" in pend, False)
    log = d / "sync.log"
    lines = [f"[{(now - _td(minutes=m)).strftime('%Y-%m-%dT%H:%M:%S')}.000Z] Sync error: {{}}" for m in range(0, 55, 5)]
    lines += [f"[{(now - _td(hours=3)).strftime('%Y-%m-%dT%H:%M:%S')}.000Z] Sync error: {{}}"] * 30
    lines += [f"[{now.strftime('%Y-%m-%dT%H:%M:%S')}.000Z] Fully synced"]
    log.write_text("\n".join(lines) + "\n")
    check("errors counted only within the last hour", W.errors_last_hour(log, now), 11)
    # alert state machine
    check("ok -> bad alerts", W.decide({}, ["x"], now), "alert")
    check("still bad within 12h is silent", W.decide({"bad": True, "last_alert": (now - _td(hours=2)).isoformat()}, ["x"], now), None)
    check("still bad after 12h re-alerts", W.decide({"bad": True, "last_alert": (now - _td(hours=13)).isoformat()}, ["x"], now), "realert")
    check("bad -> ok says recovered", W.decide({"bad": True}, [], now), "recovered")
    check("ok -> ok is silent", W.decide({"bad": False}, [], now), None)
    # echo is write-if-changed
    echo = d / "Sync Echo.md"
    check("first sight of a nonce writes the echo", W.write_echo(echo, "abc123", now), True)
    check("the same nonce never rewrites it (no Sync version per run)", W.write_echo(echo, "abc123", now + _td(minutes=30)), False)
    hb = d / "hb.md"
    hb.write_text("---\ntype: reference\n---\nts: 2026-10-05T18:00:00+00:00\nnonce: abc123\n")
    when, nonce = W.read_heartbeat(hb)
    check("heartbeat parsed", (when.isoformat() if when else None, nonce), ("2026-10-05T18:00:00+00:00", "abc123"))
    # log rotation keeps the tail
    big = d / "big.log"
    big.write_bytes(b"x" * (W.LOG_MAX_BYTES + 10) + b"\nTAIL")
    check("rotates past the cap", W.rotate(big), True)
    check("keeps the most recent bytes", big.read_bytes().endswith(b"TAIL") and big.stat().st_size == W.LOG_KEEP_BYTES, True)

# ── THE MORNING EMAIL TRIAGE MUST SEE EVERY APPLICATION ───────────────────────
# 2026-10-04. email_triage.live_applications() read "My Applications!A1:K60" while the
# tab held 189 rows, so 130 applications (Amazon, Google, Microsoft, Palantir…) were
# invisible and an email from any of them could not be matched to its application.
# Behavioural: drive the REAL function with a fake Sheet of 700 rows.
def test_email_triage_reads_every_application():
    print("E1. email_triage reads the whole My Applications tab; only the PROMPT is capped, loudly")
    sys.path.insert(0, str(HERE.parent / "email"))
    import email_triage as T
    hdr = ["_id", "Status", "Due", "Company", "Role", "Lane", "Location", "Cycle",
           "Apply", "Applied", "Ago", "Source / Referral", "Notes"]
    def row(i, status, applied):
        r = [""] * len(hdr)
        r[0], r[1], r[3], r[4], r[9] = f"id{i}", status, f"Co{i}", "SWE Intern", applied
        return r
    asked, logs = [], []
    real_gapi, real_log = T._gapi, T.log
    def fake(rows):
        def _g(*args, **kw):
            asked.append(args[-1])
            return json.dumps([hdr] + rows)
        return _g
    try:
        T.log = lambda m: logs.append(m)
        # 189 rows, the real size on 2026-10-04: every one must come back.
        T._gapi = fake([row(i, "Applied", f"2026-09-{1 + i % 28:02d}") for i in range(189)])
        got = T.live_applications()
        check("all 189 applications are read (was 59)", len(got), 189)
        check("the range has no row cap", bool(re.search(r"\d+$", asked[-1])), False)
        check("no AT-CAP warning below the ceiling", any("AT CAP" in m for m in logs), False)
        # 700 rows: the prompt is trimmed, every in-process row survives, and it is LOUD.
        logs.clear()
        rows = [row(i, "Applied", f"2025-{1 + i % 12:02d}-01") for i in range(690)]
        rows += [row(1000 + i, "OA - To Do", "2025-01-01") for i in range(10)]
        T._gapi = fake(rows)
        got = T.live_applications()
        check("the prompt is held to MAX_APPS", len(got), T.MAX_APPS)
        check("every in-process row survives the trim, even the oldest",
              sum(1 for a in got if a["status"] == "OA - To Do"), 10)
        check("hitting the ceiling is announced", any("AT CAP" in m for m in logs), True)
    finally:
        T._gapi, T.log = real_gapi, real_log
    # The fix only matters if it reaches the VPS: until 2026-10-04 deploy.sh never copied
    # scripts/email/, so the VPS ran a hand-placed Sep-4 copy and a deployed fix was inert.
    dep = (HERE.parent / "deploy.sh").read_text()
    check("deploy.sh copies email_triage.py to where the cron runs it",
          bool(re.search(r"cp scripts/email/email_triage\.py[^\n]*~/\.hermes/scripts/email/", dep)), True)
    check("deploy.sh copies the email_triage.sh cron wrapper",
          "cp scripts/cron/email_triage.sh ~/.hermes/scripts/email_triage.sh" in dep, True)
    check("the wrapper runs the copy deploy.sh writes",
          "$HOME/.hermes/scripts/email/email_triage.py" in (HERE / "email_triage.sh").read_text(), True)

for fn in (test_prep_nudge_reads_the_files_that_are_actually_updated,
           test_prep_nudge_validates_its_own_input,
           test_prep_nudge_never_calls_an_unread_queue_empty,
           test_the_prompt_cannot_assert_an_unknown_queue_is_clear,
           test_email_triage_reads_every_application,
           test_hermes_entries_do_not_count_as_freshness,
           test_sync_watchdog):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        print(f"  ❌ {fn.__name__} RAISED {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
