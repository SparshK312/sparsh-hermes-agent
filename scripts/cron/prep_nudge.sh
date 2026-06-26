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
# Known low-prep windows (inclusive) — nudge stays silent. Keep these in sync with
# his calendar (CSC384 midterm Jul 7; SF/YC Jul 24-29). Add ranges as needed.
LOW_PREP_WINDOWS = [
    ("2026-07-06", "2026-07-07"),   # CSC384 midterm cram + day
    ("2026-07-24", "2026-07-29"),   # SF / YC Startup School
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
    if h and ("Pattern" in ln or ln.strip().startswith("#")):
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
last_rep = log_dates[-1] if log_dates else None
days_since = (today - datetime.date.fromisoformat(last_rep)).days if last_rep else None

# current streak = consecutive days (ending today or yesterday) present in the log
streak = 0
have = set(log_dates)
probe = today
if today_s not in have:                 # not logged yet today -> count from yesterday
    probe = today - datetime.timedelta(days=1)
while probe.isoformat() in have:
    streak += 1
    probe -= datetime.timedelta(days=1)

# ---- redo list: items due for re-solve ---------------------------------------
redo_due = []
for ln in section("Redo list").splitlines():
    if ln.strip().startswith("|") and "Re-solve" not in ln and "---" not in ln:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        # columns: Problem | Pattern | First solved | Re-solve due | ✓
        if len(cells) >= 5:
            prob, due, donecol = cells[0], cells[3], cells[4]
            if re.match(r"20\d\d-\d{2}-\d{2}", due) and due <= today_s and not donecol.strip():
                redo_due.append(prob)

fresh_start = (done == 0 and not log_dates)
days_since_start = (today - datetime.date.fromisoformat(PREP_START_DATE)).days
if fresh_start:
    # never logged a single rep -> escalate once it's been a couple days since go-live
    escalate = days_since_start >= ESCALATE_DAYS_FRESH
else:
    escalate = (days_since is not None and days_since >= ESCALATE_DAYS)

# ---- emit STATE block ---------------------------------------------------------
L = [f"[prep-nudge state · {today_s}]"]
L.append(f"progress: {done}/{total} items done")
L.append(f"next_item: {next_item or 'all checked — set the next focus'}"
         + (f"  (context: {next_ctx})" if next_ctx else ""))
L.append(f"last_rep_date: {last_rep or 'none yet'}")
L.append(f"days_since_rep: {days_since if days_since is not None else 'n/a (never logged)'}")
L.append(f"current_streak_days: {streak}")
L.append(f"redo_due: {', '.join(redo_due) if redo_due else 'none'}")
L.append(f"escalate: {'yes' if escalate else 'no'}")
L.append(f"fresh_start: {'yes' if fresh_start else 'no'}")
L.append(f"days_since_prep_went_live: {days_since_start}")
L.append("target: Mercor (referral-guaranteed interview, triggers when ready) + bigger brands")
L.append("plan: [[Technical Interview Study Plan]] · log a rep by replying e.g. 'did Two Sum + Valid Anagram'")
print("\n".join(L))
PY
