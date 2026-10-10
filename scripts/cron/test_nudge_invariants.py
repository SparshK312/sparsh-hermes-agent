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
    # 2026-10-08: the heartbeat only runs while the Mac is awake; a closed-laptop weekend
    # (Fri evening → Mon morning ≈ 62 h) must not page him
    check("heartbeat alarm tolerates a closed-Mac weekend", W.HEARTBEAT_MAX_H >= 66, True)
    # (audit 2026-10-07) a lost alert is NOT recorded as sent, so the next run retries it
    st = W.next_state({}, "alert", ["x"], now, sent=True)
    check("a sent alert is recorded", (st.get("bad"), bool(st.get("last_alert"))), (True, True))
    st = W.next_state({}, "alert", ["x"], now, sent=False)
    check("a FAILED alert keeps the old state", (st.get("bad"), st.get("last_alert")), (None, None))
    check("…so the next run alerts again", W.decide(st, ["x"], now + _td(minutes=30)), "alert")
    st = W.next_state({"bad": True}, "recovered", [], now, sent=False)
    check("a FAILED recovery message is retried next run", W.decide(st, [], now), "recovered")
    # a crash alerts instead of dying silently
    calls = []
    real_run, real_send = W.run, W.send
    try:
        W.run = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        W.send = lambda subj, body: calls.append(subj) or True
        rc = W.main()
    finally:
        W.run, W.send = real_run, real_send
    check("a crash exits non-zero", rc, 2)
    check("a crash sends an alert", len(calls) == 1 and "crashed" in calls[0], True)
    # the cron wrappers report the real status, and a failing snapshot alerts once
    home = Path(tempfile.mkdtemp())
    (home / ".hermes/scripts/monitor").mkdir(parents=True)
    (home / ".hermes/scripts/monitor/sync_watchdog.py").write_text("import sys; sys.exit(3)\n")
    (home / ".local/bin").mkdir(parents=True)
    sent_log = home / "sent.txt"
    hermes_stub = home / ".local/bin/hermes"
    hermes_stub.write_text(f"#!/bin/sh\necho \"$6\" >> '{sent_log}'\n")
    hermes_stub.chmod(0o755)
    env = dict(_os.environ, HOME=str(home))
    rc = subprocess.run(["bash", str(HERE / "sync_watchdog.sh")], env=env).returncode
    check("sync_watchdog.sh exits with the watchdog's status", rc, 3)
    if not Path("/home/hermes/vault").exists():   # on the VPS the real vault exists; test off-box
        snap = HERE / "vault_git_snapshot.sh"
        rcs = [subprocess.run(["bash", str(snap)], env=env, capture_output=True).returncode for _ in range(2)]
        msgs = sent_log.read_text().splitlines() if sent_log.exists() else []
        check("a failing snapshot exits non-zero", rcs, [1, 1])
        check("…and alerts ONCE, not every hour", sum("failing" in m for m in msgs), 1)

# ── THE "RECENT EVENTS" RECIPE HERMES IS GIVEN MUST RETURN THE NEWEST ENTRIES ──
# Audit 2026-10-07: `.hermes.md` said `grep -rh '^## \[' Log/ | sort | tail -30`. `sort`
# orders the WHOLE header, so within a day entries sorted by action name and `tail`
# returned only `update`s — a live Hermes turn missed all 5 of that day's decisions.
# Behavioural: run the exact command from .hermes.md against a fixture log.
def test_hermes_recent_events_recipe():
    print("L1. .hermes.md 'Recent events' recipe returns the newest entries of every action")
    md = (HERE.parent.parent / "vault-context" / ".hermes.md").read_text()
    m = re.search(r"Recent events: `([^`]+)`", md)
    check("recipe present in .hermes.md", bool(m), True)
    if not m:
        return
    v = Path(tempfile.mkdtemp())
    day = v / "Log/2026/2026-10"; day.mkdir(parents=True)
    (day / "2026-10-06.md").write_text("## [2026-10-06] update | old — older day\n")
    (day / "2026-10-07.md").write_text(
        "## [2026-10-07] update | a — first\n\n## [2026-10-07] update | b — second\n\n"
        "## [2026-10-07] decision | c — NEWEST, a decision\n")
    cmd = m.group(1).replace("/home/hermes/vault/", "").replace("tail -30", "tail -1")
    out = subprocess.run(["bash", "-c", cmd], cwd=v, capture_output=True, text=True).stdout.strip()
    check("the newest entry is returned even when it is a decision", out.endswith("NEWEST, a decision"), True)

# ── THE PREFILL'S TASK SOURCE MUST BE TODAY'S OPEN ROWS, NOT OLD SECTIONS ─────────
# 2026-10-08: today_actions.py printed ~30 KB a day — "🗄️ Thu Sep 17 (was TODAY)", "today's
# wave is staged", and "Thu Oct 1" (weekday+month matched every Thursday in October) — and
# the prefill turned them into tasks. The live list is the THIS WEEK table. Synthetic
# fixture with the real file's SHAPE (the repo is public: no real entries here).
_TA_FIXTURE = """# Action Items
## 🥇 THE STACK — re-cut Mon Sep 14 · 🗄️ OVER. Rows moved to THIS WEEK below.
### 🗄️ Thu Sep 17 *(was "TODAY")* — all done
- old task A
### 📈 APPLICATIONS — today's wave is staged
- old task B
### 📄 RÉSUMÉ REBUILD — his call, Thu Oct 1 2026
- old task C
## 🔴 THE FORWARD CALENDAR
### 🔥 THIS WEEK — rebuilt Wed Sep 30
> 🗄️ cleared items quoted here
| When | What | Note |
|---|---|---|
| 🔴 **DAILY Wed Oct 7 → Thu Oct 15** | 🟢 **ACME: interview prep ladder** | n |
| ✅ **SENT Wed Oct 7** | 📧 **ACME: availability submitted** | n |
| 🗄️ *(superseded)* | 🎯 **OLDCO: dead thread** | n |
| ✅ ~~**Mon Oct 5**~~ | ✅ **ROUND: sat** | n |
| ✅ **TEST SUBMITTED Thu Oct 8** · ⬜ **VOICE ROUND still to do: closes Sat Oct 10** · 🗄️ was: TODAY Wed Oct 7 | 🧪 **WIDGETCO OAs** | n |
| 🔴 **TODAY Tue Oct 6** | 🟢 **ACME: old today row** | n |
| 🔴 **TODAY Thu Oct 8** | 🟢 **ACME: today row** | n |
| ✅ **HELD Tue Oct 6** | 💼 **EXT: meeting held** | ⬜ stale open-decision note in the NOTE column |
| 🟡 **After passing** | 🟢 **ACME: questions for later** | n |
| 🔴 **By Sat Oct 10** | 📝 **APPLY: deadline soon** | n |
| 🔴 **By Mon Oct 19** | 📝 **APPLY: deadline later** | n |
| 🟡 **Next** | 📄 **{long} ⬜ LATE OPEN STEP past the cut** | n |
### 📅 Thu Oct 8 — dated section
- dated task D
#### sub-heading inside the dated section
- dated task E
### 📅 Fri Oct 9 — tomorrow
- not today F
"""

def test_today_actions_reads_open_rows_only():
    print("T1. today_actions: open THIS WEEK rows + today's dated sections; no stale sections; capped")
    sys.path.insert(0, str(HERE.parent / "vault"))
    import today_actions as TA
    fx = _TA_FIXTURE.replace("{long}", "x" * 400)
    out = TA.build(fx, date(2026, 10, 8))
    for gone in ("old task A", "old task B", "old task C", "availability submitted",
                 "dead thread", "ROUND: sat", "not today F", "dated task D", "meeting held"):
        check(f"dropped: {gone}", gone in out, False)
    for kept in ("interview prep ladder", "VOICE ROUND still to do", "today row", "LATE OPEN STEP"):
        check(f"kept: {kept}", kept in out, True)
    tag = {k: next((l[2:9] for l in out.splitlines() if k in l), None) for k in
           ("interview prep ladder", "today row", "questions for later", "deadline soon",
            "deadline later", "VOICE ROUND")}
    check("today's work is [TODAY]; later work is [WEEK]", tag,
          {"interview prep ladder": "[TODAY]", "today row": "[TODAY]", "questions for later": "[WEEK] ",
           "deadline soon": "[TODAY]", "deadline later": "[WEEK] ", "VOICE ROUND": "[TODAY]"})
    stale = [l for l in out.splitlines() if "STALE" in l]
    check("only the past TODAY row is flagged stale", [("old today row" in l) for l in stale], [True])
    check("small", len(out) < 3000, True)
    nofmt = TA.build("# Action Items\n### Some other heading\n- x\n", date(2026, 10, 8))
    check("a missing THIS WEEK table says UNKNOWN, never 'nothing due'",
          ("UNKNOWN" in nofmt, "nothing due" in nofmt), (True, False))
    big = _TA_FIXTURE.replace("| 🔴 **TODAY Thu Oct 8**",
                              "".join(f"| 🔴 **Open {i}** | {'x' * 200} | n |\n" for i in range(40)) + "| 🔴 **TODAY Thu Oct 8**")
    capped = TA.build(big, date(2026, 10, 8))
    check("the size cap announces itself", (len(capped) <= TA.TOTAL_MAX + 200, "TRUNCATED" in capped), (True, True))

# ── THE MORNING EMAIL TRIAGE MUST SEE EVERY APPLICATION ───────────────────────
# 2026-10-04. email_triage.live_applications() read "My Applications!A1:K60" while the
# tab held 189 rows, so 130 applications (Amazon, Google, Microsoft, Palantir…) were
# invisible and an email from any of them could not be matched to its application.
# Behavioural: drive the REAL function with a fake Sheet of 700 rows.
# ── THE TRIAGE MUST NOT TURN A FINISHED STEP INTO A TASK ─────────────────────
# 2026-10-08: "submit your availability" became a task the morning after it was submitted,
# and a bulk note on a Closed req became "check the Action Center". Synthetic fixtures only
# (the repo is public). The cases that must NOT be silenced matter as much as the drops.
def test_email_triage_done_checks():
    print("E2. email_triage: empty searches, fail-soft sent mail, thread + board done-checks, prompt evidence")
    sys.path.insert(0, str(HERE.parent / "email"))
    import email_triage as T
    real_gapi, real_log = T._gapi, T.log
    T.log = lambda *a, **k: None
    try:
        check("'No messages found.' is an empty result, not a crash", T._parse_search("No messages found.\n"), [])
        T._gapi = lambda *a, **k: "No messages found."
        check("an empty sent window is ok", T.fetch_sent(), ([], "ok"))
        def boom(*a, **k): raise RuntimeError("token expired")
        T._gapi = boom
        sent, st = T.fetch_sent()
        check("a failing sent fetch degrades, loudly labelled", (sent, st.startswith("unavailable")), ([], True))
    finally:
        T._gapi, T.log = real_gapi, real_log
    T.log = lambda *a, **k: None
    try:
        cands = [
            {"id": "m1", "date": "Wed, 07 Oct 2026 10:00:00 -0400", "thread": "t1"},   # he replied after
            {"id": "m2", "date": "Wed, 07 Oct 2026 10:00:00 -0400", "thread": "t2"},   # his mail was BEFORE
            {"id": "m3", "date": "Wed, 07 Oct 2026 10:00:00 -0400", "thread": "t3"},   # Closed row, nothing new
            {"id": "m4", "date": "Wed, 07 Oct 2026 10:00:00 -0400", "thread": "t4"},   # Closed row, but Offer row at same co
            {"id": "m5", "date": "Wed, 07 Oct 2026 10:00:00 -0400", "thread": "t5"},   # Closed row, recruiter re-engages
            {"id": "m6", "date": "Wed, 07 Oct 2026 10:00:00 -0400", "thread": "t6"},   # ambiguous match (2 rows)
            {"id": "m7", "date": "Wed, 07 Oct 2026 10:00:00 -0400", "thread": "t7"},   # replied, but the step is a FORM
            {"id": "m8", "date": "Wed, 07 Oct 2026 10:00:00 -0400", "thread": "t8"},   # Rejected row, recruiter re-engages
        ]
        sent = [{"thread": "t1", "date": "Wed, 07 Oct 2026 12:00:00 -0400"},
                {"thread": "t7", "date": "Wed, 07 Oct 2026 12:00:00 -0400"},
                {"thread": "t2", "date": "Tue, 06 Oct 2026 09:00:00 -0400"}]
        apps = [{"company": "Acme", "role": "SWE Intern", "status": "Closed"},
                {"company": "Widgetco", "role": "Data Intern", "status": "Closed"},
                {"company": "Widgetco", "role": "SWE Intern", "status": "Offer"},
                {"company": "Gizmo", "role": "SWE Intern", "status": "Closed"},
                {"company": "Dup", "role": "SWE Intern", "status": "Closed"},
                {"company": "Dup", "role": "SWE Intern", "status": "Rejected"},
                {"company": "Kappa", "role": "SWE Intern", "status": "Rejected"}]
        items = [
            {"id": "m1", "category": "needs-reply", "action": "reply to the recruiter"},
            {"id": "m2", "category": "needs-reply", "action": "reply to the recruiter"},
            {"id": "m3", "category": "application-update", "matched_application": "Acme — SWE Intern",
             "status_change": "", "action": "check the portal"},
            {"id": "m4", "category": "application-update", "matched_application": "Widgetco — Data Intern",
             "status_change": "", "action": "complete onboarding form"},
            {"id": "m5", "category": "application-update", "matched_application": "Gizmo — SWE Intern",
             "status_change": "Interview", "action": "book the interview"},
            {"id": "m6", "category": "application-update", "matched_application": "Dup — SWE Intern",
             "status_change": "", "action": "check the portal"},
            {"id": "m7", "category": "needs-reply", "action": "complete the onboarding form"},
            {"id": "m8", "category": "application-update", "matched_application": "Kappa — SWE Intern",
             "status_change": "", "action": "complete the new assessment by Friday"},
        ]
        n = T.reconcile(items, cands, sent, apps)
        got = {i["id"]: bool(i.get("action")) for i in items}
        check("replied in the same thread AFTER the email -> action cleared", got["m1"], False)
        check("his earlier mail in the thread does NOT count as a reply", got["m2"], True)
        check("exact unique Closed row, nothing new -> action cleared", got["m3"], False)
        check("Closed row but a live Offer row at the same company -> KEPT", got["m4"], True)
        check("Closed row but a new interview -> KEPT", got["m5"], True)
        check("ambiguous board match -> KEPT", got["m6"], True)
        check("a reply does not prove a NON-reply step -> KEPT, with the fact noted",
              (got["m7"], "replied" in (items[6].get("note") or "")), (True, True))
        check("Rejected row but a new request (no status change) -> KEPT", got["m8"], True)
        check("cleared items carry evidence, items are never removed",
              (n, len(items), all(i.get("done_evidence") for i in items if not i.get("action"))), (2, 8, True))
        md = T.render([{"category": "needs-reply", "company": "Acme", "summary": "s", "action": "",
                        "done_evidence": "you replied"}])
        check("a done item renders as handled, with no ▶️ action", ("already handled" in md, "▶️" in md), (True, False))
        # the prompt carries the evidence, tagged, and says when it is unavailable
        captured = {}
        class _R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({"content": [{"type": "text", "text": '{"items": []}'}],
                                               "usage": {}, "stop_reason": "end_turn"}).encode()
        real_open, real_env = T.urllib.request.urlopen, T.env
        T.urllib.request.urlopen = lambda req, timeout=0: (captured.update(body=json.loads(req.data)), _R())[1]
        T.env = lambda k: "test-key"
        try:
            T.classify([{"id": "x", "from": "a", "date": "d", "subject": "s", "body": "b"}], [],
                       [], "unavailable: RuntimeError", ["2026-10-07 [DONE?] Acme — ✅ availability SUBMITTED"], "ok")
        finally:
            T.urllib.request.urlopen, T.env = real_open, real_env
        u = captured["body"]["messages"][0]["content"]
        sysmsg = captured["body"]["system"][0]["text"]
        check("prompt carries the tagged log evidence", "[DONE?] Acme — ✅ availability SUBMITTED" in u, True)
        check("prompt says sent mail is UNAVAILABLE (not 'none')", "UNAVAILABLE" in u, True)
        check("each email carries its thread id", "Thread: " in u, True)
        check("rules: a sent email must be dated AFTER the email", "dated AFTER the email" in sysmsg, True)
        check("rules: a [PLAN] line is never evidence", "NEVER evidence" in sysmsg, True)
        check("rules: same step, not just same company", "different step at the same" in sysmsg, True)
    finally:
        T.log = real_log

# ── THE MORNING BRIEF MUST NOT SERVE YESTERDAY'S INBOX OR AN EMPTY PLAN ──────
def test_brief_inbox_and_plan_are_current():
    print("B1. brief: only today's, non-failed triage is used; the plan is the open THIS WEEK rows")
    sys.path.insert(0, str(HERE.parent / "hae"))
    import health_morning_brief_gate as G
    d = Path(tempfile.mkdtemp())
    real_tj, real_ai, real_log = G.TRIAGE_JSON, G.ACTION_ITEMS, G._log
    G._log = lambda *a, **k: None
    try:
        G.TRIAGE_JSON = d / "t.json"
        item = {"category": "needs-reply", "company": "Acme", "summary": "s", "action": "",
                "done_evidence": "you replied"}
        G.TRIAGE_JSON.write_text(json.dumps({"generated_at": "2026-10-07T10:40:00+00:00", "items": [item]}))
        check("yesterday's triage is NOT today's inbox", G.gather_inbox_checked("2026-10-08"), ([], "stale: last ran 2026-10-07"))
        G.TRIAGE_JSON.write_text(json.dumps({"generated_at": "2026-10-08T10:40:00+00:00", "failed": "boom", "items": []}))
        check("a failed triage is reported as failed", G.gather_inbox_checked("2026-10-08")[1].startswith("failed"), True)
        G.TRIAGE_JSON.write_text(json.dumps({"generated_at": "2026-10-08T10:40:00+00:00", "items": [item]}))
        items, st = G.gather_inbox_checked("2026-10-08")
        check("today's triage is used, done evidence kept", (st, items[0].get("done_evidence")), ("ok", "you replied"))
        G.ACTION_ITEMS = d / "ai.md"
        G.ACTION_ITEMS.write_text(_TA_FIXTURE)
        hd = G.gather_action_items("2026-10-08")["hard_deadlines"]
        check("the plan carries the open rows", ("interview prep ladder" in hd, "old task A" in hd), (True, False))
        G.ACTION_ITEMS = d / "missing.md"
        check("an unreadable task list says UNKNOWN, not nothing",
              G.gather_action_items("2026-10-08")["hard_deadlines"].startswith("UNKNOWN"), True)
    finally:
        G.TRIAGE_JSON, G.ACTION_ITEMS, G._log = real_tj, real_ai, real_log

# ── THE PREP NUDGE AIMS AT THE LIVE TARGET AND TONIGHT'S PLANNED WORK ─────────
# 2026-10-08: it printed a hard-coded target company for a week while the
# real target was a booked interview, and "days since rep" ignored the scorecard. Synthetic.
def test_prep_nudge_live_target_and_plan():
    print("P1. prep-nudge: target from frontmatter, tonight's row from the plan's WORK table, last rep = max(log, scorecard)")
    from zoneinfo import ZoneInfo
    from datetime import datetime as _dt
    t = _dt.now(ZoneInfo("America/Toronto")).date()     # prep_nudge uses Toronto time (review: no flake near midnight)
    lab = f"{t:%a} {t:%b} {t.day}"
    v = build(sess_days_ago=20, snap_days_ago=0)
    rated = (t - timedelta(days=2)).isoformat()
    (v / "09 - Systems" / "Hermes" / "prep-queue-snapshot.md").write_text(
        SNAPSHOT.format(gen=t.isoformat(), n=2).replace("| GREEN | 2026-09-12 |", f"| GREEN | {rated} |"))
    tr = v / "00 - Dashboard" / "Interview Prep.md"
    tr.write_text(tr.read_text().replace("type: prep-tracker\n",
        "type: prep-tracker\nlive_target: \"Acme SWE interviews\"\n"
        f"live_target_date: {(t + timedelta(days=5)).isoformat()}\nlive_plan: \"Plans/Acme Plan.md\"\n"))
    (v / "Plans").mkdir()
    (v / "Plans" / "Acme Plan.md").write_text(
        "## Midline (not the plan)\n| Day | Block | Must |\n|---|---|---|\n"
        f"| **{lab}** | evening | WRONG midline work |\n\n"
        "## Ceiling\n| Day | Where | Ceiling h | The work | Analogue |\n|---|---|---|---|---|\n"
        f"| **🔥 {lab}** | home | **6** | RIGHT ceiling work | x |\n")
    st = state(v)
    check("target read from frontmatter, with the countdown",
          (st.get("target", "").startswith("Acme SWE interviews"), "5 day(s) away" in st.get("target", "")), (True, True))
    check("tonight's row comes from the WORK table, not the midline",
          ("RIGHT ceiling work" in st.get("todays_plan", ""), "WRONG" in st.get("todays_plan", "")), (True, False))
    check("planned hours carried", "(6 h planned)" in st.get("todays_plan", ""), True)
    check("last rep = the scorecard rating when it is newer", (st.get("last_rep_date", "")[:10], st.get("days_since_rep")), (rated, "2"))
    v2 = build(sess_days_ago=1, snap_days_ago=0)
    st2 = state(v2)
    check("no live_target field -> UNKNOWN, and no company is invented",
          (st2.get("target", "").startswith("UNKNOWN"), "bigger brands" in st2["_raw"]), (True, False))
    cfg = json.loads((HERE.parent.parent / "config" / "cron_additions.json").read_text())
    p = next(j["prompt"] for j in cfg["jobs_to_append"] if j["name"] == "prep-nudge")
    check("the prompt names no hard-coded company and uses tonight's plan",
          ("bigger brands" in p, "todays_plan" in p, "is NOT proof he skipped it" in p), (False, True, True))

# ── THE EVENING CHECK-IN: BLANK IS "NOT LOGGED", NEVER ZERO ──────────────────
def test_evening_summary_blank_is_unknown():
    print("V1. evening summary: nothing logged -> one-line ask; blanks are 'not logged'; vitamins are a question")
    from zoneinfo import ZoneInfo
    from datetime import datetime as _dt
    td = _dt.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")
    def run(fm_extra="", meal=False):
        v = Path(tempfile.mkdtemp())
        (v / "04 - Daily Notes").mkdir(parents=True)
        (v / "04 - Daily Notes" / f"{td}.md").write_text(f"---\ntype: daily\nkcal: \nprotein_g: \nwater_l: \n{fm_extra}---\n# day\n")
        if meal:
            (v / "07 - Health" / "Food Log").mkdir(parents=True)
            (v / "07 - Health" / "Food Log" / f"{td}.md").write_text("## 7:30 PM · Dinner\n- pasta\n")
        out = subprocess.run(["bash", str(HERE / "evening_summary.sh")], capture_output=True, text=True,
                             env={"PATH": "/usr/bin:/bin", "HERMES_VAULT": str(v)}).stdout
        return dict(l.split(": ", 1) for l in out.splitlines() if ": " in l)
    a = run()
    check("nothing logged -> tracked_today no", a.get("tracked_today", "").startswith("no"), True)
    check("blank kcal is 'not logged', never 0", a.get("kcal_so_far", "").startswith("not logged"), True)
    check("no 'missing' claims on an untracked day", a.get("missing", "").startswith("unknown"), True)
    b = run("kcal: 1500\n", meal=True)
    check("logged day: real numbers, dinner seen, water missing",
          (b.get("tracked_today"), b.get("kcal_so_far", "")[:4], b.get("dinner_logged"), b.get("missing")),
          ("yes", "1500", "yes", "water"))
    check("vitamins are a question, not 'not taken'", b.get("vitamins", "").startswith("unknown — ask"), True)
    cfg = json.loads((HERE.parent.parent / "config" / "cron_additions.json").read_text())
    p = next(j["prompt"] for j in cfg["jobs_to_append"] if j["name"] == "health-evening-summary-nudge")
    check("prompt: one-line ask when nothing is logged; vitamins asked", ("tracked_today` is no" in p, "Vitamins today?" in p), (True, True))

# ── done_facts: the shared "what is already done / is the vault current" helper ──
def test_done_facts():
    print("N1. done_facts: machine scopes excluded, day window, tags, priority budget, freshness kinds")
    sys.path.insert(0, str(HERE.parent / "vault"))
    import done_facts as DF
    from datetime import datetime as _dt, timezone as _tz
    v = Path(tempfile.mkdtemp())
    def day(d, *lines):
        p = v / "Log" / d[:4] / d[:7] / f"{d}.md"; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\ntype: log\n---\n\n" + "\n\n".join(lines) + "\n")
    day("2026-10-01", "## [2026-10-01] update | Acme — ✅ OLD outside the window")
    day("2026-10-06", "## [2026-10-06] update | Acme — availability SUBMITTED",
        "## [2026-10-06] decision | Plan — his list for tomorrow: submit the Acme form")
    day("2026-10-07", "## [2026-10-07] ingest | hermes:daily-note-prefill — machine entry",
        "## [2026-10-07] update | log-food — machine entry 2",
        "## [2026-10-07] update | Widgetco — reply sent to the recruiter",
        "## [2026-10-07] update | Gizmo — " + "x" * 450 + " finally SUBMITTED",
        "## [2026-10-07] update | Misc — closes Sat Oct 10, nothing finished")
    lines, om = DF.done_lines(v, days=4, today=date(2026, 10, 7))
    txt = "\n".join(lines)
    check("machine scopes (hermes:, log-) are excluded", ("machine entry" in txt), False)
    check("entries outside the day window are excluded", "OLD outside" in txt, False)
    check("a decision is tagged PLAN, never DONE", "[PLAN] Plan — his list" in txt, True)
    check("completion words tag DONE? (case-insensitive)", ("[DONE?] Acme" in txt, "[DONE?] Widgetco" in txt), (True, True))
    check("a completion word past the cut still tags DONE?", "[DONE?] Gizmo" in txt, True)
    check("'Sat' (Saturday) is not a completion", "[NOTE] Misc" in txt, True)
    small, om2 = DF.done_lines(v, days=4, today=date(2026, 10, 7), max_total=160)
    check("a tight budget keeps DONE? lines first and reports what it omitted",
          (all("[DONE?]" in l for l in small), om2 > 0), (True, True))
    now = _dt(2026, 10, 7, 20, 0, tzinfo=_tz.utc)
    sp = v / "wd.json"
    def fr(kinds, checked_h_ago=0.5):
        sp.write_text(json.dumps({"bad": bool(kinds), "kinds": kinds,
                                  "checked": (now - timedelta(hours=checked_h_ago)).isoformat()}))
        return DF.freshness(now, sp)["state"]
    check("no problems -> ok", fr([]), "ok")
    check("a closed Mac (heartbeat only) is NOT 'sync_broken'", fr(["heartbeat"]), "ok")
    check("pending uploads -> sync_broken", fr(["pending"]), "sync_broken")
    check("a watchdog that stopped running -> unknown", fr([], checked_h_ago=5), "unknown")
    check("no state file -> unknown", DF.freshness(now, v / "missing.json")["state"], "unknown")
    sys.path.insert(0, str(HERE.parent.parent / "scripts" / "internship"))
    import role_exclusions as RX
    check("role exclusions: analyst/scientist/firmware/embedded out; data/analytics ENGINEER, SWE, ML in",
          [RX.excluded(t) for t in ("Data Analyst Intern", "Data Scientist Intern", "Firmware Intern",
                                    "SWE Intern (Embedded Systems)", "Data Engineer Intern",
                                    "Data Analytics Engineer Intern", "Software Engineer Intern",
                                    "Machine Learning Intern")],
          [True, True, True, True, False, False, False, False])

# ── A RATED REP IN THE VAULT LOG IS A REP ────────────────────────────────────
# 2026-10-09: the nudge said "last rep 12 days ago, no rep logged" the morning after ten
# logged reps ("78 Subsets 🔴", "200 Number of Islands 🟢") — reps were only read from the
# hand-kept Session log and the scorecard. Planning sentences must NOT count.
def test_prep_nudge_counts_logged_reps():
    print("P2. prep-nudge: a rated problem in the log is a rep; planning text with a rating emoji is not")
    from zoneinfo import ZoneInfo
    from datetime import datetime as _dt
    t = _dt.now(ZoneInfo("America/Toronto")).date()
    v = build(sess_days_ago=20, snap_days_ago=0)
    y = (t - timedelta(days=1)).isoformat()
    p = v / "Log" / y[:4] / y[:7] / f"{y}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(f"## [{y}] update | Acme / Interview Prep — rep 1 of the ladder: 78 Subsets 🔴 (22 min)\n")
        f.write(f"## [{y}] update | Acme / Interview Prep — 200 Number of Islands 🟢, 8 min\n")
    td = t.isoformat()
    p2 = v / "Log" / td[:4] / td[:7] / f"{td}.md"
    p2.parent.mkdir(parents=True, exist_ok=True)
    with open(p2, "a") as f:
        f.write(f"## [{td}] update | Acme / Interview Prep — queue made explicit: only 78/46 had dated slots. Protocol 🔴\n")
    st = state(v)
    check("yesterday's rated reps set the last rep", (st.get("last_rep_date", "")[:10], st.get("days_since_rep")), (y, "1"))
    check("they are listed", st.get("reps_yesterday", "").startswith("2 (78 Subsets"), True)
    check("a planning line with a rating emoji is NOT a rep", st.get("reps_today"), "0")

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
           test_sync_watchdog, test_hermes_recent_events_recipe,
           test_today_actions_reads_open_rows_only, test_email_triage_done_checks,
           test_brief_inbox_and_plan_are_current, test_prep_nudge_live_target_and_plan,
           test_evening_summary_blank_is_unknown, test_done_facts,
           test_prep_nudge_counts_logged_reps):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        print(f"  ❌ {fn.__name__} RAISED {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
