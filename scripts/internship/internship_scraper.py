#!/usr/bin/env python3
"""
Internship scraper pre-runner for Hermes cron.

Fetches markdown READMEs from GitHub aggregators (SimplifyJobs, vanshb03,
speedyapply), extracts intern postings, dedupes against postings_seen.json,
and emits ONLY new postings for the LLM to triage.

Output protocol (Hermes --script wake-gate convention):
- One JSON line per NEW posting goes to stdout
- Final stdout line is the wake-gate JSON:
    {"wakeAgent": true,  "new_count": N, ...}   → LLM runs and triages
    {"wakeAgent": false, "new_count": 0, ...}   → LLM skipped, no Telegram
- Per-source errors go to stderr verbatim. One failed aggregator doesn't kill
  the run; the script continues with the others and reports failures in the
  wake-gate payload so the LLM can mention them.

State: read-only here. The LLM appends to postings_seen.json after triage.

Sources / formats are in internship_sources.py (same directory).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse, urlunparse

try:
    from bs4 import BeautifulSoup
except ImportError:  # bs4 is only needed to parse the HTML-table sources; keep the
    BeautifulSoup = None  # module importable (tests, internship_triage) and fail late.

# Make this script's dir importable so internship_sources.py loads
sys.path.insert(0, str(Path(__file__).resolve().parent))
from internship_sources import (  # noqa: E402
    FETCH_TIMEOUT_SECONDS,
    MAX_AGE_DAYS,
    POSTINGS_SEEN_PATH,
    SOURCES,
)


# ── Posting record ────────────────────────────────────────────────────────────


@dataclass
class Posting:
    """One job posting, normalized across sources. Field names match what
    the internship-posting-triage skill expects in its JSON input."""

    company: str
    title: str
    location: str
    url: str
    posted_date: str  # YYYY-MM-DD
    age_days: int
    terms: str  # "Fall 2026" or "" if the source doesn't say
    source: str  # which aggregator surfaced this
    canonical_id: str  # used for dedup against postings_seen.json
    salary: str = ""  # only speedyapply provides this

    def to_json(self) -> dict:
        return asdict(self)


# ── URL canonicalization ──────────────────────────────────────────────────────


# Tracking-only query params that should be stripped before dedup.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "ref", "source", "src", "from",
    # Aggregator-specific tracking:
    "gh_jid",  # SimplifyJobs / Greenhouse
}


def canonicalize_url(url: str) -> str:
    """Strip tracking params, lowercase host, normalize trailing slash."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not parsed.netloc:
        return url
    query_kept = []
    if parsed.query:
        for kv in parsed.query.split("&"):
            key = kv.split("=", 1)[0].lower()
            if key not in TRACKING_PARAMS:
                query_kept.append(kv)
    cleaned_query = "&".join(query_kept)
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, netloc, path, "", cleaned_query, ""))


def canonical_id(url: str) -> str:
    """Stable ID for dedup. Matches the existing postings_seen.json schema
    ('host.com/path?cleaned_query'), so old entries continue to dedupe."""
    canon = canonicalize_url(url)
    parsed = urlparse(canon)
    base = f"{parsed.netloc}{parsed.path}"
    if parsed.query:
        base += "?" + parsed.query
    return base


# ── Age parsing ───────────────────────────────────────────────────────────────


# Source data uses several formats: "1d", "14d" (speedyapply, simplify),
# "1mo" (older entries), "May 14" (vansh). 🔒 means closed → reject.
RE_DAYS = re.compile(r"^\s*(\d+)\s*d\s*$", re.IGNORECASE)
RE_MONTHS_AGO = re.compile(r"^\s*(\d+)\s*mo\s*$", re.IGNORECASE)
RE_DATE_TEXT = re.compile(r"^\s*([A-Za-z]{3,})\s+(\d{1,2})\s*$")

MONTHS = {
    name: i + 1
    for i, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}


def parse_age_days(text: str, now: datetime | None = None) -> int | None:
    """Return age in days. None when closed, blank, or unparseable."""
    if not text:
        return None
    text = text.strip()
    if "🔒" in text:
        return None
    now = now or datetime.now()
    m = RE_DAYS.match(text)
    if m:
        return int(m.group(1))
    m = RE_MONTHS_AGO.match(text)
    if m:
        return int(m.group(1)) * 30
    m = RE_DATE_TEXT.match(text)
    if m:
        month_key = m.group(1)[:3].lower()
        if month_key in MONTHS:
            month = MONTHS[month_key]
            day = int(m.group(2))
            # If the parsed month is in the future relative to today, the
            # posting was last year.
            year = now.year
            if month > now.month or (month == now.month and day > now.day):
                year -= 1
            try:
                posted = datetime(year, month, day)
                return max(0, (now.date() - posted.date()).days)
            except ValueError:
                return None
    return None


def date_n_days_ago(age_days: int, now: datetime | None = None) -> str:
    now = now or datetime.now()
    return (now - timedelta(days=age_days)).strftime("%Y-%m-%d")


# ── URL extraction from cell content ──────────────────────────────────────────


RE_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def extract_first_url(cell: str) -> str:
    """Return the first http(s) URL in the cell. Prefer <a href> over bare URL."""
    if not cell:
        return ""
    m = RE_HREF.search(cell)
    if m:
        return m.group(1).strip()
    # Markdown link [text](url)
    m = re.search(r"\((https?://[^\s)]+)\)", cell)
    if m:
        return m.group(1).strip()
    # Bare URL
    m = re.search(r"(https?://[^\s)<\"']+)", cell)
    if m:
        return m.group(1).rstrip(").,>")
    return ""


# ── Triage logic (deterministic rules — mirrors the LLM skill) ───────────────
#
# Used by --triage-backlog mode and as a sanity-check during scraping. The
# daily cron's LLM still does the final triage with cover-line generation;
# this Python version handles bulk classification when the LLM call is
# unaffordable (e.g. the 200+ posting initial backlog).
#
# Lists are tuned against real bootstrap-seed data (May 16, 2026 — 212 entries).
# Edge cases handled:
#   - "Software Analyst" (Hitachi)    → accept (tech-adjacent)
#   - "Vehicle Systems Engineer"      → accept (embedded systems)
#   - "AI Agent Developer" (GoodRx)   → accept (AI engineer)
#   - "GTM & AI Innovation"           → reject (go-to-market)
#   - "Cloud Business & Strategy Analytics Analyst" → reject (business analyst)
#   - "Data Analyst Apprenticeship" Cardiff UK → reject (location)
#   - "HR Intern - Digital & AI - Summer 2026" → reject (HR + past period)
#   - "🔥Crowdstrike" / "Diligent Corporation" → normalize before watchlist match

WATCHLIST = {
    # FAANG+
    "meta", "apple", "netflix", "microsoft", "nvidia",
    # AI labs
    "anthropic", "openai", "xai", "cohere", "mistral",
    "google deepmind", "deepmind",
    # Fintech
    "stripe", "plaid", "mercury", "ramp", "brex", "robinhood",
    # Startups
    "vercel", "linear", "notion", "figma", "scale ai", "databricks",
    "perplexity", "cursor", "anysphere", "replit",
    # Already-active companies (avoid re-flagging)
    "amazon", "google", "shopify", "mercor",
}

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

CA_PROVINCES = {
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
}

COUNTRY_POSITIVE_SUBSTRINGS = [
    "united states", "usa", "u.s.a.", "canada",
    # Bare-city abbreviations that unambiguously mean US locations:
    "nyc", "new york city",
]

# Strings that strongly indicate non-US/Canada. Tested against location lowercased.
COUNTRY_NEGATIVE_SUBSTRINGS = [
    "united kingdom", " uk", "uk,", "england", "scotland", "wales",
    "ireland", "germany", "france", "spain", "italy", "netherlands",
    "switzerland", "sweden", "norway", "denmark", "finland", "poland",
    "romania", "czech", "portugal", "greece", "austria", "belgium",
    "india", "japan", "china", " korea", "singapore", "hong kong",
    "taiwan", "vietnam", "indonesia", "philippines", "thailand",
    "australia", "new zealand", "israel", "uae", "dubai", "saudi",
    "brazil", "argentina", "mexico", "colombia", "chile",
    "south africa", "egypt", "kenya", "nigeria",
    # German cities — defense against "DE Schwalbach Frankfurt" matching as Delaware
    "frankfurt", "berlin", "munich", "münchen", "hamburg", "köln", "cologne",
    "stuttgart", "düsseldorf", "schwalbach",
]

POSITIVE_ROLE_KEYWORDS = [
    # Core SWE
    "software engineer", "software developer", "software development",
    "software analyst", "junior developer", "junior software",
    "swe ", " swe", "sde ", " sde", "sdet",
    "software test", "test infra", "software testing",
    # Stack-specific
    "embedded software", "firmware",
    "backend", "back-end", "back end",
    "frontend", "front-end", "front end",
    "full-stack", "full stack", "fullstack",
    # AI/ML
    "machine learning", "ml engineer", "ml researcher", "ml intern",
    "ai engineer", "ai developer", "ai agent", "ai intern",
    "applied ai", "ai research", "ai/ml", "ml/ai",
    "computer vision", "deep learning", "nlp ", " nlp",
    "llm engineer",
    # Data
    "data engineer", "data scientist", "data developer", "data engineering",
    "data analyst", "data science", "analytics engineer",
    # Research (undergrad-friendly variants)
    "research engineer", "research intern",
    # Infra
    "platform engineer", "infrastructure engineer", "devops engineer",
    "site reliability", "cloud engineer", "systems engineer",
    "verification engineer", "vehicle systems",
    # Discipline-tagged engineers (some Geotab/Tesla-style titles)
    "robotics engineer", "controls engineer",
]

NEGATIVE_ROLE_KEYWORDS = [
    # Non-tech departments
    "human resources", "hr intern", "hr co-op", "hr coop",
    "marketing", "communications intern",
    "events intern", "social media", "content intern",
    "sales intern", "sales co-op", "business development",
    "finance intern", "finance co-op", "accounting", "audit intern",
    "tax intern", "private placement", "portfolio analyst",
    "supply chain", "procurement", "operations intern",
    "consulting intern", "consultant",
    # Design / Product (Sparsh wants engineering not PM/design)
    "design intern", "graphic design", "industrial design",
    "ux ", " ux", "ui designer", "ui/ux",
    "product manager", "product management",
    # Legal / Compliance
    "legal intern", "compliance intern",
    # Writing
    "copywriter", "content writer", "writer intern", "journalist",
    # Non-tech engineering disciplines
    "thermal engineer", "mechanical engineer", "chemical engineer",
    "civil engineer", "structural engineer", "aerospace engineer",
    # Other non-tech
    "biology", "chemistry", "clinical",
    # GTM / business analytics
    "go-to-market", "gtm ", " gtm", "brand intern", "investor",
    "strategy analyst", "business analyst", "business analytics",
    "analytics analyst", "strategy analytics", "business strategy",
    "financial analyst", "fp&a",
    # Education level — Sparsh is undergrad
    " phd", "phd ", "ph.d", "doctoral", "graduate researcher",
    "mba intern", "mba co-op",
    # Research Scientist explicitly (often PhD-only; if undergrad-friendly,
    # it'll usually be titled "Research Intern" or "Research Engineer Intern")
    "research scientist",
]

TARGET_PERIODS_LOWER = ["fall 2026", "winter 2027", "spring 2027", "summer 2027"]

# Periods that are past or currently happening → reject for forward planning
PAST_PERIODS_LOWER = [
    "summer 2026", "spring 2026", "winter 2026", "fall 2025",
    "summer 2025", "spring 2025", "winter 2025", "fall 2024",
    "summer 2024", "spring 2024", "winter 2024",
]

OFFSEASON_SOURCE_NAMES = {"simplify-offseason", "vansh-offseason"}


def normalize_company_name(name: str) -> str:
    """Lowercase, strip emojis and corporate suffixes for watchlist match."""
    if not name:
        return ""
    # Drop everything that isn't a word char, space, or ampersand
    s = re.sub(r"[^\w\s&]", " ", name)
    # Strip common suffixes (case-insensitive)
    s = re.sub(
        r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|"
        r"technologies|technology|labs|laboratories|group|holdings|"
        r"company|co)\b\.?",
        "", s, flags=re.IGNORECASE,
    )
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def is_watchlist_company(company: str) -> bool:
    """Substring-tolerant watchlist match. 'Cursor (Anysphere)' → match both."""
    normalized = normalize_company_name(company)
    if not normalized:
        return False
    for entry in WATCHLIST:
        if normalized == entry or entry in normalized or normalized in entry:
            return True
    return False


def classify_location(location: str) -> tuple[str, str]:
    """Returns one of ('match' | 'reject' | 'unclear', reason)."""
    if not location:
        return "unclear", "empty location"
    loc = location.strip()
    if not loc:
        return "unclear", "empty location"
    loc_lower = loc.lower()

    # Remote → ambiguous; default to accept (most US/Canada-listed remote roles
    # are still US/Canada-only on the employer side).
    if loc_lower in {"remote", "remote, usa", "remote usa", "remote (us)",
                     "remote (canada)", "remote, canada"}:
        return "match", "remote (assume US/Canada-based)"

    # Positive country mention wins — even if a non-target country is also in
    # the string (e.g. "Toronto, Canada / London UK").
    for pos in COUNTRY_POSITIVE_SUBSTRINGS:
        if pos in loc_lower:
            return "match", f"contains '{pos}'"

    # Negative country hits → reject
    for neg in COUNTRY_NEGATIVE_SUBSTRINGS:
        if neg in loc_lower:
            return "reject", f"non-US/Canada (contains '{neg.strip()}')"

    # State / province codes — require a COMMA prefix so we don't false-match
    # leading country codes ("DE Schwalbach Frankfurt" → Delaware?? No, Germany).
    # North American format is consistently "City, ST" — comma-prefixed.
    code_matches = re.findall(r",\s*([A-Z]{2})\b", loc)
    us_codes = {c for c in code_matches if c in US_STATES}
    ca_codes = {c for c in code_matches if c in CA_PROVINCES}
    if us_codes:
        return "match", f"US state: {','.join(sorted(us_codes))}"
    if ca_codes:
        return "match", f"Canada province: {','.join(sorted(ca_codes))}"

    return "reject", f"no US/Canada signal in '{loc[:60]}'"


def classify_role(title: str) -> tuple[str, str]:
    """Returns one of ('match' | 'reject' | 'unclear', reason).

    Negatives are checked first — a title that hits both lists is rejected."""
    if not title:
        return "unclear", "empty title"
    t = title.lower()
    for neg in NEGATIVE_ROLE_KEYWORDS:
        if neg in t:
            return "reject", f"non-target role keyword: '{neg.strip()}'"
    for pos in POSITIVE_ROLE_KEYWORDS:
        if pos in t:
            return "match", f"target role keyword: '{pos.strip()}'"
    return "unclear", "no strong tech-role keyword"


def classify_period(title: str, terms: str, source: str) -> tuple[str, str]:
    """Returns one of ('match' | 'reject' | 'unclear', reason).

    Period info is rare in titles (~5% of postings). Source repo is a stronger
    signal — off-season repos by definition only carry Fall/Winter/Spring."""
    bag = " ".join([title or "", terms or ""]).lower()

    # Past periods → reject
    for p in PAST_PERIODS_LOWER:
        if p in bag:
            return "reject", f"past period: '{p}'"

    # 12-month / 16-month placements don't fit the 4-month rotation plan
    if re.search(r"\b(?:12|16)[\s-]?month", bag):
        return "reject", "12/16-month placement (incompatible with 4-month plan)"

    # Explicit target period in title or terms
    for p in TARGET_PERIODS_LOWER:
        if p in bag:
            return "match", f"explicit target period: '{p}'"

    # No explicit period — infer from source
    if source in OFFSEASON_SOURCE_NAMES:
        return "match", "off-season source (Fall 2026 / Winter 2027 / Spring 2027)"

    # speedyapply-ai or unknown — lean accept (the user can review)
    return "unclear", "no period in title/terms; non-off-season source"


def triage_posting(p: "Posting") -> dict:
    """Apply all classifiers. Returns dict with verdict + reason + checks."""
    company_watchlisted = is_watchlist_company(p.company)
    loc_status, loc_reason = classify_location(p.location)
    role_status, role_reason = classify_role(p.title)
    period_status, period_reason = classify_period(p.title, p.terms, p.source)
    age_recent = (p.age_days is not None) and (p.age_days <= 7)

    checks = {
        "watchlist": company_watchlisted,
        "location": [loc_status, loc_reason],
        "role": [role_status, role_reason],
        "period": [period_status, period_reason],
        "age_days": p.age_days,
        "age_recent": age_recent,
    }

    # Any hard reject → skip
    if loc_status == "reject":
        return {"verdict": "skip", "reason": loc_reason, "checks": checks}
    if role_status == "reject":
        return {"verdict": "skip", "reason": role_reason, "checks": checks}
    if period_status == "reject":
        return {"verdict": "skip", "reason": period_reason, "checks": checks}

    # All filters either match or unclear — proceed to verdict
    if company_watchlisted and age_recent:
        return {
            "verdict": "apply now",
            "reason": "watchlist + applicable + recent (≤7d)",
            "checks": checks,
        }
    if company_watchlisted:
        age = p.age_days if p.age_days is not None else "?"
        return {
            "verdict": "wait",
            "reason": f"watchlist + applicable, posted {age}d ago",
            "checks": checks,
        }
    return {
        "verdict": "consider",
        "reason": "applicable but not on watchlist",
        "checks": checks,
    }


# ── Markdown table parser ─────────────────────────────────────────────────────


def parse_markdown_table(raw: str, source_name: str) -> Iterator[Posting]:
    """Find pipe-delimited tables and yield Posting per row.

    Handles multiple sub-tables in one file. Also forward-fills the company
    name across "↳" continuation rows (used by vansh-offseason to compact
    multi-posting companies — only the first row spells the company out)."""
    lines = raw.split("\n")
    headers: list[str] = []
    in_table = False
    last_company = ""  # forward-fill for ↳ continuation rows

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            # Out of a table region. Reset header state for the next one.
            headers = []
            in_table = False
            last_company = ""
            continue

        # Parse cells (drop the leading/trailing | as the split shim)
        cells = [c.strip() for c in stripped.strip("|").split("|")]

        # Header separator like "| --- | --- |" (also accepts ":---", "---:")
        if cells and all(re.fullmatch(r":?[-=]+:?", c) for c in cells):
            in_table = bool(headers)
            continue

        if not headers:
            headers = [c.lower() for c in cells]
            continue

        if in_table and len(cells) == len(headers):
            row = dict(zip(headers, cells))

            # Resolve company-continuation marker (↳) by forward-filling.
            # Strip the markdown/HTML cell first so the marker test works.
            raw_company = row.get("company", "").strip()
            clean_company = re.sub(
                r"<[^>]+>|\*+|\[|\]\([^)]*\)", "", raw_company
            ).strip()
            if clean_company in {"↳", "↳", "↳", "→"} or clean_company == "":
                row["company"] = last_company
            else:
                last_company = clean_company

            posting = row_to_posting(row, source_name)
            if posting:
                yield posting


# ── HTML table parser (SimplifyJobs Off-Season) ───────────────────────────────


def parse_html_table(raw: str, source_name: str) -> Iterator[Posting]:
    """Walk every <table> in the file. BS4 handles HTML embedded in markdown.

    Forward-fills the company name across "↳" continuation rows (SimplifyJobs
    uses this convention in their HTML tables too, just like vanshb03 in
    markdown — only the first row of a multi-posting company spells the name)."""
    if BeautifulSoup is None:
        raise RuntimeError("beautifulsoup4 is required to parse HTML-table sources "
                           "(pip install beautifulsoup4)")
    soup = BeautifulSoup(raw, "html.parser")
    for table in soup.find_all("table"):
        thead = table.find("thead")
        tbody = table.find("tbody")
        if not thead or not tbody:
            continue
        head_row = thead.find("tr")
        if not head_row:
            continue
        headers = [th.get_text(strip=True).lower() for th in head_row.find_all("th")]

        last_company = ""  # per-table continuation tracker

        for tr in tbody.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) != len(headers):
                continue

            row: dict[str, str] = {}
            for header_name, td in zip(headers, tds):
                if header_name == "application":
                    # Grab the FIRST <a href> = direct company link. The
                    # second link in SimplifyJobs cells is the simplify.jobs
                    # rewrite — we'd rather link straight to the source.
                    first_a = td.find("a")
                    row[header_name] = first_a.get("href", "") if first_a else ""
                elif header_name in ("company", "company "):
                    row["company"] = td.get_text(strip=True)
                else:
                    # Multi-line locations use <br>; convert to commas.
                    for br in td.find_all("br"):
                        br.replace_with(", ")
                    row[header_name] = td.get_text(strip=True)

            # Resolve ↳ continuation against last_company (same logic as
            # the markdown parser).
            company_cell = row.get("company", "").strip()
            if company_cell in {"↳", "→"} or company_cell == "":
                row["company"] = last_company
            else:
                last_company = company_cell

            posting = row_to_posting(row, source_name)
            if posting:
                yield posting


# ── Row → Posting normalization ───────────────────────────────────────────────


def row_to_posting(row: dict, source_name: str) -> Posting | None:
    """Map varied source schemas onto our normalized Posting record.

    Returns None when the row is closed (🔒), too old (> MAX_AGE_DAYS),
    missing required fields, or fails URL extraction."""

    company = row.get("company", "").strip()
    title = (
        row.get("role")
        or row.get("position")
        or row.get("title", "")
    ).strip()
    location = row.get("location", "").strip()
    terms = row.get("terms", "").strip()
    salary = row.get("salary", "").strip()

    age_text = (
        row.get("age")
        or row.get("date posted")
        or row.get("date")
        or ""
    ).strip()

    apply_text = (
        row.get("application")
        or row.get("application/link")
        or row.get("posting")
        or row.get("apply")
        or ""
    ).strip()

    if not company or not title:
        return None
    if "🔒" in age_text or "🔒" in title:
        return None

    url = extract_first_url(apply_text)
    if not url or not url.startswith(("http://", "https://")):
        return None

    age_days = parse_age_days(age_text)
    if age_days is None or age_days > MAX_AGE_DAYS:
        return None

    # Scrub leftover markdown/HTML from text fields.
    company = re.sub(r"<[^>]+>|\*+|\[|\]\([^)]*\)", "", company).strip()
    title = re.sub(r"<[^>]+>|\*+", "", title).strip()
    location = re.sub(r"<[^>]+>", ", ", location)
    location = re.sub(r"\s*,\s*,+", ", ", location).strip(" ,")

    return Posting(
        company=company,
        title=title,
        location=location,
        url=canonicalize_url(url),
        posted_date=date_n_days_ago(age_days),
        age_days=age_days,
        terms=terms,
        source=source_name,
        canonical_id=canonical_id(url),
        salary=salary,
    )


# ── State (postings_seen.json) ────────────────────────────────────────────────


def load_seen_ids() -> set[str]:
    """Set of canonical IDs already triaged in prior runs. Read-only here."""
    path = Path(POSTINGS_SEEN_PATH)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[state-warning] postings_seen.json unreadable: {e}", file=sys.stderr)
        return set()
    return {
        entry["id"]
        for entry in data.get("seen", [])
        if isinstance(entry, dict) and "id" in entry
    }


def append_seen_entries(new_entries: list[dict]) -> int:
    """Append entries to postings_seen.json. Used by --bootstrap to seed the
    backlog without LLM triage. Normal-mode runs leave writes to the LLM.

    Returns count of entries actually appended (skips ones with IDs already
    present, so re-running --bootstrap is idempotent)."""
    path = Path(POSTINGS_SEEN_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {"seen": []}
    else:
        data = {"seen": []}

    existing_ids = {
        e["id"] for e in data.get("seen", []) if isinstance(e, dict) and "id" in e
    }
    appended = 0
    for entry in new_entries:
        if entry.get("id") and entry["id"] not in existing_ids:
            data.setdefault("seen", []).append(entry)
            existing_ids.add(entry["id"])
            appended += 1

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return appended


# ── HTTP fetch ────────────────────────────────────────────────────────────────


def fetch_source(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Hermes-InternshipWatcher/2.0 (script-first)"},
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
        return resp.read().decode("utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────


# ── Master table writer ──────────────────────────────────────────────────────


VERDICT_RANK = {"apply now": 0, "consider": 1, "wait": 2}
SOURCE_DISPLAY = {
    "simplify-offseason": "simplify",
    "vansh-offseason": "vansh",
    "speedyapply-ai": "speedyapply",
}

PIPELINE_PATH = Path(
    "/home/hermes/vault/00 - Dashboard/Internship Pipeline.md"
)
OPEN_OPP_SECTION_HEADER = "## Open Opportunities (Triage Queue)"


def _escape_md_cell(s: str, max_len: int) -> str:
    if not s:
        return ""
    s = s.replace("|", "\\|").replace("\n", " ").strip()
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def _format_table_row(entry: dict) -> str:
    posted = entry.get("posted_date") or "?"
    company = _escape_md_cell(entry.get("company", "?"), 28)
    role = _escape_md_cell(entry.get("role", "?"), 70)
    location = _escape_md_cell(entry.get("location", "?"), 38)
    source = SOURCE_DISPLAY.get(entry.get("source", ""), entry.get("source", "")[:12])
    url = entry.get("url", "")
    return f"| {posted} | {company} | {role} | {location} | {source} | [↗]({url}) |"


def build_open_opportunities_section(entries) -> str:
    """Render the full markdown section from postings_seen entries.

    Splits into three sub-tables (apply now / consider / wait), each sorted
    by age ascending (newest first)."""
    actionable = [
        e for e in entries
        if e.get("verdict") in ("apply now", "consider", "wait")
    ]

    actionable.sort(key=lambda e: (
        VERDICT_RANK.get(e.get("verdict"), 99),
        e.get("age_days") if isinstance(e.get("age_days"), (int, float)) else 999,
    ))

    apply_now = [e for e in actionable if e.get("verdict") == "apply now"]
    consider = [e for e in actionable if e.get("verdict") == "consider"]
    wait = [e for e in actionable if e.get("verdict") == "wait"]

    out: list[str] = []
    out.append(OPEN_OPP_SECTION_HEADER)
    out.append("")
    out.append(
        "> Currently-open postings matching your targeting "
        "(Fall 2026 / Winter 2027 / Spring 2027 / Summer 2027 · SWE / AI / ML / Data · US / Canada). "
        "Move rows to *Active Companies* below as you apply."
    )
    out.append(">")
    refreshed = datetime.now().strftime("%Y-%m-%d %H:%M")
    out.append(
        f"> **Last refreshed:** {refreshed} · **Actionable:** {len(actionable)} "
        f"(🟢 {len(apply_now)} apply now · 🟡 {len(consider)} consider · ⏳ {len(wait)} wait)"
    )
    out.append("")

    def emit_table(rows: list, heading: str, blurb: str):
        if not rows:
            return
        out.append(f"### {heading}")
        out.append("")
        out.append(f"_{blurb}_")
        out.append("")
        out.append("| Posted | Company | Role | Location | Source | Apply |")
        out.append("| --- | --- | --- | --- | --- | --- |")
        for e in rows:
            out.append(_format_table_row(e))
        out.append("")

    emit_table(
        apply_now,
        f"🟢 Apply Now ({len(apply_now)})",
        "On watchlist · all filters pass · posted ≤7 days ago — top priority.",
    )
    emit_table(
        consider,
        f"🟡 Consider ({len(consider)})",
        "Not on the 28-company watchlist but otherwise applicable — review and add to watchlist if relevant.",
    )
    emit_table(
        wait,
        f"⏳ Wait ({len(wait)})",
        "On watchlist + applicable but posted >7 days ago — likely past the early-bird window; investigate before applying.",
    )

    return "\n".join(out)


def write_open_opportunities_to_pipeline(section_md: str) -> None:
    """Insert or replace the Open Opportunities section in Pipeline.md."""
    content = PIPELINE_PATH.read_text(encoding="utf-8")

    pattern = re.compile(
        r"^" + re.escape(OPEN_OPP_SECTION_HEADER) + r".*?(?=^## (?!#)|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )

    if pattern.search(content):
        new_content = pattern.sub(section_md.rstrip() + "\n\n", content)
    elif "## Rotation Pipeline" in content:
        new_content = content.replace(
            "## Rotation Pipeline",
            section_md.rstrip() + "\n\n## Rotation Pipeline",
            1,
        )
    else:
        new_content = content.rstrip() + "\n\n" + section_md.rstrip() + "\n"

    PIPELINE_PATH.write_text(new_content, encoding="utf-8")


# ── Backlog triage mode ──────────────────────────────────────────────────────


def run_triage_backlog() -> int:
    """Re-scrape sources to recover the data dropped during bootstrap, apply
    deterministic triage to every bootstrap-seed entry, update postings_seen.json,
    and rebuild the Open Opportunities section in Pipeline.md.

    Idempotent: re-running re-triages everything currently in seen.json that
    has verdict 'bootstrap-seed' (and adds any genuinely new postings)."""
    started = datetime.now()

    seen_path = Path(POSTINGS_SEEN_PATH)
    if seen_path.exists():
        try:
            seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            seen_data = {"seen": []}
    else:
        seen_data = {"seen": []}

    seen_by_id: dict[str, dict] = {
        e["id"]: e for e in seen_data.get("seen", [])
        if isinstance(e, dict) and "id" in e
    }
    bootstrap_ids = {
        eid for eid, entry in seen_by_id.items()
        if entry.get("verdict") == "bootstrap-seed"
    }
    print(
        f"[triage-backlog] Found {len(bootstrap_ids)} bootstrap-seed entries "
        f"to re-triage (out of {len(seen_by_id)} total)",
        file=sys.stderr,
    )

    # Re-scrape all sources to recover age_days / posted_date / terms.
    all_postings: list[Posting] = []
    sources_ok: list[str] = []
    failures: list[tuple[str, str]] = []
    for source in SOURCES:
        name = source["name"]
        try:
            raw = fetch_source(source["url"])
            if source["format"] == "html_table":
                all_postings.extend(parse_html_table(raw, name))
            else:
                all_postings.extend(parse_markdown_table(raw, name))
            sources_ok.append(name)
        except Exception as e:
            failures.append((name, f"{type(e).__name__}: {e}"))
            print(f"[source-failure] {name}: {failures[-1][1]}", file=sys.stderr)

    fresh_by_id: dict[str, Posting] = {p.canonical_id: p for p in all_postings}
    print(
        f"[triage-backlog] Scraped {len(all_postings)} postings; "
        f"{len(fresh_by_id)} unique IDs from {sources_ok}",
        file=sys.stderr,
    )

    today_str = started.strftime("%Y-%m-%d")
    retriaged = stale = newly_triaged = 0
    verdict_counts: Counter = Counter()

    # 1) For every postings_seen entry whose id is in current fresh scrape:
    #    refresh all fields AND re-apply triage rules. This makes the run
    #    idempotent (re-running after a filter change reclassifies everything)
    #    and ensures the master table always reflects current data.
    for entry_id, entry in seen_by_id.items():
        if entry_id not in fresh_by_id:
            continue  # handled in phase 2 below
        p = fresh_by_id[entry_id]
        t = triage_posting(p)
        entry["verdict"] = t["verdict"]
        entry["reason"] = t["reason"]
        entry["age_days"] = p.age_days
        entry["posted_date"] = p.posted_date
        entry["terms"] = p.terms
        entry["salary"] = p.salary
        entry["company"] = p.company
        entry["role"] = p.title
        entry["location"] = p.location
        entry["url"] = p.url
        entry["source"] = p.source
        entry["triaged_at"] = today_str
        entry.pop("note", None)
        retriaged += 1
        verdict_counts[t["verdict"]] += 1

    # 2) For entries no longer in fresh scrape: mark stale if they were
    #    previously actionable or still bootstrap-seed.
    for entry_id, entry in seen_by_id.items():
        if entry_id in fresh_by_id:
            continue
        verdict = entry.get("verdict")
        if verdict in ("bootstrap-seed", "apply now", "consider", "wait"):
            entry["was_verdict"] = verdict
            entry["verdict"] = "stale"
            entry["reason"] = (
                "no longer in any aggregator (was: " + str(verdict) + ")"
            )
            entry["triaged_at"] = today_str
            stale += 1

    # 3) Triage any brand-new postings (in fresh scrape but not in seen.json).
    for p in all_postings:
        if p.canonical_id in seen_by_id:
            continue
        t = triage_posting(p)
        seen_by_id[p.canonical_id] = {
            "id": p.canonical_id,
            "first_seen": today_str,
            "verdict": t["verdict"],
            "reason": t["reason"],
            "company": p.company,
            "role": p.title,
            "location": p.location,
            "url": p.url,
            "source": p.source,
            "age_days": p.age_days,
            "posted_date": p.posted_date,
            "terms": p.terms,
            "salary": p.salary,
            "triaged_at": today_str,
        }
        newly_triaged += 1
        verdict_counts[t["verdict"]] += 1

    # Persist postings_seen.json
    seen_data["seen"] = list(seen_by_id.values())
    seen_path.write_text(
        json.dumps(seen_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Render and inject the master table
    section_md = build_open_opportunities_section(seen_by_id.values())
    try:
        write_open_opportunities_to_pipeline(section_md)
        pipeline_status = "written"
    except Exception as e:
        pipeline_status = f"failed: {type(e).__name__}: {e}"
        print(f"[pipeline-write-failure] {pipeline_status}", file=sys.stderr)

    # Final report
    actionable = sum(verdict_counts.get(v, 0) for v in ("apply now", "consider", "wait"))
    elapsed = round((datetime.now() - started).total_seconds(), 2)
    summary = {
        "mode": "triage-backlog",
        "retriaged": retriaged,
        "stale": stale,
        "newly_triaged": newly_triaged,
        "verdict_breakdown": dict(verdict_counts),
        "actionable_total": actionable,
        "pipeline_md_status": pipeline_status,
        "sources_ok": sources_ok,
        "sources_failed": [{"name": n, "error": e} for n, e in failures],
        "elapsed_seconds": elapsed,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n", 1)[0])
    ap.add_argument(
        "--bootstrap",
        action="store_true",
        help=("Seed postings_seen.json with all currently-visible postings "
              "(verdict='bootstrap-seed') and exit without invoking the LLM. "
              "Use once after first install so the daily cron only triages "
              "truly new postings going forward."),
    )
    ap.add_argument(
        "--triage-backlog",
        action="store_true",
        help=("Re-scrape sources, apply deterministic triage to every "
              "bootstrap-seed entry in postings_seen.json, and rebuild the "
              "Open Opportunities table in Pipeline.md. Use after --bootstrap "
              "to surface the backlog of currently-open postings without "
              "burning LLM tokens."),
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()

    # Mode dispatch: backlog triage short-circuits the normal scrape pipeline.
    if args.triage_backlog:
        return run_triage_backlog()

    started = datetime.now()
    seen_ids = load_seen_ids()

    all_postings: list[Posting] = []
    failures: list[tuple[str, str]] = []  # (source_name, exact_error_message)
    sources_ok: list[str] = []

    for source in SOURCES:
        name = source["name"]
        try:
            raw = fetch_source(source["url"])
        except Exception as e:  # urllib raises a tree of these; report literally
            failures.append((name, f"fetch failed: {type(e).__name__}: {e}"))
            continue

        try:
            if source["format"] == "html_table":
                postings = list(parse_html_table(raw, name))
            elif source["format"] == "markdown_table":
                postings = list(parse_markdown_table(raw, name))
            else:
                failures.append((name, f"unknown format: {source['format']}"))
                continue
        except Exception as e:
            failures.append((name, f"parse failed: {type(e).__name__}: {e}"))
            continue

        sources_ok.append(name)
        all_postings.extend(postings)

    # Dedup against postings_seen.json AND within this batch (same posting
    # often appears in multiple aggregators).
    new_postings: list[Posting] = []
    batch_ids: set[str] = set()
    for p in all_postings:
        if p.canonical_id in seen_ids or p.canonical_id in batch_ids:
            continue
        batch_ids.add(p.canonical_id)
        new_postings.append(p)

    # Stderr: source-level failures, verbatim.
    for name, err in failures:
        print(f"[source-failure] {name}: {err}", file=sys.stderr)

    # ── Bootstrap mode: seed everything as 'seen' without LLM, then exit ──
    if args.bootstrap:
        today = started.strftime("%Y-%m-%d")
        seed_entries = [
            {
                "id": p.canonical_id,
                "first_seen": today,
                "verdict": "bootstrap-seed",
                "company": p.company,
                "role": p.title,
                "location": p.location,
                "url": p.url,
                "source": p.source,
                "note": "Seeded by --bootstrap; not LLM-triaged.",
            }
            for p in new_postings
        ]
        appended = append_seen_entries(seed_entries)

        print(json.dumps({
            "wakeAgent": False,
            "mode": "bootstrap",
            "seeded_count": appended,
            "raw_postings_seen": len(all_postings),
            "already_in_seen_json": len(all_postings) - len(new_postings),
            "sources_ok": sources_ok,
            "sources_failed": [{"name": n, "error": e} for n, e in failures],
            "max_age_days": MAX_AGE_DAYS,
            "scanned_at": started.isoformat(timespec="seconds"),
            "elapsed_seconds": round((datetime.now() - started).total_seconds(), 2),
            "next_step": ("Run the cron normally; only postings new since this "
                          "moment will be triaged by the LLM."),
        }, ensure_ascii=False))
        print(
            f"[bootstrap] Seeded {appended} postings into postings_seen.json. "
            f"Going forward, only truly new postings will be sent to the LLM.",
            file=sys.stderr,
        )
        return 0

    # ── Normal mode: emit JSON-per-posting + wake-gate ──
    for p in new_postings:
        print(json.dumps(p.to_json(), ensure_ascii=False))

    wake = {
        "wakeAgent": len(new_postings) > 0,
        "new_count": len(new_postings),
        "raw_postings_seen": len(all_postings),
        "already_dedup_filtered": len(all_postings) - len(new_postings),
        "sources_ok": sources_ok,
        "sources_failed": [{"name": n, "error": e} for n, e in failures],
        "max_age_days": MAX_AGE_DAYS,
        "scanned_at": started.isoformat(timespec="seconds"),
        "elapsed_seconds": round((datetime.now() - started).total_seconds(), 2),
    }
    print(json.dumps(wake, ensure_ascii=False))

    # Always exit 0 — partial failures are reported in stderr + wake payload,
    # not via exit code. Hermes treats nonzero exit as a hard fail.
    return 0


if __name__ == "__main__":
    sys.exit(main())
