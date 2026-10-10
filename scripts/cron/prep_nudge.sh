#!/usr/bin/env bash
# Hermes cron job: prep-nudge — daily interview-prep accountability nudge.
#
# AGENT job by design (same pattern as the health nudges): this script reads the live
# Prep Tracker ([[00 - Dashboard/Interview Prep.md]]) and emits a compact STATE block;
# the agent composes the actual Telegram message from it per the job's `prompt` in
# config/cron_additions.json. The script self-suppresses (prints {"wakeAgent": false})
# on KNOWN low-prep windows (CSC384 midterm + SF/YC) so it stays quiet then.
#
# Cadence: daily streak (evening). Escalates gently after ESCALATE_DAYS quiet days.
set -uo pipefail

/usr/bin/python3 - <<'PY'
import re, datetime, os
from pathlib import Path
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Toronto")
except Exception:
    TZ = None

# ---- config -------------------------------------------------------------------
ESCALATE_DAYS = 3            # quiet days after a rep before the nudge gets pointed
ESCALATE_DAYS_FRESH = 2      # days since prep went live with STILL ZERO reps -> escalate
PREP_START_DATE = "2026-06-24"   # when the nudge went live (for never-started escalation)
# Known low-prep windows (inclusive) — nudge stays silent. Hand-maintained: when a
# window passes, add the next one. Both original entries had EXPIRED, so the nudge had
# no coverage for the CSC384 final or Peru — the two stretches it most obviously should
# not nag through. Check this list whenever a fixed date lands in Life Context.
LOW_PREP_WINDOWS = [
    ("2026-07-06", "2026-07-07"),   # CSC384 midterm cram + day (past)
    ("2026-07-24", "2026-07-29"),   # SF / YC Startup School (past)
    ("2026-08-15", "2026-08-22"),   # CSC384 final run-up + exam day (Sat Aug 22, 9am, 65% of grade)
    ("2026-08-29", "2026-09-07"),   # Peru
]

def vault() -> Path:
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    return vps if vps.exists() else Path.home() / "Documents" / "School Vault - UofT"

now = datetime.datetime.now(TZ) if TZ else datetime.datetime.now()
today = now.date()
today_s = today.isoformat()

# ---- low-prep window gate -----------------------------------------------------
for a, b in LOW_PREP_WINDOWS:
    if a <= today_s <= b:
        print('{"wakeAgent": false}')
        raise SystemExit

V = vault()
TRACKER = V / "00 - Dashboard" / "Interview Prep.md"
try:
    txt = TRACKER.read_text(encoding="utf-8")
except Exception:
    # tracker missing -> still nudge generically
    print(f"[prep-nudge state · {today_s}]\ntracker_found: no\nnote: could not read the Prep Tracker; nudge him to start with Two Sum (Pattern 1).")
    raise SystemExit

# Only look at the live tracker, not the archived May plan below it.
live = txt.split("## 🗄️", 1)[0]

# ---- checkbox progress + next item -------------------------------------------
done = len(re.findall(r"\[x\]", live, re.I))
todo = len(re.findall(r"\[ \]", live))
total = done + todo

# next unchecked item: text after the first "[ ]" up to a "·" or end of line,
# plus the nearest preceding bold/heading for context.
next_item, next_ctx = None, None
ctx = None
for ln in live.splitlines():
    h = re.match(r"\s*(?:#{2,4}\s+|\*\*)(.+?)(?:\*\*)?\s*$", ln)
    if h and (ln.strip().startswith("#") or ln.strip().startswith("**")):
        # capture both ## section headers AND **bold pattern names** so the nudge's
        # next-item context is the specific pattern (e.g. "Arrays / Strings / Hash").
        ctx = re.sub(r"[*#]", "", ln).strip()
    m = re.search(r"\[ \]\s*([^·\n]+)", ln)
    if m:
        next_item = m.group(1).strip().rstrip("·").strip()
        next_ctx = ctx
        break

# ---- session log: last rep date + streak + days since ------------------------
def section(title_substr):
    # return the text of the "### ... <title_substr> ..." section up to the next "###"
    idx = live.find(title_substr)
    if idx == -1:
        return ""
    rest = live[idx:]
    nxt = rest.find("\n###", 3)
    return rest if nxt == -1 else rest[:nxt]

def dates_in(s):
    return sorted({d for d in re.findall(r"(20\d\d-\d{2}-\d{2})", s)})

log_dates = dates_in(section("Session log"))

# 🔴 THE SESSION LOG IS NOT THE FILE THAT GETS UPDATED DAILY. Log.md is.
# Measured 2026-09-25: the Session-log table's newest row was 2026-09-22 while Log.md
# carried prep entries on the 24th AND four on the 25th (including "all four 🔴s
# cleared"). This nudge therefore told him he was three days dark on a day he had done
# two sessions. The table is curated by hand when someone remembers; Log.md is appended
# by every session as it happens. Take the LATER of the two and say which one it came
# from, so a disagreement is visible instead of silently resolved the wrong way.
# 🔴 2026-10-05: the log is ONE FILE PER DAY (Log/YYYY/YYYY-MM/YYYY-MM-DD.md); Log.md is a
# MOVED stub. Read the recent day files (plus the stub, which log_append --sweep empties).
# 🔴 FRESHNESS MUST COME FROM THE MAC, NOT FROM HERMES. On 2026-10-03 this reported
# "log fresh" on a vault three days stale, because the newest entry was Hermes's OWN
# `hermes:daily-note-prefill` line, written on this machine. An entry this box wrote
# proves nothing about whether the Mac's writes are arriving. So `newest` ignores
# `hermes:` scopes; the prep dates still count every writer.
LOG_LOOKBACK_DAYS = 60
def _log_text():
    parts = []
    stub = V / "Log.md"
    if stub.exists():
        parts.append(stub.read_text("utf-8", "ignore"))
    d = V / "Log"
    if d.is_dir():
        cutoff = (today - datetime.timedelta(days=LOG_LOOKBACK_DAYS)).isoformat()
        for p in sorted(d.glob("*/*/????-??-??.md")):
            if p.stem >= cutoff:
                parts.append(p.read_text("utf-8", "ignore"))
    return "\n".join(parts)

# A REP in the log (2026-10-09): an Interview-Prep entry that names a problem AND rates it
# ("78 Subsets 🔴", "200 Number of Islands 🟢"). On Oct 9 the nudge said "last rep 12 days
# ago" the morning after TEN logged reps, because reps were only read from the hand-kept
# Session log and the scorecard. Plans/research never carry a "<number> <name> <rating>".
# Problem names are Title Case with small joiners ("Number of Islands", "Top K Frequent");
# requiring that stops a planning sentence ("46 had dated slots… Protocol 🔴") from counting.
REP_RE = re.compile(r"\b\d{1,4}\s+[A-Z][\w'’/+-]*(?:\s+(?:of|the|a|an|in|to|and|with|from|on|by|at|for|II|III|[A-Z0-9][\w'’/+-]*)){0,7}\s*(?:🔴|🟡|🟢)")

def _log_prep_dates():
    raw = _log_text()
    out, newest, reps = [], None, {}
    for m in re.finditer(r"^## \[(20\d\d-\d{2}-\d{2})\]\s+(\w+)\s+\|([^\n]*)", raw, re.M):
        d, action, scope = m.group(1), m.group(2), m.group(3)
        if not scope.strip().lower().startswith("hermes:") and (newest is None or d > newest):
            newest = d
        if re.search(r"interview prep|prep\b", scope, re.I) and action in ("update", "ingest"):
            out.append(d)
            if REP_RE.search(scope) and not scope.strip().lower().startswith("hermes:"):
                reps.setdefault(d, []).append(REP_RE.search(scope).group(0).strip())
    return sorted(set(out)), newest, reps

# ⚠️ Keep the two ideas SEPARATE. A Log.md entry scoped "Interview Prep" is often a plan
# re-cut or a research triage, not a rep at the keyboard — counting those as reps would
# invent a streak across Sep 16–22, a week the tracker itself records as "DARK — no reps
# logged". So: the Session log still defines a REP (it is curated and means he sat down),
# and Log.md defines ACTIVITY. Escalation needs BOTH to be quiet. That kills the false
# "you are three days dark" without minting a false streak in its place.
# 🔴 CHECK OUR OWN INPUT. Obsidian Sync has been erroring "Vault limit exceeded" since
# 2026-09-14, and on 09-25 the VPS copy of Log.md was 40 entries / 12 hours behind the
# Mac's. A stale Log.md makes "no prep entry recently" indistinguishable from "the file
# stopped arriving" — the exact confusion this whole fix exists to remove. So the
# freshness of the source is part of the answer, never an assumption.
LOG_STALE_DAYS = 2
logmd_dates, logmd_newest_any, log_reps = _log_prep_dates()
logmd_age = ((today - datetime.date.fromisoformat(logmd_newest_any)).days
             if logmd_newest_any else None)
logmd_stale = logmd_age is None or logmd_age > LOG_STALE_DAYS
sess_last = log_dates[-1] if log_dates else None
logmd_last = logmd_dates[-1] if logmd_dates else None
last_rep = sess_last
last_rep_src = "Session log" if sess_last else "none"
days_since = (today - datetime.date.fromisoformat(last_rep)).days if last_rep else None
days_since_activity = ((today - datetime.date.fromisoformat(logmd_last)).days
                       if logmd_last else None)

# current streak = consecutive days (ending today or yesterday) present in the log
streak = 0
have = set(log_dates) | set(log_reps)          # reps only (Session log + rated reps in the log)
probe = today
if today_s not in have:                 # not logged yet today -> count from yesterday
    probe = today - datetime.timedelta(days=1)
while probe.isoformat() in have:
    streak += 1
    probe -= datetime.timedelta(days=1)

# ---- redo list: the SNAPSHOT, not a table in the tracker ----------------------
# 🔴 This used to parse a markdown table out of the tracker's "Redo list" section. That
# section now opens with, in bold: "DO NOT MAINTAIN A TABLE HERE. IT ROTS." — the table
# was removed on 2026-09-15 after being wrong twice. So the parse matched nothing and
# `redo_due` has been the literal string "none" on EVERY run since, while the real queue
# (the scorecard app) had up to eleven problems overdue. The prompt's whole
# spaced-repetition branch — "if redo_due is not none, tell him to re-solve those" — has
# never once fired.
#
# The live queue is the scorecard, which is a local Mac app whose .json does not sync.
# `Scripts/catchup.py` writes a MARKDOWN snapshot (which does sync) every time it runs.
# Read that. If it is missing or stale, say UNKNOWN — "we could not look" is not the
# same fact as "nothing is due", and reporting one as the other is how this broke.
SNAPSHOT_MAX_AGE_DAYS = 3
redo_due, redo_state, snap_rated = [], "unknown", []
snap = V / "09 - Systems" / "Hermes" / "prep-queue-snapshot.md"
try:
    stxt = snap.read_text(encoding="utf-8")
    gen = re.search(r"^generated:\s*(20\d\d-\d{2}-\d{2})", stxt, re.M)
    gen_date = datetime.date.fromisoformat(gen.group(1)) if gen else None
    age = (today - gen_date).days if gen_date else None
    if age is not None and age <= SNAPSHOT_MAX_AGE_DAYS:
        redo_state = "fresh"
        for ln in stxt.splitlines():
            if ln.startswith("|"):
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                if len(cells) >= 5 and re.match(r"20\d\d-\d{2}-\d{2}$", cells[4]):
                    snap_rated.append(cells[4])          # the scorecard's rating date = a rep
                if ln.rstrip().endswith("YES |") and len(cells) >= 2:
                    redo_due.append(cells[1])
    else:
        redo_state = f"stale (snapshot generated {gen_date}, {age}d old)"
except Exception:
    redo_state = "unknown (no snapshot — run Scripts/catchup.py on the Mac)"

# 2026-10-08 (review): the hand-kept Session log stopped at Sep 24 while the scorecard has
# ratings through Sep 27, so "days since your last rep" was 3 days too long. A rating IS a
# rep (he solved it and rated it). Take the later of the two and say which.
snap_last = max(snap_rated) if snap_rated else None
log_rep_last = max(log_reps) if log_reps else None
if log_rep_last and (not last_rep or log_rep_last > last_rep) and (not snap_last or log_rep_last >= snap_last):
    last_rep, last_rep_src = log_rep_last, "rated rep in the vault log"
    days_since = (today - datetime.date.fromisoformat(last_rep)).days
if snap_last and (not last_rep or snap_last > last_rep):
    last_rep, last_rep_src = snap_last, "scorecard rating"
    days_since = (today - datetime.date.fromisoformat(last_rep)).days

# ---- the LIVE TARGET: structured fields, never a hard-coded company -----------------
# 2026-10-08: this printed a hard-coded target company for a week while the real target was a
# different, already-booked interview. The Interview Prep banner is free
# text and goes stale ("Oct 15 or 16"), so the target is read from three frontmatter fields
# Claude sessions keep current: live_target, live_target_date (YYYY-MM-DD), live_plan
# (vault-relative path of the plan note whose day table names each day's work).
def _fm(key):
    head = txt.split("\n---", 1)[0] if txt.startswith("---") else ""   # the frontmatter block
    m = re.search(rf"^{key}:\s*(.+?)\s*$", head, re.M)
    return m.group(1).strip().strip('"') if m else None
live_target, live_date, live_plan = _fm("live_target"), _fm("live_target_date"), _fm("live_plan")
days_to_target = None
try:
    if live_date:
        days_to_target = (datetime.date.fromisoformat(live_date) - today).days
except ValueError:
    live_date = f"{live_date} (unparseable)"
todays_plan, todays_hours, plan_state = None, None, "no live_plan field"
if live_plan:
    try:
        ptxt = (V / live_plan).read_text(encoding="utf-8")
        label = f"{today:%a} {today:%b} {today.day}"          # "Thu Oct 8"
        hdr = None
        for ln in ptxt.splitlines():
            if not ln.startswith("|"):
                hdr = None if not ln.strip() else hdr
                continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if cells and cells[0].lower() == "day":
                # Only the table with a "work" column is the plan. The same note also has a
                # §5B MIDLINE day table, which he ruled is NOT the plan (Oct 8) and which
                # came first, so the first "Day" table returned the wrong work (caught in testing).
                hdr = [c.lower() for c in cells] if any("work" in c.lower() for c in cells) else None
                continue
            if hdr and re.search(rf"\b{re.escape(label)}\b", re.sub(r"[*🔥]", "", cells[0])):
                wi = next((i for i, h in enumerate(hdr) if "work" in h), len(cells) - 1)
                hi = next((i for i, h in enumerate(hdr) if h.endswith(" h") or "hours" in h), None)
                todays_plan = cells[wi] if wi < len(cells) else None
                todays_hours = cells[hi].strip("* ") if hi is not None and hi < len(cells) else None
                break
        plan_state = "found" if todays_plan else f"no row for {label} in the plan's day table"
    except Exception as e:
        plan_state = f"plan unreadable ({type(e).__name__})"
# what he LOGGED as prep today (the done half of done-vs-planned)
prep_today = []
for m in re.finditer(r"^## \[" + today_s + r"\]\s+(\w+)\s+\|([^\n]*)", _log_text(), re.M):
    scope_summary = m.group(2)
    if re.search(r"interview prep|\bprep\b|leetcode|re-solve|mock", scope_summary, re.I) \
            and not scope_summary.strip().lower().startswith("hermes:"):
        prep_today.append(re.sub(r"\s+", " ", scope_summary).strip()[:180])

fresh_start = (done == 0 and not log_dates)
days_since_start = (today - datetime.date.fromisoformat(PREP_START_DATE)).days
if fresh_start:
    # never logged a single rep -> escalate once it's been a couple days since go-live
    escalate = days_since_start >= ESCALATE_DAYS_FRESH
else:
    # Quiet on BOTH channels, or it is not a lapse. Before 2026-09-25 this looked only
    # at the hand-curated Session log and escalated on days he had visibly done prep.
    _rep_quiet = days_since is not None and days_since >= ESCALATE_DAYS
    _act_quiet = days_since_activity is None or days_since_activity >= ESCALATE_DAYS
    # If Log.md itself is stale we cannot SEE the activity channel, so its quiet is not
    # evidence. Never get pointed with him on the strength of a file that stopped arriving.
    escalate = _rep_quiet and _act_quiet and not logmd_stale

# ---- emit STATE block ---------------------------------------------------------
L = [f"[prep-nudge state · {today_s}]"]
L.append(f"progress: {done}/{total} items done")
L.append(f"next_item: {next_item or 'all checked — set the next focus'}"
         + (f"  (context: {next_ctx})" if next_ctx else ""))
L.append(f"last_rep_date: {last_rep or 'none yet'}  (source: {last_rep_src})")
L.append(f"last_session_log_row: {sess_last or 'none'}")
L.append(f"last_prep_entry_in_Log_md: {logmd_last or 'none'}")
L.append(f"log_md_newest_entry: {logmd_newest_any or 'none'}"
         + (f"   ⚠️ STALE by {logmd_age}d — Log.md is not reaching this machine (check "
            f"Obsidian Sync). Treat prep activity as UNKNOWN, not zero."
            if logmd_stale else "   (fresh)"))
L.append(f"days_since_prep_activity: {days_since_activity if days_since_activity is not None else 'n/a'}"
         f"   (any prep-scoped Log.md entry — planning counts here, NOT as a rep)")
L.append(f"days_since_rep: {days_since if days_since is not None else 'n/a (never logged)'}")
L.append(f"current_streak_days: {streak}")
if redo_state == "fresh":
    L.append(f"redo_due: {', '.join(redo_due) if redo_due else 'none (queue is clear)'}")
else:
    # NEVER print "none" here when we could not look. See the block above.
    L.append(f"redo_due: UNKNOWN — {redo_state}. Do NOT tell him the queue is clear; "
             f"say the queue could not be read and to run catchup.py.")
L.append(f"redo_source: {redo_state}")
L.append(f"escalate: {'yes' if escalate else 'no'}")
L.append(f"fresh_start: {'yes' if fresh_start else 'no'}")
L.append(f"days_since_prep_went_live: {days_since_start}")
if live_target:
    L.append(f"target: {live_target[:200]}"
             + (f"  (on {live_date}; {days_to_target} day(s) away)" if days_to_target is not None and days_to_target >= 0
                else (f"  (date {live_date} has PASSED — the target may be stale)" if days_to_target is not None else "")))
else:
    L.append("target: UNKNOWN — Interview Prep.md has no live_target field. Do not name a company.")
L.append(f"todays_plan: {todays_plan[:700] if todays_plan else 'none — ' + plan_state}"
         + (f"  ({todays_hours} h planned)" if todays_plan and todays_hours else ""))
L.append(f"reps_today: {len(log_reps.get(today_s, []))}"
         + (f" ({', '.join(log_reps[today_s][:8])})" if log_reps.get(today_s) else ""))
L.append(f"reps_yesterday: {len(log_reps.get((today - datetime.timedelta(days=1)).isoformat(), []))}"
         + (f" ({', '.join(log_reps[(today - datetime.timedelta(days=1)).isoformat()][:8])})" if log_reps.get((today - datetime.timedelta(days=1)).isoformat()) else ""))
L.append("prep_logged_today: " + (" || ".join(prep_today[:4]) if prep_today else
                                  "nothing prep-scoped in today's log yet (he may simply not have logged it)"))
plan_link = f"[[{Path(live_plan).stem}]]" if live_plan else "[[Interview Prep]]"
L.append(f"plan: {plan_link} · log a rep by replying e.g. 'did 78 Subsets + 46 Permutations'")
print("\n".join(L))
PY
