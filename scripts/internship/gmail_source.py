#!/usr/bin/env python3
"""
gmail_source.py — read curated internship newsletters from Gmail as a watcher source.

InternInsider (hi@interninsider.me) emails ~40-50 hand-curated internships every
few days with DIRECT apply links — higher signal than the GitHub aggregators, and
already arriving in the inbox. This pulls those (and, best-effort, LinkedIn
job-alert emails) over IMAP and turns them into Posting objects that join the
scrape pool, deduped by canonical_id like every other source.

ACTIVATION — needs a Gmail app password (the OAuth token Hermes holds is
calendar-only and can't read mail). Set in ~/.hermes/.env:
    EMAIL_ADDRESS=you@gmail.com
    EMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx     # myaccount.google.com/apppasswords
    EMAIL_IMAP_HOST=imap.gmail.com             # optional; this is the default
Without these the source returns [] cleanly, so the watcher still runs on the
GitHub sources — nothing breaks if the credential is absent.

Stdlib only (imaplib, email, re).
"""
from __future__ import annotations

import email
import imaplib
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import internship_scraper as S  # noqa: E402 — Posting + canonical_id

IMAP_HOST_DEFAULT = "imap.gmail.com"
SINCE_DAYS = 12

# Newsletter senders -> parser key
SENDERS = {
    "hi@interninsider.me": "interninsider",
    "jobs-noreply@linkedin.com": "linkedin",
}

# InternInsider line:  * _[Role](url)_ @ **Company** – Location
RE_II = re.compile(
    r"_\[(?P<role>[^\]]+)\]\((?P<url>https?://[^)]+)\)_\s*@\s*"
    r"\*\*(?P<company>[^*]+)\*\*\s*[–—-]\s*(?P<loc>[^\n]+)"
)


def _age_days(dt: datetime) -> int:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (now - dt).days)


def _mk_posting(role: str, url: str, company: str, loc: str, source: str, edt: datetime):
    url = url.strip()
    return S.Posting(
        company=company.strip(),
        title=role.strip(),
        location=re.sub(r"\s+", " ", loc).strip().rstrip(" |"),
        url=url,
        posted_date=edt.strftime("%Y-%m-%d"),
        age_days=_age_days(edt),
        terms="",
        source=source,
        canonical_id=S.canonical_id(url),
        salary="",
    )


def parse_interninsider(text: str, edt: datetime) -> list:
    return [
        _mk_posting(m["role"], m["url"], m["company"], m["loc"], "interninsider", edt)
        for m in RE_II.finditer(text)
    ]


def parse_linkedin(text: str, edt: datetime) -> list:
    # LinkedIn text/plain alerts are sparse and use redirect URLs; left as a
    # conservative no-op so we never emit junk. InternInsider carries the value.
    return []


PARSERS = {"interninsider": parse_interninsider, "linkedin": parse_linkedin}


def _plain_text(msg) -> str:
    """Best text body for a MIME message: prefer text/plain, else strip text/html."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", "replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    from jd_fetch import _html_to_text
                    return _html_to_text(
                        payload.decode(part.get_content_charset() or "utf-8", "replace")
                    )
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode(msg.get_content_charset() or "utf-8", "replace") if payload else ""


def fetch_email_postings(env, since_days: int = SINCE_DAYS) -> tuple[list, list]:
    """Pull newsletter postings over IMAP. Returns (postings, failures).
    Empty + no failure if creds are absent (so the watcher degrades gracefully)."""
    user = env("EMAIL_ADDRESS")
    pw = env("EMAIL_APP_PASSWORD")
    if not user or not pw:
        return [], []  # not configured -> skip silently
    host = env("EMAIL_IMAP_HOST") or IMAP_HOST_DEFAULT
    postings, fails, seen = [], [], set()
    try:
        M = imaplib.IMAP4_SSL(host)
        M.login(user, pw)
        M.select("INBOX", readonly=True)
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
        for sender, pname in SENDERS.items():
            try:
                typ, data = M.search(None, f'(FROM "{sender}" SINCE {since})')
                if typ != "OK" or not data or not data[0]:
                    continue
                for num in data[0].split():
                    typ, raw = M.fetch(num, "(RFC822)")
                    if typ != "OK" or not raw or not raw[0]:
                        continue
                    msg = email.message_from_bytes(raw[0][1])
                    try:
                        edt = parsedate_to_datetime(msg.get("Date"))
                    except Exception:  # noqa: BLE001
                        edt = datetime.now(timezone.utc)
                    for p in PARSERS[pname](_plain_text(msg), edt):
                        if p.canonical_id and p.canonical_id not in seen:
                            seen.add(p.canonical_id)
                            postings.append(p)
            except Exception as e:  # noqa: BLE001
                fails.append((f"gmail:{pname}", f"{type(e).__name__}: {e}"))
        M.logout()
    except Exception as e:  # noqa: BLE001
        fails.append(("gmail", f"{type(e).__name__}: {e}"))
    return postings, fails


if __name__ == "__main__":
    # parser test against a saved plaintext fixture: python gmail_source.py <file>
    fixture = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ii.txt"
    text = Path(fixture).read_text(encoding="utf-8", errors="replace")
    posts = parse_interninsider(text, datetime.now(timezone.utc))
    print(f"parsed {len(posts)} postings")
    for p in posts[:40]:
        print(f"  {p.company:24} | {p.title[:48]:48} | {p.location}")
