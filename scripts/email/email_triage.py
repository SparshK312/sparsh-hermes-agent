#!/usr/bin/env python3
"""
email_triage.py — the morning inbox read.

WHY THIS EXISTS (2026-08-28): two application outcomes sat unrecorded for days because
nothing in the system reads email. Roblox rejected him Aug 27 and the board still said
`OA`; Tesla rejected him Aug 13 and NOBODY NOTICED FOR TWO WEEKS. Both misses were
mechanical, not careless — outcomes arrive by email, and every status was only as fresh
as the last time he manually typed one in. This closes that loop.

SHAPE: hybrid, deliberately. The mechanical half is a script (fetch since last run,
drop known noise, dedupe by message id) because none of that needs judgment and an LLM
doing it would be slower, costlier and non-deterministic about what it had already seen.
The judgment half is ONE model call that gets the candidate mail AND his live
application list, so it can say "Roblox rejected you, that was your only live OA" rather
than "you have an email from Roblox."

  fetch -> prefilter -> [ single LLM call ] -> triage.json + triage.md -> daily note

SAFETY: read-only. It never sends, replies, archives or labels. It is wrapped so that a
bad morning costs an empty section in the daily note, never a crashed cron.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERMES = Path.home() / ".hermes"
ENV_FILE = HERMES / ".env"
STATE = HERMES / "health" / "email_triage_state.json"
OUT_JSON = HERMES / "health" / "email_triage.json"
OUT_MD = HERMES / "health" / "email_triage.md"
GOOGLE_API = HERMES / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py"
VENV_PY = HERMES / "hermes-agent" / "venv" / "bin" / "python"

BOARD_SHEET_ID = "1Kkle7QoKsBMXihoslWjIoMDqKFznwxxA4Y_OgiqJpWI"
VAULT = Path(os.environ.get("HERMES_VAULT", "/home/hermes/vault"))
INBOUND_LOG = VAULT / "06 - Internships" / "Job Search" / "Inbound Leads.md"
MODEL = "claude-sonnet-4-6"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
PROMPT_VERSION = "triage-v1"

# 2026-08-28: measured his actual volume at ~400 messages per 15 days (~27/day), so a
# 2-day window is ~54 — which was UNDER the old cap of 60 by six messages. A busy
# weekend would have silently truncated the fetch and dropped mail on the floor with no
# indication. Raised, and the cap is now reported when hit rather than passed over.
MAX_FETCH = 150          # hard cost guard: candidates considered per run
MAX_ENRICH = 25          # full bodies fetched per run (see enrich_bodies)
BODY_CHARS = 1100        # enough for a rejection/interview email's actual content
SEEN_CAP = 4000          # ids retained in state


# ── noise prefilter ───────────────────────────────────────────────────────────
# Cheap, mechanical, and conservative. Anything dropped here is never seen by the
# model, so the rule is: only drop what is unambiguously machine chatter. When in
# doubt it goes to the model — a wasted 300 tokens is cheaper than a missed rejection.
NOISE_SENDERS = re.compile(
    r"(noreply-accounts@google\.com|no-reply@accounts\.google\.com|googleplay-noreply@"
    r"|googledevelopers-noreply@|payments-noreply@google\.com|aws-marketing-email-replies@"
    r"|no_reply@email\.apple\.com|notifications@m\.wealthsimple\.com|@substack\.com"
    r"|@medium\.com|@linkedin\.com|@github\.com|calendar-notification@)", re.I)
NOISE_SUBJECTS = re.compile(
    r"^(your receipt|receipt from|invoice|security alert|you shared some google account"
    r"|new sign-in|verify your email|password reset|welcome to|your .* order)", re.I)

# Gmail's search snippet is ~200 chars, and a rejection routinely puts the actual verdict
# past that: the real Roblox mail cut at "we've made the" — one word before "decision to
# not move forward". Classifying that correctly from the subject line is luck, and a
# missed rejection is the exact failure this whole job exists to prevent. So for anything
# that smells like recruiting we pay one extra API call and read the real body.
RECRUITING = re.compile(
    r"(applicat|interview|assessment|recruit|candidat|offer|hiring|internship|talent"
    r"|greenhouse|lever\.co|ashby|workday|smartrecruiters|icims|jobvite|myworkday"
    r"|next steps|your application|move forward|position)", re.I)


def env(key: str) -> str | None:
    v = os.environ.get(key)
    if v:
        return v
    try:
        for ln in ENV_FILE.read_text().splitlines():
            if ln.startswith(f"{key}="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return None


def log(msg: str) -> None:
    print(f"[triage] {msg}", file=sys.stderr)


def _gapi(*args: str, timeout: int = 90) -> str:
    py = str(VENV_PY) if VENV_PY.exists() else sys.executable
    proc = subprocess.run([py, str(GOOGLE_API), *args], capture_output=True,
                          text=True, timeout=timeout, input="")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "has not been used" in err or "SERVICE_DISABLED" in err:
            raise RuntimeError(
                "Google API not enabled in the GCP project. Enable Gmail API (and Sheets "
                "API for board context) at console.cloud.google.com, then re-run.")
        raise RuntimeError(f"google_api.py {' '.join(args)} failed: {err[:300]}")
    return proc.stdout


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:  # noqa: BLE001
        return {"seen": [], "last_run": None}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state["seen"] = state["seen"][-SEEN_CAP:]
    STATE.write_text(json.dumps(state, indent=1))


# ── fetch ─────────────────────────────────────────────────────────────────────
def fetch_candidates(days: int, seen: set[str], query: str | None = None,
                     cap: int | None = None) -> list[dict]:
    """Recent mail, minus obvious machine noise, minus anything already reported."""
    q = query or f"newer_than:{days}d -in:sent -in:draft"
    raw = _gapi("gmail", "search", q, "--max", str(cap or MAX_FETCH), timeout=180)
    try:
        msgs = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"gmail search returned non-JSON: {raw[:250]}")
    if isinstance(msgs, dict):
        msgs = msgs.get("messages") or msgs.get("results") or []

    out, skipped = [], 0
    for m in msgs:
        mid = str(m.get("id") or m.get("messageId") or "")
        sender = str(m.get("from") or m.get("sender") or "")
        subject = str(m.get("subject") or "")
        if not mid or mid in seen:
            continue
        if NOISE_SENDERS.search(sender) or NOISE_SUBJECTS.search(subject.strip()):
            skipped += 1
            seen.add(mid)          # noise is decided once, never re-litigated
            continue
        body = str(m.get("snippet") or m.get("body") or m.get("plaintext_body") or "")
        out.append({"id": mid, "from": sender, "subject": subject,
                    "date": str(m.get("date") or ""), "body": body[:BODY_CHARS],
                    "full": False})
    limit = cap or MAX_FETCH
    if len(msgs) >= limit:
        log(f"⚠️  AT CAP ({limit}) — the window returned at least as many messages as "
            f"we asked for, so older mail in it was NOT seen. Raise --max.")
    log(f"{len(msgs)} fetched, {skipped} filtered as noise, {len(out)} to classify")
    return out


def enrich_bodies(cands: list[dict]) -> None:
    """Replace the snippet with the real body for recruiting-shaped mail only.

    Targeted rather than blanket: fetching all ~50 candidates would spend most of the
    calls and tokens on newsletters. This spends them where being wrong is expensive.
    """
    targets = [c for c in cands
               if RECRUITING.search(c["subject"]) or RECRUITING.search(c["from"])][:MAX_ENRICH]
    ok = 0
    for c in targets:
        try:
            raw = _gapi("gmail", "get", c["id"], timeout=45)
            msg = json.loads(raw)
            if isinstance(msg, list) and msg:
                msg = msg[0]
            text = (msg.get("plaintext_body") or msg.get("body")
                    or msg.get("text") or msg.get("snippet") or "")
            text = re.sub(r"https?://\S{40,}", "[link]", str(text))   # strip tracking URLs
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if text:
                c["body"] = text[:BODY_CHARS]
                c["full"] = True
                ok += 1
        except Exception as e:  # noqa: BLE001 - snippet is a fine fallback
            log(f"body fetch failed for {c['id']}: {type(e).__name__}")
    log(f"enriched {ok}/{len(targets)} recruiting-shaped emails with full bodies")


def live_applications() -> list[dict]:
    """His tracked applications, read off the live board. Best-effort: without this the
    model still triages, it just can't say WHICH application an email belongs to."""
    try:
        raw = _gapi("sheets", "get", BOARD_SHEET_ID, "My Applications!A1:K60")
        data = json.loads(raw)
        rows = data.get("values", data) if isinstance(data, dict) else data
        if not rows:
            return []
        hdr = [str(c).strip() for c in rows[0]]
        idx = {h.lower(): i for i, h in enumerate(hdr)}
        apps = []
        for r in rows[1:]:
            g = lambda k: (r[idx[k]] if k in idx and idx[k] < len(r) else "")  # noqa: E731
            if g("company"):
                apps.append({"company": g("company"), "role": g("role"),
                             "status": g("status"), "applied": g("applied")})
        return apps
    except Exception as e:  # noqa: BLE001
        log(f"board context unavailable ({type(e).__name__}) — triaging without it")
        return []


# ── the model call ────────────────────────────────────────────────────────────
SYSTEM = """You triage Sparsh Kochhar's inbox each morning for his personal assistant.

He is a University of Toronto ECE student interning at Shopify, applying to software and
ML internships for Winter 2027 and Summer 2027. He is a US permanent resident.

Your job is to find what he ACTUALLY needs to know and say it plainly. You are writing
for someone who will read this in thirty seconds before his day starts.

VOICE: this goes straight into his own daily note, so address him as "you". Write
"Roblox rejected your application", never "his application" and never "Sparsh's".

Return ONLY a JSON object, no prose and no code fence:

{"items": [
  {"id": "<message id, verbatim>",
   "category": "application-update" | "inbound" | "needs-reply" | "time-sensitive" | "worth-knowing",
   "company": "<company, or empty>",
   "matched_application": "<the company+role from his tracked list this concerns, or empty>",
   "status_change": "Rejected" | "OA" | "Interview" | "Offer" | "Ghosted" | "",
   "summary": "<one sentence, plain, factual, no hype>",
   "action": "<what he should do, or empty if nothing>",
   "urgency": "high" | "normal" | "low"}
]}

RULES
- Omit anything that is not genuinely useful. An empty list is a correct and common answer.
- "application-update": any outcome on a job application — rejection, assessment invite,
  interview request, offer. Set status_change. If it maps to one of his tracked
  applications, put that in matched_application VERBATIM as given.
- "inbound": someone approached HIM about a role he never applied to — a founder, an
  agency recruiter, a YC Work-at-a-Startup message. Put the person's name and company in
  `summary`. Use this even for small or off-thesis companies: it is catalogued, not
  acted on. ⚠️ A reply to an application HE submitted is NOT inbound — that is
  "application-update" or "needs-reply".
- "needs-reply": a real human wrote to him and is waiting. Never a no-reply address,
  never a newsletter, never an automated confirmation.
- "time-sensitive": a real deadline or expiry in the next few days.
- "worth-knowing": genuinely notable, rare. Do not pad with newsletters.
- urgency "high" ONLY for something that breaks if ignored today.
- Be accurate about rejections. "We've decided not to move forward" is a rejection. A
  confirmation that an application was received is NOT an update — skip it.
- Never invent a company or an application that is not in the email.
- Write like a person, not a press release. No emoji, no exclamation marks.
- If two emails contradict each other, say so rather than reporting both flatly."""


def classify(cands: list[dict], apps: list[dict]) -> dict:
    key = env("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    app_lines = "\n".join(
        f"- {a['company']} — {a['role']} (status: {a['status']}, applied {a['applied']})"
        for a in apps) or "(board unavailable)"
    mail = "\n\n".join(
        f"[id: {c['id']}]\nFrom: {c['from']}\nDate: {c['date']}\nSubject: {c['subject']}\n{c['body']}"
        for c in cands)
    user = (f"His tracked applications:\n{app_lines}\n\n"
            f"=== {len(cands)} emails from the last day ===\n\n{mail}")

    body = {
        "model": MODEL, "max_tokens": 4000, "temperature": 0,
        "system": [{"type": "text", "text": SYSTEM,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        ANTHROPIC_URL, data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"anthropic {e.code}: {e.read().decode()[:300]}") from e

    # A response truncated at max_tokens still returns HTTP 200 with partial JSON.
    if raw.get("stop_reason") == "max_tokens":
        raise RuntimeError("model response truncated at max_tokens")
    text = "".join(b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text")
    usage = raw.get("usage", {})
    cost = (usage.get("input_tokens", 0) * 3 + usage.get("output_tokens", 0) * 15) / 1e6
    log(f"classified {len(cands)} emails · ~${cost:.4f}")

    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise RuntimeError(f"model returned non-JSON: {text[:250]}")
        return json.loads(m.group(0))


# ── rendering ─────────────────────────────────────────────────────────────────
ORDER = {"high": 0, "normal": 1, "low": 2}
LABEL = {"application-update": "Application", "needs-reply": "Needs a reply",
         "inbound": "Inbound", "time-sensitive": "Time-sensitive",
         "worth-knowing": "Worth knowing"}


def _iso_date(raw: str) -> str:
    """RFC-2822 mail date -> YYYY-MM-DD so appended rows match the hand-written ones."""
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        m = re.search(r"\d{4}-\d{2}-\d{2}", raw or "")
        return m.group(0) if m else (raw or "")[:16]


def append_inbound(items: list[dict], cands: list[dict]) -> int:
    """Append inbound approaches to the vault tracker, deduped by Gmail message id.

    APPEND-ONLY and idempotent: the id is parked in a trailing HTML comment (invisible
    in Obsidian) and re-checked on every run, so re-processing the same mail cannot
    duplicate a row and his hand edits to the table are never touched.
    """
    rows = [i for i in items if i.get("category") == "inbound"]
    if not rows or not INBOUND_LOG.exists():
        return 0
    try:
        text = INBOUND_LOG.read_text()
    except Exception as e:  # noqa: BLE001
        log(f"inbound log unreadable: {type(e).__name__}")
        return 0
    by_id = {c["id"]: c for c in cands}
    new = []
    for it in rows:
        mid = it.get("id", "")
        if not mid or f"<!--id:{mid}-->" in text:
            continue
        c = by_id.get(mid, {})
        date = _iso_date(c.get("date") or "")
        who = re.sub(r"\s*<[^>]*>", "", c.get("from", "")).strip().strip('"') or "?"
        summary = (it.get("summary") or "").replace("|", "/").strip()
        new.append(f"| {date} | {who} | {it.get('company','')} | | {summary} "
                   f"<!--id:{mid}--> |")
    if not new:
        return 0
    # Anchor on the HEADING PREFIX, not its full text. v1 matched the exact string
    # "## Not inbound, but unanswered"; the heading was later reworded to "...but open"
    # and the match SILENTLY failed, dumping rows at the end of the file instead of into
    # the table. A prefix match survives rewording; falling back to end-of-file is the
    # last resort, not the second one.
    block = "\n".join(new)
    m = re.search(r"^## Not inbound.*$", text, re.M)
    if m:
        text = text[:m.start()] + block + "\n\n" + text[m.start():]
    else:
        log("inbound table anchor not found — appending at end of file")
        text = text.rstrip() + "\n" + block + "\n"
    text = re.sub(r"^last_updated: .*$",
                  f"last_updated: {datetime.now().strftime('%Y-%m-%d')}",
                  text, count=1, flags=re.M)
    INBOUND_LOG.write_text(text)
    log(f"appended {len(new)} inbound lead(s) to {INBOUND_LOG.name}")
    return len(new)


def render(items: list[dict]) -> str:
    if not items:
        return "## 📬 Inbox\n\nNothing needing attention.\n"
    items = sorted(items, key=lambda i: (ORDER.get(i.get("urgency", "normal"), 1),
                                         i.get("category", "")))
    out = ["## 📬 Inbox", ""]
    for it in items:
        tag = LABEL.get(it.get("category", ""), "Note")
        co = it.get("company") or ""
        head = f"**{tag}{' — ' + co if co else ''}**"
        if it.get("urgency") == "high":
            head = "🔴 " + head
        line = f"- {head} {it.get('summary','').strip()}"
        if it.get("status_change"):
            line += f" *(status: {it['status_change']})*"
        out.append(line)
        if it.get("action"):
            out.append(f"    - ▶️ {it['action'].strip()}")
        if it.get("matched_application"):
            out.append(f"    - board row: {it['matched_application']}")
    out.append("")
    return "\n".join(out)


def _arg(name: str, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main() -> int:
    global STATE, OUT_JSON, OUT_MD, MAX_ENRICH
    days = int(_arg("--days", 2))
    query = _arg("--query")            # backfill: an explicit Gmail query
    cap = int(_arg("--max", MAX_FETCH))
    # A backfill must not poison the daily dedup state, or the next morning would go
    # blind on everything the sweep happened to touch.
    if _arg("--state"):
        STATE = Path(_arg("--state"))
        OUT_JSON = STATE.with_suffix(".out.json")
        OUT_MD = STATE.with_suffix(".out.md")
        MAX_ENRICH = int(_arg("--enrich", 40))
    state = _load_state()
    seen = set(state.get("seen", []))
    try:
        cands = fetch_candidates(days, seen, query, cap)
        items = []
        if cands:
            enrich_bodies(cands)
            apps = live_applications()
            items = (classify(cands, apps) or {}).get("items", []) or []
            valid = {c["id"] for c in cands}
            items = [i for i in items if i.get("id") in valid]   # no invented ids
            for c in cands:
                seen.add(c["id"])
        try:
            append_inbound(items, cands)
        except Exception as e:  # noqa: BLE001 - never let the tracker break the brief
            log(f"inbound append failed: {type(e).__name__}: {e}")
        md = render(items)
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(
            {"generated_at": datetime.now(timezone.utc).isoformat(),
             "prompt_version": PROMPT_VERSION, "considered": len(cands),
             "items": items}, indent=1))
        OUT_MD.write_text(md)
        state["seen"] = sorted(seen)
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        _save_state(state)
        log(f"{len(items)} item(s) surfaced -> {OUT_MD}")
        print(md)
        return 0
    except Exception as e:  # noqa: BLE001
        # A bad morning must cost an empty section, never a crashed cron.
        log(f"FAILED: {type(e).__name__}: {e}")
        try:
            hint = ""
            if "not enabled" in str(e) or "SERVICE_DISABLED" in str(e):
                hint = ("\n\nGmail API is off in GCP project 1088921728942. Enable it at\n"
                        "https://console.cloud.google.com/apis/library/gmail.googleapis.com?project=1088921728942\n"
                        "(and sheets.googleapis.com for board matching). The token already has the scopes.")
            OUT_MD.write_text(f"## 📬 Inbox\n\n⚠️ Inbox scan failed: {type(e).__name__}. "
                              f"Nothing was read this morning.{hint}\n")
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
