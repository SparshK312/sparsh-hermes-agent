#!/usr/bin/env python3
"""
ats_router.py — API-first job-description fetch for the curation engine.

The insight (validated by 3 deep-research reports + a live NVIDIA test): most
"JS-heavy" ATS boards are a JSON API behind the JS frontend. Hit the API directly
and you get the full JD as structured JSON — fast, free, unlimited concurrency, no
browser. A non-200 from the API == a dead/expired posting (auto-filterable).

Two entry points:
  - fetch_board(board)      -> [JobRecord]   board-level pull for one company
                                             (lane-1 brand-first sourcing)
  - fetch_jd_record(url)    -> JobRecord     single-URL enrich + dead-check
                                             (lane-2 + re-checking existing rows)

Supported ATS (board-level JSON): greenhouse, lever, ashby, workable, workday,
smartrecruiters. Oracle / iCIMS / custom (Tesla/Apple/Stripe) -> "manual":
no API, returned as a click-through record (ranked by brand, JD via plain-GET if
the page is server-rendered, else left for the user to open).

Parse gotchas baked in (from the research synthesis):
  - Greenhouse `content` is HTML-entity-escaped -> html.unescape before stripping.
  - Lever `descriptionPlain` drops the requirement bullets -> concat the `lists`.
  - Workday job DETAIL is a GET on /wday/cxs/.../job{externalPath}; the LISTING is
    a POST on /wday/cxs/.../jobs. JD lives in jobPostingInfo.jobDescription.
  - Oracle needs ora-irc-cx-userid + ora-irc-language headers (handled if used).

Stdlib + httpx + trafilatura. Async with a global semaphore and strict per-request
timeouts so a hung board can never blow the wall-clock.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

try:
    import trafilatura  # full-page extraction for the manual/plain-GET fallback
except ImportError:  # keep importable without it; fragments use the stdlib path
    trafilatura = None


# ── tunables ──────────────────────────────────────────────────────────────────
HTTP_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
CONCURRENCY = 16
MAX_JD_CHARS = 12_000          # store the real JD (user wants to read it); bound storage
MIN_USABLE_CHARS = 200         # below this after cleaning -> treat as no-JD
WORKDAY_PAGE = 20              # CXS listing page size
WORKDAY_MAX_PAGES = 5          # per search term (boards sort full-time first; search-driven)
WORKDAY_SEARCH_TERMS = ["intern", "co-op", "university", "student"]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
JSON_HEADERS = {"Accept": "application/json", "User-Agent": UA}

_JUNK_RE = re.compile(
    r"(enable javascript|please (enable|turn on) javascript|"
    r"accept (all )?cookies|cookie (policy|preferences|consent)|access denied|"
    r"human verification|are you a robot)",
    re.IGNORECASE,
)


# ── record ────────────────────────────────────────────────────────────────────
@dataclass
class JobRecord:
    title: str = ""
    location: str = ""
    url: str = ""                 # canonical posting/apply URL
    full_jd: str = ""             # cleaned plain-text job description
    posted_date: str = ""         # YYYY-MM-DD ("" if unknown)
    ats_type: str = ""
    req_id: str = ""
    dead: bool = False            # non-200 / pulled / expired
    error: str = ""               # populated when dead or fetch failed

    def has_jd(self) -> bool:
        return bool(self.full_jd) and len(self.full_jd) >= MIN_USABLE_CHARS


# ── HTML cleaning ─────────────────────────────────────────────────────────────
class _Stripper(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in ("p", "br", "li", "div", "h1", "h2", "h3", "ul", "ol", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if t:
                self.parts.append(t + " ")


def clean_fragment(s: str) -> str:
    """ATS JSON descriptions are HTML fragments (often entity-escaped). Unescape,
    strip tags, normalize whitespace. Greenhouse double-escapes — unescape twice."""
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    p = _Stripper()
    try:
        p.feed(s)
    except Exception:  # noqa: BLE001
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()
    text = "".join(p.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()[:MAX_JD_CHARS]


def clean_full_page(html_str: str, url: str = "") -> str:
    """Plain-GET fallback for server-rendered manual pages."""
    if not html_str:
        return ""
    if trafilatura is not None:
        try:
            out = trafilatura.extract(html_str, url=url or None, include_comments=False,
                                      include_tables=True, no_fallback=False)
            if out:
                return out.strip()[:MAX_JD_CHARS]
        except Exception:  # noqa: BLE001
            pass
    return clean_fragment(html_str)


def is_junk(text: str) -> bool:
    if not text or len(text) < MIN_USABLE_CHARS:
        return True
    return bool(_JUNK_RE.search(text[:500]))


# ── date helpers ──────────────────────────────────────────────────────────────
def _iso_to_date(s: str) -> str:
    if not s:
        return ""
    try:
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:  # noqa: BLE001
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
        return m.group(1) if m else ""


def _epoch_ms_to_date(v) -> str:
    try:
        return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).date().isoformat()
    except Exception:  # noqa: BLE001
        return ""


def _posted_ago_to_date(s: str) -> str:
    """Workday 'Posted 24 Days Ago' / 'Posted Today' / 'Posted Yesterday'."""
    if not s:
        return ""
    low = s.lower()
    today = datetime.now(timezone.utc).date()
    if "today" in low:
        return today.isoformat()
    if "yesterday" in low:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s*day", low)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\s*month", low)
    if m:
        return (today - timedelta(days=int(m.group(1)) * 30)).isoformat()
    return ""


# ── ATS detection ─────────────────────────────────────────────────────────────
def detect_ats(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    if "myworkdayjobs.com" in host:
        return "workday"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if "workable.com" in host:
        return "workable"
    if "oraclecloud.com" in host or ".fa." in host:
        return "oracle"
    if "icims.com" in host:
        return "icims"
    return "manual"


# ── low-level fetch ───────────────────────────────────────────────────────────
async def _get_json(client: httpx.AsyncClient, url: str, headers=None):
    r = await client.get(url, headers=headers or JSON_HEADERS, follow_redirects=True)
    if r.status_code != 200:
        return None, r.status_code
    try:
        return r.json(), 200
    except Exception:  # noqa: BLE001
        return None, r.status_code


async def _post_json(client: httpx.AsyncClient, url: str, body: dict, headers=None):
    h = dict(headers or JSON_HEADERS)
    h["Content-Type"] = "application/json"
    r = await client.post(url, json=body, headers=h, follow_redirects=True)
    if r.status_code != 200:
        return None, r.status_code
    try:
        return r.json(), 200
    except Exception:  # noqa: BLE001
        return None, r.status_code


# ── Greenhouse location repair ────────────────────────────────────────────────
# Some Greenhouse boards put the WORK MODEL in `location.name` ("Hybrid",
# "In-Office", "Flexible - Any SpaceX Site") and keep the actual city in
# `offices[]`. classify_location() then finds no geography and HARD-REJECTS the
# posting, so it is dropped silently — not logged, not flagged, just absent.
# Measured 2026-08-20 over 11,529 live postings: cloudflare 299/304 postings,
# samsara 184/263, affirm 137/201. Both SpaceX 2027 SWE internships were
# invisible this way.
#
# Two traps this implementation exists to avoid:
#   1. classify_location matches bare "Remote" by EXACT-SET membership, so
#      appending anything to it destroys the match. Merging only when the string
#      STARTS with a work-model token, and skipping office values that are
#      themselves work models, keeps "Remote" intact.
#   2. A blanket merge admits foreign roles. Positives run before negatives in
#      classify_location, so an all-foreign office list still rejects, while a
#      mixed list correctly matches on its US/CA entry.
# Net over the full corpus: +492 admitted, 1 regression (Verkada
# "Remote" + "France Remote", which is a correct rejection).
_WORK_MODEL = re.compile(r"^(hybrid|remote|distributed|flexible|on-?site|in-?office|"
                         r"anywhere|multiple locations?|various)\b", re.I)


def _merge_offices(loc: str, offices) -> str:
    """Append real geography from offices[] when location.name is a work model."""
    loc = (loc or "").strip()
    if not _WORK_MODEL.match(loc):
        return loc
    seen, parts = set(), []
    for o in offices or []:
        v = ((o or {}).get("location") or (o or {}).get("name") or "").strip()
        k = v.lower()
        if v and k not in seen and k != loc.lower() and not _WORK_MODEL.fullmatch(v):
            seen.add(k)
            parts.append(v)
    # Append rather than replace: the work model is real signal downstream.
    return f"{loc} — {'; '.join(parts)}" if parts else loc


# ── per-ATS board fetchers ────────────────────────────────────────────────────
async def _board_greenhouse(client, board) -> list[JobRecord]:
    token = board["token"]
    data, code = await _get_json(
        client, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    if not data:
        return []
    out = []
    for j in data.get("jobs", []):
        out.append(JobRecord(
            title=j.get("title", ""),
            location=_merge_offices((j.get("location") or {}).get("name", ""), j.get("offices")),
            url=j.get("absolute_url", ""),
            full_jd=clean_fragment(j.get("content", "")),
            # first_published, NOT updated_at: a re-touched req is not a fresh one.
            # Median gap 49 days, p90 258, max 2,681 across 11,528 live postings —
            # and age_days<=7 drives both the apply-now verdict and hotness, so
            # preferring updated_at systematically overstates freshness board-wide.
            posted_date=_iso_to_date(j.get("first_published") or j.get("updated_at") or ""),
            ats_type="greenhouse",
            req_id=str(j.get("id", "")),
        ))
    return out


async def _board_ashby(client, board) -> list[JobRecord]:
    org = board["org"]
    data, code = await _get_json(
        client, f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true")
    if not data:
        return []
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location") or ""
        if not loc and j.get("address"):
            loc = (j.get("address") or {}).get("postalAddress", {}).get("addressLocality", "")
        loc = loc or j.get("locationName", "")
        # secondaryLocations[] holds additional real geographies (objects, not
        # strings). Ignoring them means a role labelled "Canada" but ALSO
        # US-eligible reads Canada-only. 42 live rows are misclassified this way;
        # Notion and Replit are both Ashby.
        for sec in j.get("secondaryLocations") or []:
            sv = ((sec or {}).get("location") or "").strip()
            if not sv:
                sv = (((sec or {}).get("address") or {})
                      .get("postalAddress", {}).get("addressLocality", "") or "").strip()
            if sv and sv.lower() not in loc.lower():
                loc = f"{loc}; {sv}" if loc else sv
        out.append(JobRecord(
            title=j.get("title", ""),
            location=loc,
            url=j.get("jobUrl") or j.get("applyUrl", ""),
            full_jd=clean_fragment(j.get("descriptionHtml") or j.get("descriptionPlain", "")),
            posted_date=_iso_to_date(j.get("publishedAt") or j.get("updatedAt") or ""),
            ats_type="ashby",
            req_id=str(j.get("id", "")),
        ))
    return out


async def _board_workable(client, board) -> list[JobRecord]:
    account = board["account"]
    data, code = await _get_json(
        client, f"https://apply.workable.com/api/v1/widget/accounts/{account}?details=true")
    if not data:
        return []
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location") or {}
        loc_str = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x) \
            if isinstance(loc, dict) else str(loc)
        out.append(JobRecord(
            title=j.get("title", ""),
            location=loc_str,
            url=j.get("url") or j.get("application_url", ""),
            full_jd=clean_fragment(j.get("description", "") + " " + j.get("requirements", "")),
            posted_date=_iso_to_date(j.get("published_on") or j.get("created_at") or ""),
            ats_type="workable",
            req_id=str(j.get("shortcode") or j.get("id", "")),
        ))
    return out


def _workday_base(board) -> tuple[str, str, str]:
    """Return (origin, tenant, site) from a workday board config."""
    host = board["host"]                       # e.g. nvidia.wd5.myworkdayjobs.com
    tenant = host.split(".")[0]
    site = board["site"]
    return f"https://{host}", tenant, site


async def _board_workday(client, board, prefilter=None) -> list[JobRecord]:
    origin, tenant, site = _workday_base(board)
    list_url = f"{origin}/wday/cxs/{tenant}/{site}/jobs"
    headers = {**JSON_HEADERS, "Referer": f"{origin}/{site}"}
    # 1) SEARCH the listing per intern-term (boards sort full-time first, so a blank
    #    paginate would need ~100 pages). Prefilter on (title, location) BEFORE the
    #    expensive per-job detail calls so we don't fetch hundreds of intl roles.
    candidates: dict[str, dict] = {}
    terms = board.get("search_terms", WORKDAY_SEARCH_TERMS)
    for term in terms:
        for page in range(WORKDAY_MAX_PAGES):
            body = {"appliedFacets": {}, "limit": WORKDAY_PAGE,
                    "offset": page * WORKDAY_PAGE, "searchText": term}
            data, code = await _post_json(client, list_url, body, headers)
            if not data:
                break
            postings = data.get("jobPostings", [])
            if not postings:
                break
            for jp in postings:
                if prefilter and not prefilter(jp.get("title", ""), jp.get("locationsText", "")):
                    continue
                ext = jp.get("externalPath", "")
                if ext and ext not in candidates:
                    candidates[ext] = jp
            if (page + 1) * WORKDAY_PAGE >= data.get("total", 0):
                break
    candidates = list(candidates.values())
    # 2) GET detail for each candidate (this is where the real JD lives)
    async def _detail(jp):
        ext = jp.get("externalPath", "")
        if not ext:
            return None
        # externalPath already begins with "/job/..." — do NOT prepend another /job
        durl = f"{origin}/wday/cxs/{tenant}/{site}{ext}"
        data, code = await _get_json(client, durl, headers)
        if not data:
            return None
        info = data.get("jobPostingInfo", {})
        return JobRecord(
            title=info.get("title") or jp.get("title", ""),
            location=info.get("location") or jp.get("locationsText", ""),
            url=info.get("externalUrl") or f"{origin}/{site}{ext}",
            full_jd=clean_fragment(info.get("jobDescription", "")),
            posted_date=_iso_to_date(info.get("startDate", "")) or _posted_ago_to_date(info.get("postedOn", "")),
            ats_type="workday",
            req_id=info.get("jobReqId") or "",
        )
    results = await asyncio.gather(*[_detail(jp) for jp in candidates])
    return [r for r in results if r]


def _sr_loc(p) -> str:
    loc = p.get("location", {}) or {}
    return ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)


async def _board_smartrecruiters(client, board, prefilter=None) -> list[JobRecord]:
    company = board["company"]
    # 1) page the listing, prefilter on (title, location) BEFORE detail calls
    summaries = []
    offset = 0
    for _ in range(10):
        data, code = await _get_json(
            client, f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&offset={offset}")
        if not data:
            break
        content = data.get("content", [])
        if not content:
            break
        for p in content:
            if prefilter and not prefilter(p.get("name", ""), _sr_loc(p)):
                continue
            summaries.append(p)
        offset += 100
        if offset >= data.get("totalFound", 0):
            break

    # 2) fetch the (now small) detail set concurrently
    async def _detail(p):
        pid = p.get("id", "")
        d2, _ = await _get_json(
            client, f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{pid}")
        jd = ""
        if d2:
            secs = (d2.get("jobAd") or {}).get("sections") or {}
            jd = " ".join(clean_fragment((secs.get(k) or {}).get("text", ""))
                          for k in ("companyDescription", "jobDescription",
                                    "qualifications", "additionalInformation"))
        return JobRecord(
            title=p.get("name", ""), location=_sr_loc(p),
            url=p.get("applyUrl") or p.get("ref", ""),
            full_jd=jd.strip(), posted_date=_iso_to_date(p.get("releasedDate", "")),
            ats_type="smartrecruiters", req_id=pid,
        )
    return list(await asyncio.gather(*[_detail(p) for p in summaries]))


# fix lever (clean implementation; the stub above is replaced at call time)
async def _board_lever_impl(client, board) -> list[JobRecord]:
    site = board["site"]
    r = await client.get(f"https://api.lever.co/v0/postings/{site}?mode=json",
                         headers=JSON_HEADERS, follow_redirects=True)
    if r.status_code != 200:
        return []
    try:
        jobs = r.json()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for j in jobs:
        cats = j.get("categories") or {}
        jd = j.get("descriptionPlain", "")
        for lst in j.get("lists", []):
            jd += "\n\n" + clean_fragment(lst.get("text", "")) + "\n" + clean_fragment(lst.get("content", ""))
        out.append(JobRecord(
            title=j.get("text", ""),
            location=cats.get("location", ""),
            url=j.get("hostedUrl") or j.get("applyUrl", ""),
            full_jd=jd.strip()[:MAX_JD_CHARS],
            posted_date=_epoch_ms_to_date(j.get("createdAt")),
            ats_type="lever",
            req_id=str(j.get("id", "")),
        ))
    return out


async def _board_amazon(client, board) -> list[JobRecord]:
    """amazon.jobs search.json — public JSON search. Drive with intern queries,
    union + dedup by job_path. Rich payload (quals + description)."""
    queries = board.get("queries", ["software engineer intern", "software dev engineer intern",
                                    "machine learning intern", "data engineer intern",
                                    "product manager intern"])
    out: dict[str, JobRecord] = {}
    base = "https://www.amazon.jobs/en/search.json"
    for q in queries:
        url = (f"{base}?base_query={q.replace(' ', '+')}&result_limit=100&sort=recent"
               f"&country%5B%5D=USA&country%5B%5D=CAN")
        data, code = await _get_json(client, url, {**JSON_HEADERS, "User-Agent": UA})
        if not data:
            continue
        for j in data.get("jobs", []):
            path = j.get("job_path", "")
            if not path or path in out:
                continue
            jd = " ".join(clean_fragment(j.get(k, "")) for k in
                          ("description_short", "basic_qualifications", "preferred_qualifications"))
            out[path] = JobRecord(
                title=j.get("title", ""), location=j.get("location") or j.get("normalized_location", ""),
                url="https://www.amazon.jobs" + path, full_jd=jd.strip(),
                posted_date=_amazon_date(j.get("posted_date", "")),
                ats_type="amazon", req_id=str(j.get("id_icims") or j.get("id", "")),
            )
    return list(out.values())


def _amazon_date(s: str) -> str:
    # "May  6, 2026"
    if not s:
        return ""
    try:
        return datetime.strptime(re.sub(r"\s+", " ", s.strip()), "%B %d, %Y").date().isoformat()
    except Exception:  # noqa: BLE001
        return ""


_BOARD_FETCHERS = {
    "greenhouse": _board_greenhouse,
    "lever": _board_lever_impl,
    "ashby": _board_ashby,
    "workable": _board_workable,
    "workday": _board_workday,
    "smartrecruiters": _board_smartrecruiters,
    "amazon": _board_amazon,
}


# ── public API ────────────────────────────────────────────────────────────────
# word-boundary match so "intern" doesn't fire on "INTERNal"/"INTERNational"
_INTERN_RE = re.compile(
    r"\b(intern(ship)?s?|co-?op|new\s+grad(uate)?|university\s+grad|student)\b", re.I)
# seniority/full-time markers that disqualify an "intern"-ish title
_SENIOR_RE = re.compile(r"\b(senior|sr\.?|staff|principal|director|vp|head\s+of)\b", re.I)


def default_intern_filter(title: str) -> bool:
    t = title or ""
    if _SENIOR_RE.search(t):
        return False
    return bool(_INTERN_RE.search(t))


def _default_prefilter(title: str, location: str = "") -> bool:
    return default_intern_filter(title)


async def fetch_board(client, board: dict, prefilter=None) -> list[JobRecord]:
    """Pull all postings for one company board. Workday/SmartRecruiters accept a
    (title, location) prefilter applied at the LISTING level — before the expensive
    per-job detail calls — to cap how many JDs we fetch (defaults to interns only)."""
    ats = board.get("ats_type")
    fetcher = _BOARD_FETCHERS.get(ats)
    if not fetcher:
        return []
    try:
        if ats in ("workday", "smartrecruiters"):
            return await fetcher(client, board, prefilter or _default_prefilter)
        return await fetcher(client, board)
    except Exception as e:  # noqa: BLE001
        print(f"[ats_router] board {board.get('name')} ({ats}) failed: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return []


async def fetch_jd_record(client, url: str) -> JobRecord:
    """Single-URL enrich + dead-check. Non-200 from the ATS API -> dead=True."""
    ats = detect_ats(url)
    try:
        if ats == "workday":
            rec = await _single_workday(client, url)
        elif ats == "greenhouse":
            rec = await _single_greenhouse(client, url)
        elif ats == "ashby":
            rec = await _single_ashby(client, url)
        elif ats == "lever":
            rec = await _single_lever(client, url)
        elif ats == "workable":
            rec = await _single_workable(client, url)
        elif ats == "smartrecruiters":
            rec = await _single_smartrecruiters(client, url)
        elif ats == "icims":
            rec = await _single_icims(client, url)
        else:
            rec = await _single_manual(client, url, ats)
    except Exception as e:  # noqa: BLE001
        return JobRecord(url=url, ats_type=ats, error=f"{type(e).__name__}: {e}")
    rec.url = rec.url or url
    rec.ats_type = ats
    return rec


async def _single_workday(client, url) -> JobRecord:
    p = urlparse(url)
    host = p.netloc
    tenant = host.split(".")[0]
    parts = [x for x in p.path.split("/") if x]
    # drop optional locale (en-US) leading segment
    if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
        parts = parts[1:]
    if not parts or "job" not in parts:
        return JobRecord(url=url, error="unparseable workday url")
    site = parts[0]
    ji = parts.index("job")
    jobpath = "/" + "/".join(parts[ji:])               # /job/US-CA-.../slug_REQID
    durl = f"https://{host}/wday/cxs/{tenant}/{site}{jobpath}"
    data, code = await _get_json(client, durl, {**JSON_HEADERS, "Referer": f"https://{host}/{site}"})
    if not data:
        return JobRecord(url=url, dead=True, error=f"workday api {code}")
    info = data.get("jobPostingInfo", {})
    return JobRecord(
        title=info.get("title", ""), location=info.get("location", ""),
        url=info.get("externalUrl") or url,
        full_jd=clean_fragment(info.get("jobDescription", "")),
        posted_date=_iso_to_date(info.get("startDate", "")) or _posted_ago_to_date(info.get("postedOn", "")),
        req_id=info.get("jobReqId", ""),
    )


async def _single_greenhouse(client, url) -> JobRecord:
    parts = [x for x in urlparse(url).path.split("/") if x]
    token = job_id = ""
    if "jobs" in parts:
        ji = parts.index("jobs")
        token = parts[ji - 1] if ji >= 1 else ""
        job_id = parts[ji + 1] if ji + 1 < len(parts) else ""
    if not (token and job_id):
        return await _single_manual(client, url, "greenhouse")
    data, code = await _get_json(
        client, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}")
    if not data:
        return JobRecord(url=url, dead=True, error=f"greenhouse api {code}")
    # SAME two repairs as _board_greenhouse. This is the path most wide-net
    # Greenhouse rows take (via fetch_jd_record), so fixing only the board
    # fetcher leaves the store split-brain: brand-board rows get true ages and
    # merged locations while wide-net rows keep inflated freshness and
    # work-model locations. The single-job endpoint returns both `offices` and
    # `first_published` — verified live on Cloudflare 7902104
    # (first_published 2026-08-03 vs updated_at 2026-08-06).
    return JobRecord(
        title=data.get("title", ""),
        location=_merge_offices((data.get("location") or {}).get("name", ""), data.get("offices")),
        url=data.get("absolute_url") or url, full_jd=clean_fragment(data.get("content", "")),
        posted_date=_iso_to_date(data.get("first_published") or data.get("updated_at") or ""),
        req_id=str(data.get("id", "")),
    )


async def _single_ashby(client, url) -> JobRecord:
    # strip trailing /application or /apply so the posting id is the last segment
    parts = [x for x in urlparse(url).path.split("/") if x and x not in ("application", "apply")]
    if len(parts) < 2:
        return await _single_manual(client, url, "ashby")
    org, job_id = parts[0], parts[1]            # /{org}/{uuid}[/application]
    recs = await _board_ashby(client, {"org": org})
    for r in recs:
        if job_id and job_id in (r.url or ""):
            return r
    return JobRecord(url=url, dead=not recs, error="ashby posting not found on board")


async def _single_lever(client, url) -> JobRecord:
    parts = [x for x in urlparse(url).path.split("/") if x]
    if len(parts) < 2:
        return await _single_manual(client, url, "lever")
    site, pid = parts[0], parts[1]
    data, code = await _get_json(client, f"https://api.lever.co/v0/postings/{site}/{pid}")
    if not data:
        return JobRecord(url=url, dead=True, error=f"lever api {code}")
    jd = data.get("descriptionPlain", "")
    for lst in data.get("lists", []):
        jd += "\n\n" + clean_fragment(lst.get("text", "")) + "\n" + clean_fragment(lst.get("content", ""))
    cats = data.get("categories") or {}
    return JobRecord(
        title=data.get("text", ""), location=cats.get("location", ""),
        url=data.get("hostedUrl") or url, full_jd=jd.strip()[:MAX_JD_CHARS],
        posted_date=_epoch_ms_to_date(data.get("createdAt")), req_id=str(data.get("id", "")),
    )


# ── schema.org JobPosting ────────────────────────────────────────────────────
# Found 2026-08-28 after two Netflix roles sat at #1 and #2 on the board with fit 82
# and 85 — scored, it turned out, on the page's CSS THEME CONFIG. The generic text
# extractor had grabbed a `{"themeOptions": {"customFonts": ...}}` island, truncated it
# at 12,000 chars, and stored that as the job description. Both roles are PhD-ONLY; the
# model never saw the word "PhD" because the requirement was never in the data.
#
# The real description was sitting in a <script type="application/ld+json"> JobPosting
# block the whole time. That is a schema.org standard Google requires for job-search
# indexing, so most career sites emit it — which makes this a general win, not a
# Netflix patch. Try it FIRST on any page, before falling back to prose extraction.
_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def _ldjson_jobposting(html_text: str) -> tuple[str, str, str] | None:
    """(title, description, iso_date) from a schema.org JobPosting, or None."""
    for blk in _LDJSON_RE.findall(html_text or ""):
        try:
            data = json.loads(blk.strip())
        except Exception:  # noqa: BLE001
            continue
        # may be a single object, a list, or an @graph wrapper
        cands = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            cands = data["@graph"]
        for d in cands:
            if not isinstance(d, dict):
                continue
            t = d.get("@type")
            types = t if isinstance(t, list) else [t]
            if "JobPosting" not in types:
                continue
            desc = clean_fragment(d.get("description") or "")
            if len(desc) < 400:
                continue
            return (str(d.get("title") or ""), desc,
                    _iso_to_date(str(d.get("datePosted") or "")))
    return None


def _looks_like_config_blob(text: str) -> bool:
    """True when 'text' is a JSON/config island rather than prose.

    The guard that would have caught the Netflix bug at write time. A job description
    is sentences; a theme config is quoted keys. Never store the latter as a JD.
    """
    s = (text or "").lstrip()
    if not s:
        return False
    if s[:1] in "{[":
        return True
    head = s[:4000]
    return len(re.findall(r'"[A-Za-z_][\w\-]*":', head)) > 25


# ── TLS-fingerprint fallback ─────────────────────────────────────────────────
# Some ATS edges (iCIMS behind AWS WAF, several company career sites) block on the
# TLS/JA3 fingerprint, NOT on headers or the User-Agent. Proof, 2026-08-28: the same
# iCIMS URL returns 200 to curl and 405 "Human Verification" to httpx with byte-identical
# headers. curl_cffi replays a real Chrome ClientHello and gets through.
#
# It is a FALLBACK, never the default: httpx is faster, async, and already works for
# every ATS with a JSON API. This only runs when the normal path came back blocked or junk.
# ⚠️ It is also not magic — Tesla sits behind Akamai Bot Manager and returns a JS
# challenge page (2.5 KB, `sec-if-cpt-container`) with a 200, which no TLS trick solves.
# That needs a real browser.
_IMPERSONATE = "chrome124"


async def _impersonate_get(url: str, timeout: int = 25) -> tuple[int, str]:
    """Blocking curl_cffi GET pushed to a thread so the async harvest is never stalled."""
    try:
        from curl_cffi import requests as _cr
    except ImportError:
        return 0, ""

    def _go():
        r = _cr.get(url, impersonate=_IMPERSONATE, timeout=timeout,
                    headers={"Accept-Language": "en-US,en;q=0.9"})
        return r.status_code, r.text
    try:
        return await asyncio.to_thread(_go)
    except Exception:  # noqa: BLE001
        return 0, ""


async def _single_workable(client, url) -> JobRecord:
    """apply.workable.com/{account}/j/{shortcode} -> the public per-job API.

    The board fetcher uses the *widget* endpoint, which only covers accounts we
    already track. A wide-net row is a bare posting URL, so it needs the per-job
    endpoint. Verified 2026-08-28: the page HTML is a 7.6 KB JS shell with no JD,
    while /api/v1/accounts/{a}/jobs/{c} returns description + requirements +
    benefits (~4.6 KB of real text). No browser required.
    """
    parts = [x for x in urlparse(url).path.split("/") if x]
    if len(parts) < 3 or parts[1] != "j":
        return await _single_manual(client, url, "workable")
    account, shortcode = parts[0], parts[2]
    data, code = await _get_json(
        client, f"https://apply.workable.com/api/v1/accounts/{account}/jobs/{shortcode}")
    if not data:
        return JobRecord(url=url, dead=code in (404, 410), error=f"workable api {code}")
    loc = data.get("location") or {}
    loc_str = ", ".join(x for x in [loc.get("city"), loc.get("region"),
                                    loc.get("country")] if x) if isinstance(loc, dict) else str(loc)
    jd = " ".join(clean_fragment(data.get(k) or "")
                  for k in ("description", "requirements", "benefits"))
    return JobRecord(
        title=data.get("title", ""), location=loc_str, url=url,
        full_jd=jd.strip()[:MAX_JD_CHARS],
        posted_date=_iso_to_date(data.get("published") or data.get("created_at") or ""),
        req_id=str(data.get("shortcode") or data.get("id", "")),
    )


async def _single_smartrecruiters(client, url) -> JobRecord:
    """jobs.smartrecruiters.com/{Company}/{postingId}-{slug} -> the public API.

    The posting id is the numeric prefix of the last path segment. Same section
    concatenation as the board fetcher so both paths produce identical JD text.
    """
    parts = [x for x in urlparse(url).path.split("/") if x]
    if len(parts) < 2:
        return await _single_manual(client, url, "smartrecruiters")
    company, last = parts[0], parts[-1]
    m = re.match(r"(\d+)", last)
    if not m:
        return await _single_manual(client, url, "smartrecruiters")
    pid = m.group(1)
    data, code = await _get_json(
        client, f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{pid}")
    if not data:
        return JobRecord(url=url, dead=code in (404, 410), error=f"smartrecruiters api {code}")
    secs = (data.get("jobAd") or {}).get("sections") or {}
    jd = " ".join(clean_fragment((secs.get(k) or {}).get("text", ""))
                  for k in ("companyDescription", "jobDescription",
                            "qualifications", "additionalInformation"))
    return JobRecord(
        title=data.get("name", ""), location=_sr_loc(data), url=url,
        full_jd=jd.strip()[:MAX_JD_CHARS],
        posted_date=_iso_to_date(data.get("releasedDate", "")), req_id=pid,
    )


async def _single_icims(client, url) -> JobRecord:
    """iCIMS serves the JD inside an iframe, so a plain GET of the posting URL
    returns a 92 KB shell of chrome and scripts with none of the description in it.

    `?in_iframe=1` renders the same posting WITHOUT the outer frame — verified
    2026-08-28: 36 KB containing Overview / Responsibilities / Qualifications /
    Requirements. This is a URL parameter, not a browser. 10 live roles were
    unscoreable purely for want of it.
    """
    sep = "&" if urlparse(url).query else "?"
    framed = f"{url}{sep}in_iframe=1"
    try:
        # httpx is reliably WAF-blocked here (405 "Human Verification"), so skip it.
        code, body = await _impersonate_get(framed)
        if code != 200 or not body:
            return JobRecord(url=url, ats_type="icims", dead=code in (404, 410),
                             error=f"icims http {code}")
        text = clean_full_page(body, url)
        if is_junk(text):
            return JobRecord(url=url, ats_type="icims", error="icims iframe still junk")
        return JobRecord(url=url, ats_type="icims", full_jd=text[:MAX_JD_CHARS])
    except Exception as e:  # noqa: BLE001
        return JobRecord(url=url, ats_type="icims", error=f"{type(e).__name__}: {e}")


async def _single_manual(client, url, ats) -> JobRecord:
    """No usable API — try a plain GET + trafilatura (works on server-rendered
    pages like Amazon/SmartRecruiters/Taleo); otherwise leave JD empty for the
    user to click through."""
    try:
        text, code, title, posted = "", 0, "", ""
        try:
            r = await client.get(url, headers={"User-Agent": UA}, follow_redirects=True)
            code = r.status_code
            if code == 200:
                ld = _ldjson_jobposting(r.text)
                if ld:
                    title, text, posted = ld
                else:
                    text = clean_full_page(r.text, url)
        except Exception:  # noqa: BLE001 - fall through to the impersonating client
            pass
        # Retry through a real-Chrome TLS fingerprint when we were blocked OR got junk.
        # 403/405/429 are the block codes; junk means we got a challenge/cookie wall.
        if code != 200 or not text or is_junk(text):
            c2, body = await _impersonate_get(url)
            if c2 == 200 and body:
                ld = _ldjson_jobposting(body)
                if ld:
                    return JobRecord(url=url, ats_type=ats, title=ld[0],
                                     full_jd=ld[1][:MAX_JD_CHARS], posted_date=ld[2])
                t2 = clean_full_page(body, url)
                if t2 and not is_junk(t2) and not _looks_like_config_blob(t2):
                    return JobRecord(url=url, ats_type=ats, full_jd=t2[:MAX_JD_CHARS])
                text, code = t2 or text, c2
            elif c2:
                code = code or c2
        if code not in (200, 0) and not text:
            return JobRecord(url=url, ats_type=ats, dead=code in (404, 410),
                             error=f"http {code}")
        if not text or is_junk(text):
            return JobRecord(url=url, ats_type=ats, error="needs render / manual")
        if _looks_like_config_blob(text):
            # Better to show 👀 than to score a role on its stylesheet.
            return JobRecord(url=url, ats_type=ats, error="extracted config blob, not a JD")
        return JobRecord(url=url, ats_type=ats, title=title,
                         full_jd=text[:MAX_JD_CHARS], posted_date=posted)
    except Exception as e:  # noqa: BLE001
        return JobRecord(url=url, ats_type=ats, error=f"{type(e).__name__}: {e}")


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT, http2=True,
                             limits=httpx.Limits(max_connections=CONCURRENCY))


# ── smoke test ────────────────────────────────────────────────────────────────
async def _smoke():
    boards = [
        {"name": "Cerebras", "ats_type": "greenhouse", "token": "earlytalentcerebras"},
        {"name": "1Password", "ats_type": "ashby", "org": "1password"},
        {"name": "NVIDIA", "ats_type": "workday", "host": "nvidia.wd5.myworkdayjobs.com",
         "site": "NVIDIAExternalCareerSite"},
    ]
    async with make_client() as client:
        for b in boards:
            recs = await fetch_board(client, b)
            interns = [r for r in recs if default_intern_filter(r.title)]
            print(f"\n=== {b['name']} ({b['ats_type']}): {len(recs)} roles, "
                  f"{len(interns)} intern-ish ===")
            for r in interns[:3]:
                print(f"  • {r.title[:60]:60} | {r.location[:24]:24} | "
                      f"{r.posted_date or '?':10} | JD {len(r.full_jd)} chars")
        # single-URL dead-check on the known dead NVIDIA req
        dead = await fetch_jd_record(client,
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Cloud-Software-Intern--GeForce-NOW---Fall-2026_JR2019414")
        print(f"\ndead-check: dead={dead.dead} err={dead.error!r}")


if __name__ == "__main__":
    asyncio.run(_smoke())
