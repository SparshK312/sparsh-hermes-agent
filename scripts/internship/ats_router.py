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
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""),
            full_jd=clean_fragment(j.get("content", "")),
            posted_date=_iso_to_date(j.get("updated_at") or j.get("first_published") or ""),
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
        out.append(JobRecord(
            title=j.get("title", ""),
            location=loc or j.get("locationName", ""),
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
    return JobRecord(
        title=data.get("title", ""), location=(data.get("location") or {}).get("name", ""),
        url=data.get("absolute_url") or url, full_jd=clean_fragment(data.get("content", "")),
        posted_date=_iso_to_date(data.get("updated_at", "")), req_id=str(data.get("id", "")),
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


async def _single_manual(client, url, ats) -> JobRecord:
    """No usable API — try a plain GET + trafilatura (works on server-rendered
    pages like Amazon/SmartRecruiters/Taleo); otherwise leave JD empty for the
    user to click through."""
    try:
        r = await client.get(url, headers={"User-Agent": UA}, follow_redirects=True)
        if r.status_code != 200:
            return JobRecord(url=url, ats_type=ats, dead=True, error=f"http {r.status_code}")
        text = clean_full_page(r.text, url)
        if is_junk(text):
            return JobRecord(url=url, ats_type=ats, error="needs render / manual")
        return JobRecord(url=url, ats_type=ats, full_jd=text)
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
