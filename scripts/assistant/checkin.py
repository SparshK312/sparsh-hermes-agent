#!/usr/bin/env python3
"""
checkin.py — the hourly check-in. Wakes up, looks for ONE thing worth saying, and either
says it or says nothing. Replaces the fixed lunch / meal-rescue / water / dinner /
evening / stopped-logging nudges once it is switched on (cutover.sh).

HOW A TICK WORKS
  1. Code builds candidates, most important first:
       an open loop that is due (for a person he is waiting on, his inbox is checked for
       mail from them since the loop opened), then health gaps past their time of day
       (sessions owed this week, lunch, dinner, vitamins).
  2. No candidate, or the budget (outbox.allow) says no  ->  exit. No model call, no message.
  3. For the first allowed candidate, CODE writes the message, including the sentence that
     says what was checked: "found", "checked and found nothing", or "could not check".
  4. One small model call may reword it to sound like a person. ground.validate() checks
     the rewording against the facts; anything that fails is discarded and the
     code-written message is used. Nothing unvalidated is ever sent.
  5. Live: send, record in the ledger, note the ask on the loop.
     Shadow: write what WOULD have been sent to shadow.jsonl and the vault report. The
     real ledger and the loops are not touched.

SHADOW IS THE DEFAULT. It goes live only when ~/.hermes/assistant/LIVE exists (created by
cutover.sh) and --shadow is not passed.

Bookkeeping only (his call): it asks and reports; it never changes his calendar, the
board, or anything outbound. It reads Gmail and never writes to it.

  checkin.py [--shadow] [--dry-run] [--no-llm] [--now 2026-11-17T14:35]
Stdlib only. Stdout stays empty (the cron is --no-agent); diagnostics go to stderr.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
for _name in ("fitness", "email", "vault"):
    for _cand in (HERE.parent / _name, Path.home() / ".hermes" / "scripts" / _name):
        if _cand.is_dir():
            sys.path.insert(0, str(_cand))
            break

import ground  # noqa: E402
import llm  # noqa: E402
import loops  # noqa: E402
import outbox  # noqa: E402

TZ = loops.TZ
MAX_MAIL_CHECKS = 3
SHADOW_CAP = outbox.DAILY_NUDGE_CAP
REPORT_REL = Path("09 - Systems") / "Hermes" / "Assistant Shadow Report.md"
REPORT_DAYS = 4

LUNCH_WINDOW = (datetime.time(14, 30), datetime.time(18, 0))
DINNER_WINDOW = (datetime.time(20, 30), datetime.time(22, 0))
VITAMIN_WINDOW = (datetime.time(21, 0), datetime.time(22, 0))
LIFT_FROM = datetime.time(17, 0)


def log(msg: str) -> None:
    print(f"[checkin] {msg}", file=sys.stderr)


def live_flag() -> Path:
    return outbox.base_dir() / "LIVE"


def shadow_path() -> Path:
    return outbox.base_dir() / "shadow.jsonl"


@dataclass
class Candidate:
    topic: str                    # the once-a-day key shared with the brief ("loop:<id>", "lunch", …)
    status: str                   # found | checked_none | could_not_check | fact
    draft: str                    # the code-written message; always sendable as is
    facts: dict = field(default_factory=dict)
    expects_reply: bool = True
    loop_id: str | None = None
    polish: bool = True           # False = send the draft as written (it is already checked)

    def facts_text(self) -> str:
        return "\n".join(f"{k}: {v}" for k, v in self.facts.items())


# ----------------------------------------------------------------- small formatters
def clock(n: datetime.datetime) -> str:
    return n.strftime("%I:%M %p").lstrip("0")


def day(d) -> str:
    if isinstance(d, str):
        d = datetime.datetime.fromisoformat(d)
    return f"{d.strftime('%a %b')} {d.day}"


def _sentence(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if not s or s[-1] in ".?!" else s + "."


# ----------------------------------------------------------------- mail check (read-only)
_NAME = re.compile(r"^[A-Z][A-Za-z'’-]+( [A-Z][A-Za-z'’-]+){0,2}$")


def searchable(who: str) -> bool:
    """Only a real-looking name or an address is worth a mail search. 'a friend',
    'the recruiter' or 'forms team' would search for nothing and report it as a finding."""
    who = (who or "").strip()
    return bool(who) and ("@" in who or bool(_NAME.match(who)))


# 2026-11-18: a loop waiting on a company matched that company's weekly product digest
# ("Globex Health <…-noreply@…>: Your weekly progress report") and the draft asked whether
# the thread he was waiting on was settled.
# from:"<who>" matches every sender whose name or address contains the word, so a company
# name pulls in its newsletters, receipts and product mail. A message counts only when a
# person sent it, or when an automated sender is plainly about hiring.
MAIL_MAX = 10
_AUTO_ADDR = re.compile(r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|notif|newsletter|digest|news|marketing|"
                        r"mailer|updates?|receipts?|billing|invoice|support|alerts?|announce|promo|rewards|"
                        r"hello|info|team|account|security|store|shop|community|bounce)", re.I)
_HIRING = re.compile(r"\b(recruit\w*|talent|careers?|hiring|application|applied|applying|candidate|interview\w*|"
                     r"offer|position|role|intern(ship)?|assessment|next steps?|onboarding|referral|"
                     r"university|early careers?|campus)\b", re.I)


def _addr(frm: str) -> str:
    m = re.search(r"<([^>]+)>", frm or "")
    return (m.group(1) if m else (frm or "")).strip().lower()


def relevant_mail(who: str, sender: str, subject: str) -> bool:
    """Is this message plausibly from the party he is waiting on?
       - `who` is an address: the search already pinned the sender.
       - a person wrote it (not an automated mailbox, and the display name is not the
         company's own name): yes.
       - an automated mailbox or the company itself: only when the sender name or subject is about hiring
         ("Meta Recruiting", "Your application to…"), never a product or account mail."""
    if "@" in (who or ""):
        return True
    addr, name = _addr(sender), _sender_name(sender)
    local, _, domain = addr.partition("@")
    labels = [re.sub(r"[^a-z0-9]", "", x) for x in domain.split(".")[:-1]]
    flat = re.sub(r"[^a-z0-9]", "", name.lower())
    # "Globex <globex@globex.example.com>" is the company writing, not a person: the display name carries
    # the domain's own name. A bare address with no display name is judged on its mailbox.
    company_sender = "@" not in name and any(
        len(x) >= 3 and x not in ("com", "mail", "email", "www") and x in flat for x in labels)
    if not company_sender and not _AUTO_ADDR.search(local):
        return True
    return bool(_HIRING.search(f"{name} {subject}"))


def mail_from(who: str, since: datetime.date):
    """(status, info). Reads Gmail through the google-workspace script; never writes.
       found            {date, subject, sender}   newest RELEVANT message (see relevant_mail)
       checked_none     {}                        the search ran; nothing relevant came back
       could_not_check  {reason}                  not searchable, the search failed, or the
                                                  first MAIL_MAX results were all automated
                                                  mail (more may exist that were not seen)"""
    if not searchable(who):
        return "could_not_check", {"reason": "no name or address to search for"}
    try:
        import email_triage as ET  # noqa: PLC0415  (functions only; main() is never called)
        q = f'from:"{who}" after:{since.strftime("%Y/%m/%d")} -in:sent -in:draft'
        msgs = ET._parse_search(ET._gapi("gmail", "search", q, "--max", str(MAIL_MAX), timeout=60))
    except Exception as e:  # noqa: BLE001
        log(f"mail check failed for {who!r}: {type(e).__name__}: {e}")
        return "could_not_check", {"reason": "the inbox search failed"}
    if not msgs:
        return "checked_none", {}
    skipped = 0
    for m in msgs:
        sender = str(m.get("from") or m.get("sender") or "")
        subject = str(m.get("subject") or "")
        if relevant_mail(who, sender, subject):
            if skipped:
                log(f"mail check for {who!r}: skipped {skipped} automated message(s) before a relevant one")
            return "found", {"date": str(m.get("date") or "")[:31], "subject": subject[:140], "sender": sender[:80]}
        skipped += 1
    log(f"mail check for {who!r}: all {skipped} result(s) were automated mail, none about hiring")
    if skipped >= MAIL_MAX:      # the cap was reached: older relevant mail may sit behind it
        return "could_not_check", {"reason": f"only automated mail from {who} in the newest {MAIL_MAX} results"}
    return "checked_none", {}


# A wait with nothing planned ("no further chasing, await response"). First shadow run,
# 2026-11-16: the loop asked "has anything come in another way?" about exactly such a
# thread. For these the only thing worth saying is that mail ARRIVED.
PASSIVE = re.compile(r"\b(no further|nothing owed|no action|await|wait for|no chas|not chas|nothing to do)", re.I)


# ----------------------------------------------------------------- candidates: open loops
def loop_candidate(lid: str, lp: dict, n: datetime.datetime, mail=mail_from) -> Candidate | None:
    title, who, step = lp["title"], lp.get("who") or "", lp.get("next_step") or ""
    opened = datetime.datetime.fromisoformat(lp["opened_at"]).date()
    facts = {"thread": title, "opened": day(lp["opened_at"]), "now": f"{day(n)}, {clock(n)}"}
    if who:
        facts["who"] = who
    if step:
        facts["plan on file"] = step
    if lp.get("due"):
        facts["due"] = day(lp["due"] + "T00:00")
    plan = f" Plan on file: {_sentence(step)}" if step else ""

    if lp["kind"] == "waiting_on" and who:
        status, info = mail(who, opened)
        if status == "found":
            facts.update({"mail from": info["sender"], "mail subject": info["subject"], "mail date": info["date"]})
            draft = (f"Mail from {who} is in your inbox ({info['date']}): \"{info['subject']}\". "
                     f"That looks like the thread \"{title}\". Is that settled, or still open?")
            return Candidate(f"loop:{lid}", "found", draft, facts, True, lid)
        if status != "found" and PASSIVE.search(step):
            return None                                 # nothing arrived and nothing is planned: silence
        if status == "could_not_check" and not lp.get("due"):
            # Shadow report, 2026-11-16: "Couldn't check your inbox for this one since there's
            # no name or address to search for…" about a wait with no date. A message whose
            # only content is that nothing could be checked is not worth sending.
            return None
        if status == "checked_none":
            facts["inbox check"] = f"no mail from {who} since {day(lp['opened_at'])}, checked {clock(n)}"
            draft = (f"I checked your inbox for mail from {who} since {day(lp['opened_at'])}: nothing as of "
                     f"{clock(n)}. This is \"{title}\".{plan} Has anything come in another way?")
            return Candidate(f"loop:{lid}", "checked_none", draft, facts, True, lid)
        facts["inbox check"] = f"not done ({info['reason']})"
        draft = (f"\"{title}\" was due for another look today. I couldn't check your inbox for this one "
                 f"({info['reason']}).{plan} Where does it stand?")
        return Candidate(f"loop:{lid}", "could_not_check", draft, facts, True, lid)

    lead = {"my_move": "is yours to do", "conditional_plan": "has reached its check date"}.get(
        lp["kind"], "was due for another look today")
    due_s = f" (due {facts['due']})" if lp.get("due") else ""
    draft = f"\"{title}\" {lead}{due_s}.{plan} Done, or still open?"
    return Candidate(f"loop:{lid}", "fact", draft, facts, True, lid)


# ----------------------------------------------------------------- candidates: health
def health_candidates(n: datetime.datetime, E=None) -> list[Candidate]:
    """Health gaps past their time of day, most important first. `E` is coach_engine
    (injectable for tests). A blank field is UNKNOWN, never zero: these only ever say
    'nothing logged', never a number that was not recorded."""
    if E is None:
        import coach_engine as E  # noqa: PLC0415,N811
    today, t = n.date().isoformat(), n.time()
    meals = E.meals_logged(today)
    fm = E.daily_fm(today)
    stamp = f"{day(n)}, {clock(n)}"
    out: list[Candidate] = []

    # sessions owed this week (target from the Training Plan frontmatter)
    planned = int(E.planned_sessions_per_week())
    monday = n.date() - datetime.timedelta(days=n.weekday())
    done = sum(1 for i in range(n.weekday() + 1)
               if (E.WORKOUTS / f"{(monday + datetime.timedelta(days=i)).isoformat()}.md").exists())
    lifted_today = (E.WORKOUTS / f"{today}.md").exists()
    days_left = 7 - n.weekday()                               # today included
    owed = planned - done
    if t >= LIFT_FROM and not lifted_today and owed > 0 and owed >= days_left - 1:
        s = "session" if done == 1 else "sessions"
        out.append(Candidate("lift", "fact",
                             f"{done} of {planned} {s} logged this week and {days_left} "
                             f"{'day' if days_left == 1 else 'days'} left, today included. Training today?",
                             {"sessions logged this week": done, "weekly target": planned,
                              "days left this week including today": days_left, "now": stamp}))

    nothing = "" if meals else " Nothing is logged for today yet."
    if LUNCH_WINDOW[0] <= t < LUNCH_WINDOW[1] and "lunch" not in meals:
        out.append(Candidate("lunch", "fact",
                             f"No lunch logged today as of {clock(n)}.{nothing} What did you have?",
                             {"meals logged today": ", ".join(sorted(meals)) or "none", "now": stamp}))
    if DINNER_WINDOW[0] <= t < DINNER_WINDOW[1] and "dinner" not in meals:
        out.append(Candidate("dinner", "fact",
                             f"No dinner logged today as of {clock(n)}.{nothing} What did you have?",
                             {"meals logged today": ", ".join(sorted(meals)) or "none", "now": stamp}))
    if VITAMIN_WINDOW[0] <= t < VITAMIN_WINDOW[1] and str(fm.get("vitamins_taken", "")).strip().lower() != "true":
        out.append(Candidate("vitamins", "fact",
                             f"No vitamins logged today as of {clock(n)}. Taken them?",
                             {"vitamins logged today": "no", "now": stamp}))
    return out


# ----------------------------------------------------------------- candidates: new mail
# The morning read (email_triage, 06:40) is the only general look at his inbox. Twice more
# a day the check-in looks at what arrived since: one small, spend-guarded model call picks
# AT MOST ONE message that needs him (a real person waiting, or a real deadline), and code
# checks its one-line summary against the mail itself before anything is said.
INBOX_HOURS = (13, 17)
INBOX_MAX_MAILS = 25
INBOX_BODY_CHARS = 500
INBOX_SYS = (
    "You screen new email for one person. From the numbered messages, pick AT MOST ONE that he "
    "needs to act on today or tomorrow: a real person waiting for his reply, or a real deadline, "
    "appointment or document request addressed to him. Ignore newsletters, receipts, automated "
    "confirmations, marketing, social notifications and anything merely informative. Most batches "
    "contain nothing: say so. Return ONLY JSON: {\"pick\": null} or {\"pick\": <number>, "
    "\"summary\": \"<one plain sentence, at most 160 characters, using only words, names, dates and "
    "numbers that appear in that message; no advice>\"}. Write the summary TO him: say 'you' and "
    "'your', never his name. Say what the message actually tells him (what is asked, what was "
    "decided, by when), not merely that an update was sent.")
HIS_NAME = re.compile(r"\bsparsh\b", re.I)


def inbox_state_path(shadow: bool) -> Path:
    return outbox.base_dir() / ("inbox_state.shadow.json" if shadow else "inbox_state.json")


def _sender_name(frm: str) -> str:
    name = re.sub(r"<[^>]*>", "", frm or "").strip().strip('"').strip()
    return name or (frm or "").strip("<> ")


def _mail_when(raw: str):
    try:
        from email.utils import parsedate_to_datetime  # noqa: PLC0415
        d = parsedate_to_datetime(raw)
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def drop_answered(mails: list[dict], sent: list[dict], when) -> tuple[list[dict], int]:
    """Leave out mail he has already answered: a message of his own in the SAME Gmail thread,
    dated after it. (Known gap since the first shadow run: the midday read raised mail he had
    replied to an hour earlier.) A message with no thread id or an unreadable date is kept:
    unknown is never read as answered."""
    latest: dict[str, datetime.datetime] = {}
    for sm in sent or []:
        t, d = sm.get("thread") or "", when(sm.get("date") or "")
        if t and d and (t not in latest or d > latest[t]):
            latest[t] = d
    keep = []
    for m in mails:
        t, d = m.get("thread") or "", when(m.get("date") or "")
        if t and d and t in latest and latest[t] > d:
            continue
        keep.append(m)
    return keep, len(mails) - len(keep)


def inbox_candidate(n: datetime.datetime, *, shadow: bool, call=None, fetch=None, triage_seen=None,
                    persist: bool = True, sent=None):
    """A Candidate for the one new message that needs him, or None. Runs once per
    INBOX_HOURS hour per day; every message is looked at once (its id is remembered).
    Read-only on Gmail. Never raises: a failed fetch or call is logged and means None."""
    if n.hour not in INBOX_HOURS:
        return None
    live_fetch = fetch is None                      # tests inject fetch; they inject `sent` too or get none
    sp = inbox_state_path(shadow)
    try:
        st = json.loads(sp.read_text())
    except Exception:  # noqa: BLE001
        st = {"seen": [], "ran": ""}
    slot = f"{n.date().isoformat()}T{n.hour:02d}"
    if st.get("ran") == slot:
        return None
    try:
        if fetch is None:
            import email_triage as ET  # noqa: PLC0415  (functions only; main() is never called)
            fetch = lambda seen: ET.fetch_candidates(1, seen, cap=INBOX_MAX_MAILS * 3)   # noqa: E731
            if triage_seen is None:                 # what the morning read already reported
                try:
                    triage_seen = json.loads(ET.STATE.read_text()).get("seen", [])
                except Exception:  # noqa: BLE001
                    triage_seen = []
        seen = set(st.get("seen", [])) | set(triage_seen or [])
        mails = fetch(seen)
    except Exception as e:  # noqa: BLE001
        log(f"inbox: fetch failed: {type(e).__name__}: {e}")
        return None
    try:
        if sent is None and live_fetch:
            import email_triage as ET2  # noqa: PLC0415
            sent = lambda: ET2.fetch_sent(2)[0]   # noqa: E731
        mails, answered = drop_answered(mails, sent() if sent else [], _mail_when)
        if answered:
            log(f"inbox: {answered} message(s) left out, already answered in the same thread")
    except Exception as e:  # noqa: BLE001
        log(f"inbox: sent-mail check failed ({type(e).__name__}: {e}); nothing left out")
    if len(mails) > INBOX_MAX_MAILS:
        log(f"inbox: CAP {len(mails)} new messages, looking at the newest {INBOX_MAX_MAILS}; the rest wait for the next read")
    batch = mails[:INBOX_MAX_MAILS]

    def remember():
        if not persist:                                 # --dry-run writes nothing
            return
        keep = (st.get("seen", []) + [m["id"] for m in batch])[-3000:]
        outbox.base_dir().mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({"seen": keep, "ran": slot}))

    if not batch:
        remember()
        return None
    call = call or (lambda s, u: llm.call(s, u, purpose="inbox", model=llm.HAIKU, max_tokens=250))
    listing = "\n\n".join(f"{i}. From: {m['from']}\nSubject: {m['subject']}\nDate: {m['date']}\n"
                           f"{(m.get('body') or '')[:INBOX_BODY_CHARS]}" for i, m in enumerate(batch, 1))
    reply = call(INBOX_SYS, listing)
    if not reply:
        log("inbox: no model reply; these messages will be looked at again")
        return None                                   # not remembered: retried at the next slot
    remember()
    try:
        obj = json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
        pick = obj.get("pick")
        if pick is None:
            return None
        m = batch[int(pick) - 1]
        summary = re.sub(r"\s+", " ", str(obj.get("summary") or "")).strip()
    except Exception as e:  # noqa: BLE001
        log(f"inbox: unusable reply ({type(e).__name__}); nothing raised")
        return None
    mail_text = f"{m['from']}\n{m['subject']}\n{m['date']}\n{m.get('body') or ''}"
    who = _sender_name(m["from"])
    # status "found": the mail exists, so words like "signed" or "confirmed" may describe it
    ok, why = ground.validate(summary, mail_text, "found") if summary else (False, ["empty"])
    if ok and HIS_NAME.search(summary):
        ok, why = False, ["talks about him in the third person"]
    if not ok:                                        # fall back to what the mail literally says
        log(f"inbox: summary rejected ({'; '.join(why)[:160]}); using the subject line")
        summary = ""
    body = f" {_sentence(summary)}" if summary else ""
    draft = f"New mail from {who}: \"{m['subject'][:120]}\".{body} Want me to keep track of it?"
    return Candidate(f"mail:{m['id']}", "found", draft,
                     {"from": m["from"], "subject": m["subject"], "date": m["date"]}, True, None, polish=False)


# ----------------------------------------------------------------- calendar context for the gym question
def evening_events(n: datetime.datetime, fetch=None) -> list[str]:
    """Today's remaining timed calendar events as 'Title at 6:30 PM'. [] on any failure:
    this only adds context to a question, it is never the reason to send one."""
    try:
        if fetch is None:
            import daily_prefill as DP  # noqa: PLC0415
            fetch = DP.fetch_events
        out = []
        for ev in fetch(n.date()) or []:
            if (ev.get("status") or "").lower() == "cancelled":
                continue
            try:
                dt = datetime.datetime.fromisoformat(str(ev.get("start") or ""))
            except ValueError:
                continue
            if dt.tzinfo is None:
                continue                                # all-day
            dt = dt.astimezone(TZ)
            if dt.date() == n.date() and dt > n:
                title = re.sub(r"\s+", " ", str(ev.get("summary") or "")).strip()
                if title:
                    out.append(f"{title} at {clock(dt)}")
        return out[:3]
    except Exception as e:  # noqa: BLE001
        log(f"calendar context failed: {type(e).__name__}: {e}")
        return []


# ----------------------------------------------------------------- compose
POLISH_SYS = (
    "You are Hermes, his personal assistant, texting him on Telegram. Rewrite the DRAFT so it "
    "reads like a person wrote it: plain, brief, warm, no emoji, no markdown, no greeting. "
    "Keep every fact, number, name, date and time exactly as given; add none, and do not add "
    "advice. Keep the meaning of what was checked exactly: if the draft says nothing was found "
    "or that something could not be checked, say so just as plainly and do not suggest anything "
    "arrived. You cannot send, book, schedule or change anything for him: never offer to. The "
    "only things you may offer are to look again later or to close the thread. At most three "
    "sentences and at most one question. Return only the message.")


def compose(c: Candidate, use_llm: bool = True, call=None) -> tuple[str, str]:
    """(text, how). `how` is 'model', or why the code-written draft was used instead."""
    if not c.polish:
        return c.draft, "draft (already checked)"
    if not use_llm:
        return c.draft, "draft (--no-llm)"
    call = call or (lambda s, u: llm.call(s, u, purpose="checkin", model=llm.HAIKU, max_tokens=300))
    reply = call(POLISH_SYS, f"FACTS\n{c.facts_text()}\n\nDRAFT\n{c.draft}")
    if not reply:
        return c.draft, "draft (no model reply)"
    text = reply.strip().strip('"')
    ok, why = ground.validate(text, c.facts_text() + "\n" + c.draft, c.status)
    if not ok:
        log(f"model text rejected ({'; '.join(why)[:200]}); using the draft")
        return c.draft, "draft (model text failed grounding: " + "; ".join(why)[:160] + ")"
    return text, "model"


# ----------------------------------------------------------------- shadow bookkeeping
def shadow_rows() -> list[dict]:
    p = shadow_path()
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue                    # the shadow file is a report, not a control
    return out


def shadow_allow(c: Candidate, n: datetime.datetime) -> tuple[bool, str]:
    """The same policy as live, plus what shadow itself 'sent' today (topics + cap)."""
    ok, why = outbox.allow("nudge", c.topic, loop_id=c.loop_id, expects_reply=c.expects_reply, at=n)
    if not ok:
        return ok, why
    today = [r for r in shadow_rows() if r.get("at", "")[:10] == n.date().isoformat()]
    if len(today) >= SHADOW_CAP:
        return False, f"shadow cap {SHADOW_CAP} reached"
    if any(r.get("topic") == c.topic for r in today):
        return False, f"topic {c.topic!r} already raised today (shadow)"
    return True, "ok"


def write_report(n: datetime.datetime) -> bool:
    cutoff = (n - datetime.timedelta(days=REPORT_DAYS)).isoformat()
    rows = [r for r in shadow_rows() if r.get("at", "") >= cutoff]
    out = ["---", "type: tracker", "category: assistant", "status: active",
           f"last_updated: {n.date().isoformat()}", "tags: [hermes, shadow]", "---", "",
           "# Assistant Shadow Report", "",
           "> **What the check-in loop WOULD have sent you.** Nothing below was sent. Machine-written "
           "on the VPS (`scripts/assistant/checkin.py`); do not edit. Tell Claude which of these you "
           "would not have wanted.", ""]
    if not rows:
        out.append("_Nothing would have been sent in the last few days._")
    last_day = None
    for r in rows:
        d = r["at"][:10]
        if d != last_day:
            out += [f"## {day(r['at'])}", ""]
            last_day = d
        t = datetime.datetime.fromisoformat(r["at"])
        out += [f"**{clock(t)}** · `{r['topic']}` · {r['status'].replace('_', ' ')} · wording: {r['how']}", "",
                "> " + r["text"].replace("\n", "\n> "), ""]
    new = "\n".join(out).rstrip() + "\n"
    path = loops.vault() / REPORT_REL
    strip = lambda s: re.sub(r"(?m)^last_updated: .*$", "", s)   # noqa: E731
    if path.exists() and strip(path.read_text(encoding="utf-8")) == strip(new):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)
    return True


# ----------------------------------------------------------------- the tick
def tick(n: datetime.datetime, *, shadow: bool, dry_run: bool = False, use_llm: bool = True,
         call=None, mail=mail_from, engine=None, send=None, inbox=None, calendar=None) -> dict:
    """One check-in. Returns what happened (for the log and the tests)."""
    res = {"at": n.isoformat(timespec="minutes"), "mode": "shadow" if shadow else "live", "candidates": 0,
           "sent": None, "why": ""}
    allow = (lambda c: shadow_allow(c, n)) if shadow else (
        lambda c: outbox.allow("nudge", c.topic, loop_id=c.loop_id, expects_reply=c.expects_reply, at=n))

    # Cheapest refusal first: when NO nudge could go out right now (quiet hours, the daily
    # cap, an unreadable ledger), build nothing at all: no mail search, no model call.
    ok, why = outbox.allow("nudge", "__probe__", at=n)
    if not ok and (why.startswith(("quiet", "daily cap", "ledger unreadable")) or "in a row" in why):
        res["why"] = why
        return res

    data = loops.load()
    chosen, reasons, mail_checks = None, [], 0
    for lid, lp in loops.due(data, at=n):
        res["candidates"] += 1
        probe = Candidate(f"loop:{lid}", "fact", "", loop_id=lid)
        ok, why = allow(probe)                      # ask the budget BEFORE spending a mail search
        if not ok:
            reasons.append(f"loop:{lid}: {why}")
            continue
        if lp["kind"] == "waiting_on" and searchable(lp.get("who") or ""):
            if mail_checks >= MAX_MAIL_CHECKS:
                reasons.append(f"loop:{lid}: mail-check cap ({MAX_MAIL_CHECKS}) reached this tick")
                continue
            mail_checks += 1
        chosen = loop_candidate(lid, lp, n, mail)
        if chosen is None:
            reasons.append(f"loop:{lid}: passive wait, nothing arrived")
            if not shadow and not dry_run:              # look again in 3 days (bookkeeping, no message)
                loops.update(data, lid, check_after=(n.date() + datetime.timedelta(days=3)).isoformat(), at=n)
                loops.save(data)
            continue
        break
    if chosen is None:
        c = inbox(n) if inbox else None
        if c is not None:
            res["candidates"] += 1
            ok, why = allow(c)
            if ok:
                chosen = c
            else:
                reasons.append(f"{c.topic}: {why}")
    if chosen is None:
        for c in health_candidates(n, engine):
            res["candidates"] += 1
            ok, why = allow(c)
            if ok:
                chosen = c
                if c.topic == "lift":                   # plan the session around what is left today
                    later = calendar(n) if calendar else []
                    if later:
                        c.facts["still on the calendar today"] = "; ".join(later)
                        c.draft = c.draft.replace(" Training today?", f" Still on your calendar today: {'; '.join(later)}. Training today?")
                break
            reasons.append(f"{c.topic}: {why}")
    if chosen is None:
        res["why"] = "; ".join(reasons)[:400] or "nothing worth saying"
        return res                                  # no model call was made

    text, how = compose(chosen, use_llm=use_llm, call=call)
    res.update(topic=chosen.topic, status=chosen.status, text=text, how=how)
    if dry_run:
        res["sent"] = "dry-run"
        return res
    if shadow:
        outbox.base_dir().mkdir(parents=True, exist_ok=True)
        with open(shadow_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": n.isoformat(timespec="minutes"), "topic": chosen.topic,
                                 "status": chosen.status, "how": how, "text": text,
                                 "draft": chosen.draft}, ensure_ascii=False) + "\n")
        write_report(n)
        res["sent"] = "shadow"
        return res

    if send is None:
        import coach_engine as E  # noqa: PLC0415,N811
        send = E.send_message
    if not send(text):
        res["why"] = "send failed; nothing recorded"       # a lost message is not recorded as sent
        return res
    outbox.record("nudge", chosen.topic, text, loop_id=chosen.loop_id, expects_reply=chosen.expects_reply, at=n)
    if chosen.loop_id:
        data = loops.load()
        loops.note_ask(data, chosen.loop_id, at=n)
        loops.save(data)
        loops.write_view(data, at=n)
    res["sent"] = "live"
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="checkin")
    ap.add_argument("--shadow", action="store_true", help="force shadow even if LIVE is set")
    ap.add_argument("--dry-run", action="store_true", help="decide and compose, write nothing")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--now", default=None, help="pretend it is this local time (testing)")
    a = ap.parse_args(argv)
    n = datetime.datetime.fromisoformat(a.now).replace(tzinfo=TZ) if a.now else loops.now()
    shadow = a.shadow or not live_flag().exists()
    res = tick(n, shadow=shadow, dry_run=a.dry_run, use_llm=not a.no_llm,
               inbox=(None if a.no_llm else (lambda t: inbox_candidate(t, shadow=shadow, persist=not a.dry_run))),
               calendar=evening_events)
    log(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"FAILED: {type(e).__name__}: {e}")
        # A broken check-in must not be silent, and must not shout every hour either: one
        # line to stdout (= delivered by the cron) the first time it fails each day.
        try:
            mark = outbox.base_dir() / "checkin_failed_day"
            today = loops.now().date().isoformat()
            if not mark.exists() or mark.read_text().strip() != today:
                outbox.base_dir().mkdir(parents=True, exist_ok=True)
                mark.write_text(today)
                print(f"⚠️ Hermes check-in is failing: {type(e).__name__}: {e} (further failures today are only logged)")
        except Exception:  # noqa: BLE001
            print(f"⚠️ Hermes check-in is failing: {type(e).__name__}: {e}")
        sys.exit(1)
