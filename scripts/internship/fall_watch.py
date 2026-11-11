#!/usr/bin/env python3
"""
fall_watch.py — ping Telegram the moment a Fall 2027 internship opens at a top-tier
company in Toronto / Canada / remote.

WHY (2026-11-11): he decided Fall 2027 (Sep–Dec, alongside capstone in Toronto) is worth
taking for a top-tier name only. The board throws Fall 2027 away upstream, and hot_watch
inherits that filter (`_accept` -> `classify_period`), so neither would ever say "Amazon
Toronto just opened a Fall term". This is a SEPARATE watcher with its own term matcher,
its own state and its own cron. It does not touch the Sheet, the store or hot_watch.

Amazon has no separate "Fall" posting today: Fall 2027 is a season OPTION inside the
JD of a Summer-titled req (10553947: "additional openings… 2.Fall 2027 (Start
August/September; End November/December)"). So the matcher reads the whole JD, not
the title, and an existing req that GAINS Fall wording alerts like a new one.

Sources (all four must stay healthy; see "Loud failures"):
  amazon    amazon.jobs search.json, normalized_country_code[]=CAN, queries
            intern / internship / co-op / 2027, paged. Plus USA rows whose location
            type is VIRTUAL (Amazon's own remote marker), labelled "(US-remote)".
  simplify  SimplifyJobs listings.json (the URL internship_sources already uses);
            active rows, Fall 2027 read from `terms` (and the title).
  google    careers results HTML, employment_type=INTERN&location=Canada.
  boards    tier-S/A boards in company_boards.BOARDS via ats_router (manual and
            Amazon excluded; Amazon is the dedicated source above).

Gates: company tier S/A (hotness.brand_tier) plus Amazon and Google always ·
location Canada / Canadian city or province / "Remote" without a US-only qualifier,
blank passes (fail open) · Fall 2027 by match_fall() below.

Loud failures (CLAUDE.md "a check that cannot fail teaches you nothing"):
  * Amazon: an error when hits > rows returned after paging; every CAN row must have
    country_code == "CAN" (if the filter silently stops working, every country comes
    back, as the old `country[]` param did).
  * a floor per source (FLOORS). Two consecutive misses -> "FALL-WATCH SOURCE DOWN".
  * non-zero exit whenever any source failed or a send failed, and run_fall_watch.sh
    propagates it, so the cron's last_status means something.

  fall_watch.py              # poll, alert on anything new
  fall_watch.py --seed       # record the current set WITHOUT alerting
  fall_watch.py --dry-run    # print what would be sent; send nothing, write nothing
  fall_watch.py --heartbeat  # normal run, then "fall-watch alive: …" (also automatic:
                             # the first run on a Monday from 09:00 sends it)
  fall_watch.py --list       # print every Fall-tagged req in the state file

State: {req_key: {first_seen, fall_flag, confidence, title, …}} keyed on the req number
(amazon:<id_icims>, google:<job id>, else the posting's canonical_id, else
simplify:<uuid>). Amazon / Google URLs found on Simplify collapse onto the same key, so
one req is one alert whichever source saw it first.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from textwrap import shorten

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ats_router as A  # noqa: E402
import company_boards  # noqa: E402
import store_paths  # noqa: E402
from brand_first_source import _in_grad_context  # noqa: E402  (read-only helper)
from hot_watch import QUIET_END, QUIET_START  # noqa: E402
from hot_watch import send as _telegram_send  # noqa: E402
from hotness import brand_tier  # noqa: E402
from internship_scraper import canonical_id  # noqa: E402

TARGET_YEAR = 2027
WATCH_TIERS = {"S", "A"}
ALWAYS_COMPANIES = ("amazon", "google")      # watched whatever their tier says
AMAZON_QUERIES = ("intern", "internship", "co-op", "2027")
AMAZON_USA_MAX_PAGES = 5                     # USA is only scanned for VIRTUAL rows
GOOGLE_URL = ("https://www.google.com/about/careers/applications/jobs/results/"
              "?employment_type=INTERN&location=Canada")
GOOGLE_JOB_URL = "https://www.google.com/about/careers/applications/jobs/results/"
GOOGLE_MAX_PAGES = 5                         # 20 cards/page; Canada interns = 9 today
BOARD_CONCURRENCY = 8
BOARD_TIMEOUT = 90
BOARD_FAIL_FRACTION = 0.25                   # more boards than this failing = source down
MAX_ITEMS = 10                               # alert items per Telegram message
ALERT_BUDGET = 3800                          # chars; Telegram's hard cap is 4,096 (see format_alert)
DOWN_AFTER = 2                               # consecutive misses before SOURCE DOWN
PRUNE_DAYS = 180                             # forget reqs unseen this long
HEARTBEAT_WEEKDAY, HEARTBEAT_HOUR = 0, 9     # Mondays, first run from 09:00 (no extra cron)

# Floors: below these the source is assumed broken, not empty (measured 2026-11-11:
# Simplify 17,644 rows · Amazon CAN "intern" 9 · Google Canada interns 9 titles).
FLOORS = {"simplify": 10_000, "amazon": 5, "google": 1}

RANK = {None: 0, "low": 1, "medium": 2, "high": 3}
SOURCE_ORDER = {"amazon": 0, "google": 1, "boards": 2, "simplify": 3}


def _log(m: str) -> None:
    print(f"[fall-watch] {m}", file=sys.stderr)


def _simplify_url() -> str:
    """The exact listings.json URL the scraper already reads, so the two can't drift."""
    try:
        from internship_sources import SOURCES
        for s in SOURCES:
            if s.get("format") == "simplify_json" and s.get("url"):
                return s["url"]
    except Exception as e:  # noqa: BLE001
        _log(f"internship_sources unreadable ({e}); using the built-in Simplify URL")
    return ("https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/"
            "dev/.github/scripts/listings.json")


# ── term matcher ──────────────────────────────────────────────────────────────
# Deliberately independent of internship_scraper.classify_period / brand_first._accept:
# those REJECT Fall 2027 today and are being changed by another workstream.
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
           "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MON = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
        r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_FALL = r"(?:fall|autumn)"
_Y = str(TARGET_YEAR)

_HIGH_FWD = re.compile(
    rf"\b{_FALL}(?:\s+(?:term|semester|session|co-?op|internship|intern|cohort))?"
    rf"\s*(?:of\s+)?[,\-–]?\s*{_Y}\b", re.I)
_HIGH_REV = re.compile(rf"\b{_Y}\s*[-–,]?\s*{_FALL}\b", re.I)
# "September – December 2027", "Sep 2027 - Dec 2027", "August 30, 2027 to December 10, 2027"
_RANGE = re.compile(
    rf"\b{_MON}\.?(?:\s+\d{{1,2}})?,?\s*(20\d\d)?\s*(?:-|–|—|to|through|thru|until)\s*"
    rf"{_MON}\.?(?:\s+\d{{1,2}})?,?\s*(20\d\d)\b", re.I)
# "starts January 2027, May 2027, September 2027"
_START_MONTH = re.compile(rf"\b(aug(?:ust)?|sep(?:t(?:ember)?)?)\.?(?:\s+\d{{1,2}})?,?\s+{_Y}\b",
                          re.I)
_START_WORD = re.compile(r"\b(start|starts|starting|begin|begins|beginning|commenc\w*)\b", re.I)
_END_WORD = re.compile(r"\b(end|ends|ending|through|thru|until|to)\b|[-–—]", re.I)
_SENTENCE = re.compile(r"[.;!?\n]")
# Year-less: "Fall term", "fall co-op", "spring/fall co-op roles", "Fall/Winter co-op".
_LOW = re.compile(
    rf"\b{_FALL}(?:\s*/\s*(?:winter|spring|summer))?\s+"
    r"(term|semester|co-?op|coop|internships?|interns?|session|cohort|placement|work\s+term)s?\b"
    r"(?!\s*(?:of\s+)?[,\-–]?\s*20\d\d)", re.I)
_ANY_FALL_YEAR = re.compile(rf"\b{_FALL}\s*(?:of\s+)?(20\d\d)\b|\b(20\d\d)\s*{_FALL}\b", re.I)
_SEASON_YEAR = re.compile(r"\b(fall|autumn|winter|spring|summer)\s+(20\d\d)\b", re.I)
_SEASON_YEAR_REV = re.compile(r"\b(20\d\d)\s+(fall|autumn|winter|spring|summer)\b", re.I)


@dataclass
class FallMatch:
    fall: bool = False
    confidence: str | None = None      # "high" | "medium" | "low"
    evidence: str = ""
    seasons: list[str] = field(default_factory=list)


_GRAD_AFTER = re.compile(r"^\W{0,3}(graduat\w*|grads?\b|degree|conferral|convocation)", re.I)


def _grad(text: str, m: re.Match) -> bool:
    """Graduation clause on either side: brand_first_source._in_grad_context covers
    "graduate in fall 2027"; the look-ahead covers "Fall 2027 graduates"."""
    return _in_grad_context(text, m.start()) or bool(_GRAD_AFTER.match(text[m.end():m.end() + 20]))


def _snip(text: str, start: int, end: int, pad: int = 40) -> str:
    a, b = max(0, start - pad), min(len(text), end + pad)
    while a > 0 and not text[a - 1].isspace() and a < start:     # whole words only
        a += 1
    while b < len(text) and not text[b].isspace() and b > end:
        b -= 1
    return re.sub(r"\s+", " ", text[a:b]).strip()


def _month(tok: str) -> int:
    return _MONTHS[tok[:3].lower()]


def _seasons_in(text: str) -> list[str]:
    out: list[str] = []
    for rx, si, yi in ((_SEASON_YEAR, 1, 2), (_SEASON_YEAR_REV, 2, 1)):
        for m in rx.finditer(text):
            if _grad(text, m):
                continue
            s = m.group(si).capitalize().replace("Autumn", "Fall")
            label = f"{s} {m.group(yi)}"
            if label not in out:
                out.append(label)
    return out


def match_fall(*texts: str, terms=None) -> FallMatch:
    """Does any of `texts` (title, JD, …) or the `terms` list offer a Fall 2027 term?

    high   — "Fall 2027" / "2027 Fall" / "Autumn 2027", a Fall 2027 entry in `terms`, or
             an Aug/Sep → Nov/Dec 2027 date range.
    medium — a start in Aug/Sep 2027 named as a start ("starts … September 2027"), or a
             range that starts Aug/Sep 2027 and ends somewhere else.
    low    — year-less "Fall term" / "fall co-op" / "spring/fall co-op roles", and only
             when the text names no OTHER year's Fall (then the boilerplate is about it).
    Graduation clauses never count ("You will graduate in fall 2027 or spring 2028").
    """
    best = FallMatch(seasons=[str(t) for t in (terms or []) if t])

    def take(conf: str, ev: str) -> None:
        if RANK[conf] > RANK[best.confidence]:
            best.fall, best.confidence, best.evidence = True, conf, ev

    for t in terms or []:
        if re.fullmatch(rf"\s*{_FALL}\s+{_Y}\s*", str(t), re.I):
            take("high", f"terms: {', '.join(map(str, terms))}")

    other_fall_year = False
    for raw in texts:
        text = raw or ""
        if not text:
            continue
        for s in _seasons_in(text):
            if s not in best.seasons:
                best.seasons.append(s)
        for rx in (_HIGH_FWD, _HIGH_REV):
            for m in rx.finditer(text):
                if not _grad(text, m):
                    take("high", _snip(text, m.start(), m.end()))
        for m in _RANGE.finditer(text):
            if _grad(text, m):
                continue
            m1, y1, m2, y2 = _month(m.group(1)), m.group(2), _month(m.group(3)), int(m.group(4))
            start_year = int(y1) if y1 else (y2 if m2 >= m1 else y2 - 1)
            if m1 in (8, 9) and start_year == TARGET_YEAR:
                if m2 in (11, 12) and y2 == TARGET_YEAR:
                    take("high", _snip(text, m.start(), m.end()))
                else:
                    take("medium", _snip(text, m.start(), m.end()))
        for m in _START_MONTH.finditer(text):
            if _grad(text, m):
                continue
            clause = _SENTENCE.split(text[max(0, m.start() - 160):m.start()])[-1]
            starts = list(_START_WORD.finditer(clause))
            if not starts:
                continue
            between = clause[starts[-1].end():]
            if _END_WORD.search(between):
                continue                    # "starting May 2027 to September 2027"
            take("medium", _snip(text, m.start(), m.end(), pad=60))
        for m in _ANY_FALL_YEAR.finditer(text):
            yr = int(m.group(1) or m.group(2))
            if yr != TARGET_YEAR and not _grad(text, m):
                other_fall_year = True
    # A title that names a year, none of them 2027, makes year-less boilerplate about
    # THAT year ("Robotics - Software Development Engineer Intern - 2026 - Toronto"
    # carries the same "spring/fall co-op roles" line as the 2027 req).
    title_years = {int(y) for y in re.findall(r"\b(20\d\d)\b", texts[0] or "")} if texts else set()
    other_title_year = bool(title_years) and TARGET_YEAR not in title_years
    if best.confidence is None and not other_fall_year and not other_title_year:
        for raw in texts:
            text = raw or ""
            for m in _LOW.finditer(text):
                if not _grad(text, m):
                    take("low", _snip(text, m.start(), m.end()))
                    break
            if best.confidence:
                break
    return best


# ── location + company gates ──────────────────────────────────────────────────
_PROV = r"(ON|BC|QC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)"
_CAN_CASED = re.compile(rf"(,\s*{_PROV}\b|\bCA[,\-]\s*{_PROV}\b|\bCAN\b)")
_CAN_WORDS = re.compile(
    r"\b(canada|canadian|ontario|british columbia|quebec|québec|alberta|manitoba|"
    r"saskatchewan|nova scotia|new brunswick|newfoundland|prince edward island|"
    r"toronto|vancouver|montreal|montréal|ottawa|waterloo|kitchener|mississauga|markham|"
    r"burnaby|calgary|edmonton|winnipeg|halifax|gatineau|laval|oakville|brampton|"
    r"richmond hill|quebec city)\b", re.I)
_REMOTE = re.compile(r"\bremote\b", re.I)
_US_ONLY = re.compile(
    r"\b(us|u\.s\.|usa|united states)[\s-]*only\b|"
    r"\bremote\s*[-–,:(/]\s*(us|u\.s\.|usa|united states)\b|"
    r"\b(us|u\.s\.|usa|united states)\s*[-–,(/]?\s*remote\b|"
    r"\bremote\s+(?:in|within)\s+the\s+(us|u\.s\.|usa|united states)\b", re.I)


def is_canadian(loc: str) -> bool:
    return bool(_CAN_CASED.search(loc or "") or _CAN_WORDS.search(loc or ""))


def location_ok(loc: str) -> bool:
    """Canada, a Canadian city/province, or Remote without a US-only qualifier.
    Blank passes (fail open: no location is not evidence of a bad one). A multi-city
    string passes when any of its cities is Canadian."""
    loc = (loc or "").strip()
    if not loc:
        return True
    if is_canadian(loc):
        return True
    if _REMOTE.search(loc) and not _US_ONLY.search(loc):
        return True
    return False


def company_ok(name: str) -> bool:
    n = (name or "").lower()
    if any(c in n for c in ALWAYS_COMPANIES):
        return True
    try:
        return brand_tier(name) in WATCH_TIERS
    except Exception:  # noqa: BLE001
        return False


# ── keys ──────────────────────────────────────────────────────────────────────
_AMAZON_ID = re.compile(r"amazon\.jobs/(?:[a-z]{2}(?:-[a-z]{2})?/)?jobs/(\d+)", re.I)
_GOOGLE_ID = re.compile(r"careers/applications/jobs/results/(\d+)", re.I)


def req_key(url: str = "", fallback: str = "") -> str:
    u = url or ""
    m = _AMAZON_ID.search(u)
    if m:
        return f"amazon:{m.group(1)}"
    m = _GOOGLE_ID.search(u)
    if m:
        return f"google:{m.group(1)}"
    if u:
        try:
            return canonical_id(u)
        except Exception:  # noqa: BLE001
            pass
    return fallback


# ── sources ───────────────────────────────────────────────────────────────────
@dataclass
class SourceResult:
    name: str
    rows: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    raw: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def _row(source, key, company, title, location, url, match: FallMatch, **extra) -> dict:
    return {"source": source, "key": key, "company": company, "title": title or "",
            "location": location or "", "url": url or "", "fall": match.fall,
            "confidence": match.confidence, "evidence": match.evidence,
            "seasons": match.seasons, **extra}


def _amazon_text(j: dict) -> str:
    return "\n".join(A.clean_fragment(j.get(k) or "") for k in
                     ("description", "basic_qualifications", "preferred_qualifications"))


def _amazon_virtual(j: dict) -> bool:
    for loc in j.get("locations") or []:
        try:
            o = json.loads(loc) if isinstance(loc, str) else loc
        except Exception:  # noqa: BLE001
            continue
        if str((o or {}).get("type", "")).upper() == "VIRTUAL":
            return True
    return False


def _amazon_row(j: dict, us_remote: bool) -> dict:
    jid = str(j.get("id_icims") or j.get("id") or "")
    loc = j.get("normalized_location") or j.get("location") or ""
    if us_remote:
        loc = f"{j.get('location') or loc} (US-remote)"
    title = j.get("title", "")
    return _row("amazon", f"amazon:{jid}", "Amazon", title, loc,
                "https://www.amazon.jobs" + (j.get("job_path") or f"/en/jobs/{jid}"),
                match_fall(title, _amazon_text(j)), amazon_id=jid, us_remote=us_remote)


async def src_amazon(client) -> SourceResult:
    res = SourceResult("amazon")
    by_id: dict[str, dict] = {}
    for q in AMAZON_QUERIES:
        try:
            jobs, hits = await A.amazon_search(client, q, ("CAN",))
        except Exception as e:  # noqa: BLE001
            res.problems.append(f"CAN {q!r} fetch failed: {type(e).__name__}: {e}")
            continue
        res.raw += len(jobs)
        if hits is not None and len(jobs) < hits:
            res.problems.append(f"CAN {q!r}: {hits} hits but only {len(jobs)} returned")
            _log(f"ERROR amazon CAN {q!r}: {hits} hits > {len(jobs)} returned after paging")
        bad = [j for j in jobs if j.get("country_code") != "CAN"]
        if bad:
            ex = ", ".join(f"{j.get('id_icims')}={j.get('country_code')}" for j in bad[:5])
            res.problems.append(f"CAN {q!r}: {len(bad)} row(s) not country_code CAN ({ex})")
            _log(f"🔴 amazon CAN {q!r}: {len(bad)} of {len(jobs)} rows are NOT "
                 f"country_code CAN ({ex}) — the country filter is not working")
        if q == "intern" and len(jobs) < FLOORS["amazon"]:
            res.problems.append(f"CAN 'intern' returned {len(jobs)} < floor {FLOORS['amazon']}")
        for j in jobs:
            if j.get("country_code") != "CAN" or not A.default_intern_filter(j.get("title", "")):
                continue
            by_id.setdefault(str(j.get("id_icims") or j.get("id")), _amazon_row(j, False))
    # USA: only Amazon's own VIRTUAL location type counts as remote (a "US, Virtual"
    # posting carries locations[].type == "VIRTUAL"; zero intern rows have it today).
    for q in AMAZON_QUERIES:
        try:
            jobs, _ = await A.amazon_search(
                client, q, ("USA",), max_pages=AMAZON_USA_MAX_PAGES,
                page_useful=lambda j: A.default_intern_filter(j.get("title", "")))
        except Exception as e:  # noqa: BLE001
            res.problems.append(f"USA {q!r} fetch failed: {type(e).__name__}: {e}")
            continue
        res.raw += len(jobs)
        for j in jobs:
            if _amazon_virtual(j) and A.default_intern_filter(j.get("title", "")):
                by_id.setdefault(str(j.get("id_icims") or j.get("id")), _amazon_row(j, True))
    res.rows = list(by_id.values())
    return res


async def src_simplify(client) -> SourceResult:
    import httpx
    res = SourceResult("simplify")
    url = _simplify_url()
    try:
        r = await client.get(url, timeout=httpx.Timeout(60.0, connect=10.0),
                             follow_redirects=True)
        if r.status_code != 200:
            res.problems.append(f"HTTP {r.status_code}")
            return res
        data = r.json()
    except Exception as e:  # noqa: BLE001
        res.problems.append(f"fetch failed: {type(e).__name__}: {e}")
        return res
    if not isinstance(data, list):
        res.problems.append(f"unexpected payload {type(data).__name__}")
        return res
    res.raw = len(data)
    if len(data) < FLOORS["simplify"]:
        res.problems.append(f"{len(data)} rows < floor {FLOORS['simplify']}")
    for x in data:
        if not isinstance(x, dict) or not x.get("active") or x.get("is_visible") is False:
            continue
        company = x.get("company_name") or ""
        if not company_ok(company):
            continue
        locs = x.get("locations") or []
        loc = "; ".join(str(l) for l in locs)
        if not location_ok(loc):
            continue
        m = match_fall(x.get("title") or "", terms=x.get("terms") or [])
        key = req_key(x.get("url") or "", f"simplify:{x.get('id')}")
        res.rows.append(_row("simplify", key, company, x.get("title"), loc,
                             x.get("url"), m, simplify_id=x.get("id")))
    return res


_G_CARD_START = re.compile(r"<li class=\"lLd3Je\"")
_G_CARD_END = re.compile(r"aria-label=\"Learn more about[^\"]*\"")   # last thing in a card
_G_TITLE = re.compile(r"<h3 class=\"QJPWVe\">(.*?)</h3>", re.S)
_G_LINK = re.compile(r"jobs/results/(\d+)-([a-z0-9-]+)")
_G_LOC = re.compile(r"<span class=\"r0wTof[^\"]*\">(.*?)</span>", re.S)
_G_MATCHED = re.compile(r"<span class=\"SWhIm\">(\d+)</span>\s*jobs? matched")


def parse_google(page: str) -> tuple[list[dict], int | None, int]:
    """(cards, jobs-matched count, number of h3.QJPWVe titles on the page)."""
    n_titles = len(_G_TITLE.findall(page))
    m = _G_MATCHED.search(page)
    matched = int(m.group(1)) if m else None
    cards = []
    starts = [m.start() for m in _G_CARD_START.finditer(page)] + [len(page)]
    for a, b in zip(starts, starts[1:]):
        body = page[a:b]
        end = _G_CARD_END.search(body)
        if end:                       # drop the share menu / the rest of the page
            body = body[:end.end()]
        t = _G_TITLE.search(body)
        link = _G_LINK.search(body)
        if not (t and link):
            continue
        title = html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip()
        locs = []
        for l in _G_LOC.findall(body):
            v = html.unescape(re.sub(r"<[^>]+>", "", l)).strip().lstrip(";").strip()
            if v and v not in locs:
                locs.append(v)
        text = A.clean_fragment(body)
        cards.append({"id": link.group(1), "slug": link.group(2), "title": title,
                      "location": "; ".join(locs), "text": text})
    return cards, matched, n_titles


async def src_google(client) -> SourceResult:
    res = SourceResult("google")
    seen: dict[str, dict] = {}
    matched = None
    headers = {"User-Agent": A.UA, "Accept-Language": "en-US,en;q=0.9"}
    for page_no in range(1, GOOGLE_MAX_PAGES + 1):
        url = GOOGLE_URL + (f"&page={page_no}" if page_no > 1 else "")
        try:
            r = await client.get(url, headers=headers, follow_redirects=True)
        except Exception as e:  # noqa: BLE001
            res.problems.append(f"page {page_no} fetch failed: {type(e).__name__}: {e}")
            break
        if r.status_code != 200:
            res.problems.append(f"page {page_no} HTTP {r.status_code}")
            break
        cards, m, n_titles = parse_google(r.text)
        matched = m if m is not None else matched
        if page_no == 1 and n_titles < FLOORS["google"]:
            res.problems.append(f"page 1 has {n_titles} h3.QJPWVe titles < floor "
                                f"{FLOORS['google']} (layout changed?)")
        new = [c for c in cards if c["id"] not in seen]
        for c in new:
            seen[c["id"]] = c
        if not new or matched is None or len(seen) >= matched:
            break
    else:
        _log(f"google: page cap {GOOGLE_MAX_PAGES} hit — {len(seen)} of {matched} seen")
    if matched is not None and len(seen) < matched:
        res.problems.append(f"{matched} jobs matched but only {len(seen)} parsed")
        _log(f"ERROR google: {matched} matched > {len(seen)} parsed")
    res.raw = len(seen)
    for c in seen.values():
        url = f"{GOOGLE_JOB_URL}{c['id']}-{c['slug']}"
        res.rows.append(_row("google", f"google:{c['id']}", "Google", c["title"],
                             c["location"], url, match_fall(c["title"], c["text"])))
    return res


def watched_boards() -> list[dict]:
    return [b for b in company_boards.BOARDS
            if (b.get("tier") or "").upper() in WATCH_TIERS
            and b.get("ats_type") not in ("manual", "amazon")]


async def src_boards(client) -> SourceResult:
    res = SourceResult("boards")
    boards = watched_boards()
    getattr(A, "BOARD_FETCH_FAILURES", set()).clear()
    failed: set[str] = set()
    sem = asyncio.Semaphore(BOARD_CONCURRENCY)

    async def one(b):
        async with sem:
            try:
                return b, await asyncio.wait_for(A.fetch_board(client, b), BOARD_TIMEOUT)
            except Exception as e:  # noqa: BLE001
                _log(f"board {b['name']} failed: {type(e).__name__}: {e}")
                failed.add(b["name"])
                return b, []

    results = await asyncio.gather(*[one(b) for b in boards])
    failed |= set(getattr(A, "BOARD_FETCH_FAILURES", set()))
    for b, recs in results:
        res.raw += len(recs)
        for r in recs:
            if not (r.title and r.url and A.default_intern_filter(r.title)):
                continue
            if not location_ok(r.location):
                continue
            res.rows.append(_row("boards", req_key(r.url, r.url), b["name"], r.title,
                                 r.location, r.url, match_fall(r.title, r.full_jd)))
    if failed:
        _log(f"{len(failed)} of {len(boards)} board(s) failed: {', '.join(sorted(failed))}")
    if not boards or len(failed) > len(boards) * BOARD_FAIL_FRACTION or res.raw == 0:
        res.problems.append(f"{len(failed)} of {len(boards)} boards failed, "
                            f"{res.raw} postings fetched")
    return res


SOURCES = {"amazon": src_amazon, "simplify": src_simplify,
           "google": src_google, "boards": src_boards}


async def collect(names) -> dict[str, SourceResult]:
    async with A.make_client() as client:
        async def guarded(n):
            try:
                return await SOURCES[n](client)
            except Exception as e:  # noqa: BLE001 - one source never kills the others
                return SourceResult(n, problems=[f"crashed: {type(e).__name__}: {e}"])
        got = await asyncio.gather(*[guarded(n) for n in names])
    return {r.name: r for r in got}


def gate_and_merge(results: dict[str, SourceResult]) -> list[dict]:
    """Company + location gates, then one row per req key (the best-evidenced one)."""
    best: dict[str, dict] = {}
    for res in results.values():
        for r in res.rows:
            if not r.get("key") or not company_ok(r["company"]):
                continue
            if not (r.get("us_remote") or location_ok(r["location"])):
                continue
            cur = best.get(r["key"])
            if cur is None or (RANK[r["confidence"]], -SOURCE_ORDER[r["source"]]) > \
                    (RANK[cur["confidence"]], -SOURCE_ORDER[cur["source"]]):
                if cur is not None:
                    r = {**r, "seasons": list(dict.fromkeys(r["seasons"] + cur["seasons"]))}
                best[r["key"]] = r
    return list(best.values())


# ── state ─────────────────────────────────────────────────────────────────────
def state_path() -> Path:
    """VPS: next to hot_watch_state.json. Mac: ~/.hermes/state/ -- deliberately NOT
    ~/.hermes/internship/, because store_paths.on_vps() treats that directory's
    existence as "this is the VPS" and creating it would flip every Mac script."""
    env = os.environ.get("FALL_WATCH_STATE")
    if env:
        return Path(env)
    if store_paths.on_vps():
        return Path.home() / ".hermes" / "internship" / "fall_watch_state.json"
    return Path.home() / ".hermes" / "state" / "fall_watch_state.json"


class StateCorrupt(RuntimeError):
    pass


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "seeded": False, "reqs": {}, "sources": {}}
    try:
        st = json.loads(path.read_text())
    except Exception as e:  # noqa: BLE001
        raise StateCorrupt(f"{path}: {e}") from e
    if not isinstance(st, dict) or not isinstance(st.get("reqs", {}), dict):
        raise StateCorrupt(f"{path}: not a fall_watch state dict")
    st.setdefault("reqs", {})
    st.setdefault("sources", {})
    return st


def save_state(path: Path, st: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True))
    os.replace(tmp, path)


def update_reqs(st: dict, rows: list[dict], now: str) -> list[dict]:
    """Record every gated row; return the ones that need an alert: Fall-flagged and
    either never alerted, or alerted at a LOWER confidence than they now carry. So a
    new key alerts, an existing key whose fall flag flips false -> true alerts, and an
    already-alerted key is silent."""
    reqs = st.setdefault("reqs", {})
    due = []
    for r in rows:
        e = reqs.get(r["key"])
        if e is None:
            e = reqs[r["key"]] = {"first_seen": now, "alerted": False, "alerted_rank": 0}
        e.update(fall_flag=bool(r["fall"]), confidence=r["confidence"], title=r["title"],
                 company=r["company"], location=r["location"], url=r["url"],
                 source=r["source"], last_seen=now)
        if r["fall"] and (not e.get("alerted") or RANK[r["confidence"]] > e.get("alerted_rank", 0)):
            due.append(r)
    return due


def mark_alerted(st: dict, rows: list[dict], now: str) -> None:
    for r in rows:
        e = st["reqs"][r["key"]]
        e["alerted"] = True
        e["alerted_rank"] = max(e.get("alerted_rank", 0), RANK[r["confidence"]])
        e["alerted_at"] = now


def prune(st: dict, now: datetime) -> None:
    for k in list(st.get("reqs", {})):
        try:
            last = datetime.fromisoformat(st["reqs"][k].get("last_seen", ""))
        except Exception:  # noqa: BLE001
            continue
        if (now - last).days > PRUNE_DAYS:
            del st["reqs"][k]


def source_health(st: dict, res: SourceResult, now: str) -> str | None:
    """Update the consecutive-miss counter; return a message to send, or None.
    SOURCE DOWN fires on the DOWN_AFTER-th consecutive miss (not the first: one bad
    fetch is weather), once per outage; RECOVERED fires when a downed source returns.
    The caller sets `down_alerted` only after the send succeeds."""
    s = st.setdefault("sources", {}).setdefault(res.name, {"misses": 0, "down_alerted": False})
    if res.ok:
        msg = f"FALL-WATCH SOURCE RECOVERED: {res.name}" if s.get("down_alerted") else None
        s.update(misses=0, last_ok=now, last_problem=None)
        if not msg:
            s["down_alerted"] = False
        return msg
    s["misses"] = int(s.get("misses", 0)) + 1
    s["last_problem"] = "; ".join(res.problems)[:500]
    if s["misses"] >= DOWN_AFTER and not s.get("down_alerted"):
        return (f"FALL-WATCH SOURCE DOWN: {res.name} — {s['misses']} consecutive misses. "
                f"{s['last_problem']}")
    return None


# ── message ───────────────────────────────────────────────────────────────────
def _order(r: dict):
    return (r["source"] != "amazon" and r["company"] != "Amazon",
            -RANK[r["confidence"]], r["company"].lower(), r["title"].lower())


def format_alert(rows: list[dict]) -> str:
    """🔴 Bounded by ALERT_BUDGET as well as MAX_ITEMS (review fix, 2026-11-11). Telegram
    rejects a message over 4,096 characters, and send() then returns False, so nothing is
    marked and the SAME oversize message is retried every hour, forever: the watcher goes
    dark exactly when a burst of Fall reqs opens. Ten real-shaped rows measured 4,028
    (Amazon) and 4,928 (NVIDIA Workday URLs) characters. Items that do not fit are
    counted in the "…and N more" line, like the MAX_ITEMS overflow."""
    rows = sorted(rows, key=_order)
    lines: list[str] = []
    shown = 0
    for r in rows[:MAX_ITEMS]:
        item: list[str] = []
        seasons = ", ".join(r.get("seasons") or []) or "?"
        conf = r["confidence"]
        label = "LOW CONFIDENCE, year-less" if conf == "low" else conf
        tag = f"seasons: {seasons} · {label}: \"{shorten(r['evidence'], 90, placeholder='…')}\""
        item.append(f"FALL 2027: {r['company']} — {r['title'][:90]} · "
                    f"{(r['location'] or 'location n/a')[:70]} · {tag}")
        if r.get("url"):
            item.append(r["url"])
        aid = r.get("amazon_id") or (r["key"].split(":", 1)[1] if r["key"].startswith("amazon:") else "")
        if aid:
            item.append(f"REFERRAL FIRST: send your uncle job ID {aid} before applying.")
        item.append("")
        if shown and len("\n".join(lines + item)) > ALERT_BUDGET:
            break
        lines += item
        shown += 1
    extra = len(rows) - shown
    if extra > 0:
        lines.append(f"…and {extra} more (fall_watch.py --list on the VPS).")
    return "\n".join(lines).rstrip()


def _quiet(hour: int) -> bool:
    return QUIET_START <= hour or hour < QUIET_END


def heartbeat_due(st: dict, now: datetime) -> bool:
    """Weekly, built in: the first run on a Monday from 09:00 sends it, so the hourly
    cron needs no second job and no arguments. `--heartbeat` forces one."""
    if now.weekday() != HEARTBEAT_WEEKDAY or now.hour < HEARTBEAT_HOUR:
        return False
    return (st.get("last_heartbeat") or "")[:10] != now.date().isoformat()


# ── main ──────────────────────────────────────────────────────────────────────
def run(argv=None, send=None, now: datetime | None = None, collect_fn=None) -> int:
    ap = argparse.ArgumentParser(description="Fall 2027 internship watcher")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--heartbeat", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sources", default=",".join(SOURCES))
    args = ap.parse_args(argv)
    send = send or _telegram_send
    now = now or datetime.now()
    stamp = now.isoformat(timespec="seconds")
    path = state_path()

    try:
        st = load_state(path)
    except StateCorrupt as e:
        _log(f"🔴 STATE CORRUPT: {e} — refusing to run (would re-alert everything). "
             f"Move it aside and run --seed.")
        if not args.dry_run:
            send(f"FALL-WATCH STATE CORRUPT: {e}. Watcher is stopped until it is fixed.")
        return 2

    if args.list:
        fall = [(k, e) for k, e in st["reqs"].items() if e.get("fall_flag")]
        for k, e in sorted(fall, key=lambda kv: kv[1].get("first_seen", "")):
            print(f"{e.get('first_seen', '?')[:10]}  {e.get('confidence') or '?':6}  "
                  f"{e.get('company')} — {e.get('title')} · {e.get('location')}\n"
                  f"            {e.get('url')}  [{k}]")
        print(f"{len(fall)} Fall-tagged of {len(st['reqs'])} tracked · {path}")
        return 0

    names = [n.strip() for n in args.sources.split(",") if n.strip() in SOURCES]
    results = (collect_fn or (lambda ns: asyncio.run(collect(ns))))(names)
    rows = gate_and_merge(results)
    for res in results.values():
        fall_n = sum(1 for r in rows if r["source"] == res.name and r["fall"])
        _log(f"{res.name}: raw {res.raw} · gated {sum(1 for r in rows if r['source'] == res.name)}"
             f" · fall {fall_n} · {'ok' if res.ok else 'FAILED: ' + '; '.join(res.problems)}")

    health_msgs = [(res.name, m) for res in results.values()
                   if (m := source_health(st, res, stamp))]
    due = update_reqs(st, rows, stamp)
    prune(st, now)
    st["last_run"] = stamp
    failed_sources = [r.name for r in results.values() if not r.ok]
    rc = 1 if failed_sources else 0

    if args.seed:
        mark_alerted(st, due, stamp)
        st["seeded"] = True
        _log(f"seeded: {len(st['reqs'])} reqs tracked, {len(due)} Fall-tagged recorded "
             f"WITHOUT alerting")
        for r in sorted(due, key=_order):
            _log(f"  seed: {r['company']} — {r['title']} · {r['location']} · {r['confidence']}")
        if not args.dry_run:
            save_state(path, st)
        return rc

    outbox: list[tuple[str, str, list[dict]]] = []      # (kind, text, rows)
    if due:
        outbox.append(("alert", format_alert(due), due))
    for name, m in health_msgs:
        outbox.append(("health:" + name, m, []))
    if args.heartbeat or heartbeat_due(st, now):
        n_fall = sum(1 for e in st["reqs"].values() if e.get("fall_flag"))
        ok_n = sum(1 for r in results.values() if r.ok)
        hb = (f"fall-watch alive: {ok_n}/{len(results)} sources ok, {n_fall} Fall-tagged "
              f"reqs tracked")
        if failed_sources:
            hb += f" (failing: {', '.join(failed_sources)})"
        outbox.append(("heartbeat", hb, []))

    if args.dry_run:
        for kind, text, _r in outbox:
            print(f"----- would send ({kind}) -----\n{text}\n")
        if not outbox:
            print("(nothing to send)")
        _log(f"dry run: {len(due)} alert item(s), {len(health_msgs)} health message(s); "
             f"state NOT written")
        return rc

    hour = now.hour
    send_failed = False
    for kind, text, alert_rows in outbox:
        if kind != "heartbeat" and _quiet(hour):
            # Nothing is marked, so the next daytime run sends it.
            _log(f"{kind}: holding until morning (it is {hour:02d}:00)")
            continue
        if send(text):
            if kind == "alert":
                mark_alerted(st, alert_rows, stamp)
                _log(f"alerted on {len(alert_rows)} Fall 2027 req(s)")
            elif kind.startswith("health:"):
                st["sources"][kind.split(":", 1)[1]]["down_alerted"] = "DOWN" in text
            elif kind == "heartbeat":
                st["last_heartbeat"] = stamp
        else:
            send_failed = True
            _log(f"🔴 send FAILED ({kind}) — not recorded, will retry next run")
    st["seeded"] = True
    save_state(path, st)
    return 1 if (send_failed or rc) else 0


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
