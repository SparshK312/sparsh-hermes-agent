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
def _log_prep_dates():
    try:
        raw = (V / "Log.md").read_bytes()[-900_000:].decode("utf-8", "ignore")
    except Exception:
        return [], None
    out, newest = [], None
    for m in re.finditer(r"^## \[(20\d\d-\d{2}-\d{2})\]\s+(\w+)\s+\|([^\n]*)", raw, re.M):
        d, action, scope = m.group(1), m.group(2), m.group(3)
        if newest is None or d > newest:
            newest = d
        if re.search(r"interview prep|prep\b", scope, re.I) and action in ("update", "ingest"):
            out.append(d)
    return sorted(set(out)), newest

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
logmd_dates, logmd_newest_any = _log_prep_dates()
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
have = set(log_dates)          # reps only — see the note above
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
redo_due, redo_state = [], "unknown"
snap = V / "09 - Systems" / "Hermes" / "prep-queue-snapshot.md"
try:
    stxt = snap.read_text(encoding="utf-8")
    gen = re.search(r"^generated:\s*(20\d\d-\d{2}-\d{2})", stxt, re.M)
    gen_date = datetime.date.fromisoformat(gen.group(1)) if gen else None
    age = (today - gen_date).days if gen_date else None
    if age is not None and age <= SNAPSHOT_MAX_AGE_DAYS:
        redo_state = "fresh"
        for ln in stxt.splitlines():
            if ln.startswith("|") and ln.rstrip().endswith("YES |"):
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                if len(cells) >= 2:
                    redo_due.append(cells[1])
    else:
        redo_state = f"stale (snapshot generated {gen_date}, {age}d old)"
except Exception:
    redo_state = "unknown (no snapshot — run Scripts/catchup.py on the Mac)"

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
L.append("target: Mercor (referral-guaranteed interview, triggers when ready) + bigger brands")
L.append("plan: [[Technical Interview Study Plan]] · log a rep by replying e.g. 'did Two Sum + Valid Anagram'")
print("\n".join(L))
PY
