#!/usr/bin/env python3
"""
email_audit.py — one-off exhaustive sweep of application-outcome mail.

NOT the daily triage. email_triage.py is deliberately conservative: it answers "what do
I need to know THIS MORNING", so it correctly stays quiet about an outcome that is
already recorded on the board. That is exactly wrong for the question "what did we
MISS", where already-known items are the control group.

So this extracts EVERY application event it can find over a long window, with no
usefulness filter at all, and prints them for diffing against the board.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import email_triage as T  # noqa: E402

SYSTEM = """You are auditing a job applicant's email history. Extract EVERY application
event you can find. This is an exhaustive inventory, NOT a curated briefing — do not
filter for importance, novelty or usefulness. Old and already-known events matter here.

Return ONLY JSON, no prose, no code fence:

{"events": [
  {"company": "<company>",
   "role": "<role as the email states it, or empty>",
   "date": "<YYYY-MM-DD from the email>",
   "outcome": "applied" | "assessment" | "interview" | "offer" | "rejected" | "withdrawn" | "inbound" | "other",
   "evidence": "<short verbatim quote that establishes the outcome>"}
]}

RULES
- One event per email that concerns a job application OR a real person recruiting him.
  Skip newsletters, job-alert digests, marketing, and automated "jobs you may like".
- "inbound" = someone approached HIM about a role he did not apply to (a founder, a
  recruiter, a YC Work-at-a-Startup message). Set outcome "inbound" and put who they
  are and the company in `evidence`. These matter: he gets cold approaches from small
  startups and they are easy to lose in the volume.
- "applied" = a received/confirmation email. "rejected" = any decline, however worded.
  "assessment" = an OA or test invite. "interview" = a request to schedule or attend one.
- Quote the actual decision language in `evidence`. Never paraphrase it there.
- Use the company the email is FROM, not a company merely mentioned in passing.
- If the same company appears more than once with different outcomes, emit each one."""


# Coverage matters more than elegance here. ONE mega-OR query looked thorough and was
# not: it returned 88 emails while the inbox holds 400+ in any 15-day window, i.e. it was
# SAMPLING. It also had the wrong YC domain (workatastartup.com; the real sender is
# workatastartup@ycombinator.com) so inbound founder outreach was invisible entirely.
# Separate targeted queries, unioned by message id, are both more complete and cheaper
# than one broad one — and each can be counted independently to prove it isn't capped.
QUERIES = [
    ("ats", '{from:greenhouse.io from:lever.co from:ashbyhq.com from:myworkday.com '
            'from:myworkdayjobs.com from:smartrecruiters.com from:icims.com '
            'from:jobvite.com from:workable.com from:breezy.hr from:rippling.com}'),
    ("outcome", '{"not move forward" "not moving forward" "other candidates" "we regret" '
                '"decided not to" "no longer under consideration" "unable to offer" '
                '"move forward with other" "not been selected"}'),
    ("progress", '{"thank you for applying" "we received your application" '
                 '"your application" "next steps" "schedule an interview" '
                 '"we would like to invite" "online assessment" "coding challenge"}'),
    ("yc_inbound", 'from:ycombinator.com {"sent you a message" "wants to" interested}'),
    ("cold_inbound", '-from:linkedin.com {"reaching out" "saw your profile" '
                     '"came across your" "would love to chat" "opportunity at" '
                     '"are you open to" "interested in speaking" "quick chat"}'),
    ("linkedin_dm", 'from:linkedin.com {InMail "sent you a message"}'),
]


def main() -> int:
    days = int(T._arg("--days", 150))
    per = int(T._arg("--per", 200))
    seen_ids, cands = set(), []
    for name, q in QUERIES:
        try:
            got = T.fetch_candidates(days, set(),
                                     f"newer_than:{days}d -in:sent -in:draft {q}", per)
        except Exception as e:  # noqa: BLE001
            T.log(f"query {name} FAILED: {type(e).__name__}: {e}")
            continue
        fresh = [c for c in got if c["id"] not in seen_ids]
        for c in fresh:
            seen_ids.add(c["id"])
        cands += fresh
        flag = "  <-- AT CAP, may be truncated" if len(got) >= per else ""
        T.log(f"query {name:<14} matched {len(got):>3}, {len(fresh):>3} new{flag}")
    T.log(f"UNION: {len(cands)} unique emails to inventory")
    if not cands:
        print(json.dumps({"events": []})); return 0
    T.MAX_ENRICH = int(T._arg("--enrich", 90))
    T.enrich_bodies(cands)

    events, CH = [], 20
    import urllib.request
    for i in range(0, len(cands), CH):
        chunk = cands[i:i + CH]
        mail = "\n\n".join(
            f"[id: {c['id']}]\nFrom: {c['from']}\nDate: {c['date']}\n"
            f"Subject: {c['subject']}\n{c['body']}" for c in chunk)
        body = {"model": T.MODEL, "max_tokens": 8000, "temperature": 0,
                "system": [{"type": "text", "text": SYSTEM,
                            "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": mail}]}
        req = urllib.request.Request(
            T.ANTHROPIC_URL, data=json.dumps(body).encode(),
            headers={"x-api-key": T.env("ANTHROPIC_API_KEY"),
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                raw = json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            T.log(f"chunk {i//CH + 1} FAILED: {type(e).__name__}"); continue
        if raw.get("stop_reason") == "max_tokens":
            T.log(f"chunk {i//CH + 1} TRUNCATED - results incomplete")
        txt = "".join(b.get("text", "") for b in raw.get("content", [])
                      if b.get("type") == "text")
        txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            try:
                events += json.loads(m.group(0)).get("events", [])
            except json.JSONDecodeError:
                T.log(f"chunk {i//CH + 1}: unparseable JSON")
        T.log(f"chunk {i//CH + 1}/{(len(cands)+CH-1)//CH}: {len(events)} events so far")

    events.sort(key=lambda e: (e.get("company", "").lower(), e.get("date", "")))
    print(json.dumps({"events": events, "emails_inventoried": len(cands)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
