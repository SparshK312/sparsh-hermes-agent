#!/usr/bin/env python3
"""
jd_fetch.py — selective job-description fetch for the internship triage.

The scraper only knows title + company + location + URL. For the postings that
pass the cheap filter (the shortlist the frontier model actually judges), we
fetch the real job description so triage reads requirements / period / duration
instead of guessing from the title. This kills wrong-cycle false positives
(e.g. a "SWE Intern" title that is actually Summer 2026) and lets cover lines
reference the real role.

Strategy (cost-aware — Firecrawl credits are finite):
  1. Free fetch: plain urllib GET + stdlib HTML-to-text. $0. Works on
     server-rendered boards (Greenhouse, Lever, Ashby, many Workday).
  2. Firecrawl fallback: only when the free fetch is thin/blocked (JS-heavy or
     Cloudflare-walled boards like Stripe/Vercel). Costs ~1 credit/page.
  3. Give up gracefully (return None) — triage still works title-only for that one.

Only called on the filter-passing shortlist, capped per run, so Firecrawl spend
stays at a few credits/day at most. Stdlib only; no bs4 dependency.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from html.parser import HTMLParser

FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"
UA = "Mozilla/5.0 (compatible; HermesInternshipBot/1.0)"
MIN_GOOD_CHARS = 600       # below this a free fetch is "thin" -> try Firecrawl
MAX_JD_CHARS = 1400        # truncate so the GPT triage payload stays bounded
MIN_USABLE_CHARS = 250     # below this (after all attempts) -> treat as failure

# Boilerplate that means we got a cookie/consent/JS-wall, not a real JD.
_JUNK_RE = re.compile(
    r"(enable javascript|cookies? (to )?(enhance|continue|on their website)|"
    r"accept (all )?cookies|cookie (policy|preferences|consent)|"
    r"please (enable|turn on) javascript|your browser)",
    re.IGNORECASE,
)


def _is_junk(text: str) -> bool:
    """True if the text is too short to be useful or is consent/JS boilerplate."""
    if not text or len(text) < MIN_USABLE_CHARS:
        return True
    head = text[:400]
    return bool(_JUNK_RE.search(head)) and len(text) < MIN_GOOD_CHARS


class _TextExtractor(HTMLParser):
    """Stdlib HTML -> text. Drops script/style/head; collapses whitespace."""

    _SKIP = {"script", "style", "noscript", "svg", "head", "header", "footer", "nav"}

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if t:
                self.parts.append(t)


def _html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001
        return ""
    return re.sub(r"\s+", " ", " ".join(p.parts)).strip()


def _free_fetch(url: str, timeout: int) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype and "text" not in ctype:
                return None
            raw = r.read(2_000_000).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    return _html_to_text(raw) or None


def _firecrawl_fetch(url: str, key: str, timeout: int = 50) -> str | None:
    # waitFor lets JS boards (Workday/Ashby) render before extraction; it costs
    # time, not extra credits. summary keeps the GPT payload small.
    body = json.dumps(
        {"url": url, "formats": ["summary"], "onlyMainContent": True, "waitFor": 6000}
    ).encode("utf-8")
    try:
        req = urllib.request.Request(
            FIRECRAWL_URL,
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None
    d = data.get("data") or {}
    return d.get("summary") or d.get("markdown") or None


def fetch_jd(url: str, firecrawl_key: str | None = None, timeout: int = 20) -> str | None:
    """Return a trimmed job-description string, or None. Free fetch first;
    Firecrawl only if the free text is thin AND a key is available."""
    if not url or not url.startswith("http"):
        return None
    text = _free_fetch(url, timeout)
    if (not text or len(text) < MIN_GOOD_CHARS) and firecrawl_key:
        fc = _firecrawl_fetch(url, firecrawl_key)
        if fc and not _is_junk(fc):
            text = fc
    if _is_junk(text or ""):   # both attempts thin/junk -> let triage use the title
        return None
    return text[:MAX_JD_CHARS]


if __name__ == "__main__":
    # manual test: python jd_fetch.py <url> [firecrawl_key]
    u = sys.argv[1] if len(sys.argv) > 1 else "https://job-boards.greenhouse.io/offerup/jobs/8004171"
    k = sys.argv[2] if len(sys.argv) > 2 else None
    jd = fetch_jd(u, k)
    print(f"[{len(jd) if jd else 0} chars, free={'no' if (jd or '').startswith('[firecrawl]') else 'yes'}]")
    print(jd or "(none)")
