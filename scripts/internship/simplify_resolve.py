#!/usr/bin/env python3
"""
simplify_resolve.py — turn a `simplify.jobs/p/<uuid>` link into the employer's ATS URL,
the real location and the real posted date.

WHY (2026-09-12). The SWElist newsletter is the ONLY feed that is not a subset of the
SimplifyJobs repo: measured over 16 digests (Aug 27 - Sep 11, 1,460 postings), just 42%
of its links appear in the repo's main README and 74% with the off-season README, and
fresh digests carry Tesla and Microsoft reqs the README never lists. So it has to stay a
source — but every link it carries is a simplify.jobs/p/<uuid> rewrite with no location,
which means (a) ats_router cannot JD-enrich it, and (b) the (company, title, location)
dedup can never match it to the same req arriving from the repo or a brand board. That is
where the board's duplicate rows come from: 12 of 15 Tesla queue rows on 2026-09-08 were
Simplify-UUID re-pickups of reqs he had already applied to.

The simplify.jobs posting page is server-rendered (Next.js) and carries the location and
a click-through URL that 307-redirects to the employer's posting. Two small requests,
cached forever by uuid (a posting's ATS URL does not change), bounded per run.
Stdlib only, synchronous — it runs inside wide_net_source._gather_postings(), which is
already synchronous (urllib/imaplib).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import store_paths

UUID_RE = re.compile(r"simplify\.jobs/p/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
TIMEOUT = 10
# Per-run bound on NEW resolutions (cache hits are free). SWElist contributes ~9 in-lane
# tier-S/A/B rows a day, so this clears a 12-day backlog on the first run and is idle after.
MAX_RESOLVES_PER_RUN = 120
# Aggregator hosts that are NOT an employer: a redirect landing on one of these is not a
# resolution, it is another hop we do not follow.
_NON_EMPLOYER_HOSTS = ("simplify.jobs", "jobright.ai", "swelist.com", "linkedin.com",
                       "indeed.com", "glassdoor.com")

_CACHE: dict | None = None
_resolved_this_run = 0
# Filled per run so a caller can log what happened (counts, not rows).
STATS = {"cache_hit": 0, "resolved": 0, "failed": 0, "budget_exhausted": 0}


def cache_path() -> Path:
    env = os.environ.get("SIMPLIFY_CACHE")
    if env:
        return Path(env)
    return store_paths.store_path(warn=False).parent / "simplify_resolve_cache.json"


def _load_cache() -> dict:
    global _CACHE
    if _CACHE is None:
        p = cache_path()
        try:
            _CACHE = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (OSError, json.JSONDecodeError):
            _CACHE = {}
        if not isinstance(_CACHE, dict):
            _CACHE = {}
    return _CACHE


def save_cache() -> None:
    if _CACHE is None:
        return
    p = cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_CACHE, ensure_ascii=False, indent=0), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        print(f"[simplify-resolve] cache not saved: {e}", file=sys.stderr)


def reset_run() -> None:
    """Call once at the start of a harvest so the per-run budget and STATS restart."""
    global _resolved_this_run
    _resolved_this_run = 0
    for k in STATS:
        STATS[k] = 0


def uuid_of(url: str) -> str:
    m = UUID_RE.search(url or "")
    return m.group(1).lower() if m else ""


def parse_page(html_text: str) -> dict:
    """Pure: extract {click_url, location, posted_date, company, title} from the
    server-rendered Simplify posting page. Empty dict when the page is not one."""
    m = _NEXT_DATA_RE.search(html_text or "")
    if not m:
        return {}
    try:
        jp = json.loads(m.group(1))["props"]["pageProps"]["jobPosting"]
    except (ValueError, KeyError, TypeError):
        return {}
    locs = []
    for loc in jp.get("locations") or []:
        v = ((loc or {}).get("value") or "").strip()
        if v and v not in locs:
            locs.append(v)
    posted = (jp.get("start_date") or "")[:10]
    return {
        "click_url": jp.get("url") or "",
        "location": "; ".join(locs),
        "posted_date": posted if re.match(r"\d{4}-\d{2}-\d{2}$", posted) else "",
        "company": ((jp.get("job") or {}).get("company") or {}).get("name") or "",
        "title": jp.get("title") or "",
    }


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _redirect_target(url: str) -> str:
    """One hop only: the Location header of the click URL, without following it."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        opener.open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            return e.headers.get("Location", "") or ""
        return ""
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _is_employer_url(url: str) -> bool:
    host = urlparse(url or "").netloc.lower()
    return bool(host) and not any(h in host for h in _NON_EMPLOYER_HOSTS)


def resolve(url: str) -> dict | None:
    """Resolve a simplify.jobs/p/<uuid> URL. Returns
    {url, location, posted_date, company, title} or None (not a Simplify link, budget
    spent, or the page/redirect could not be read). Never raises."""
    global _resolved_this_run
    uid = uuid_of(url)
    if not uid:
        return None
    cache = _load_cache()
    hit = cache.get(uid)
    if hit is not None:
        STATS["cache_hit"] += 1
        return hit or None            # a cached failure is stored as {} -> None
    if _resolved_this_run >= MAX_RESOLVES_PER_RUN:
        STATS["budget_exhausted"] += 1
        return None
    _resolved_this_run += 1
    try:
        info = parse_page(_get(f"https://simplify.jobs/p/{uid}"))
        target = _redirect_target(info["click_url"]) if info.get("click_url") else ""
        if not info or not _is_employer_url(target):
            raise ValueError("no employer url")
        out = {"url": target, "location": info["location"],
               "posted_date": info["posted_date"], "company": info["company"],
               "title": info["title"]}
        cache[uid] = out
        STATS["resolved"] += 1
        return out
    except Exception as e:  # noqa: BLE001
        # Cache the miss too, so a dead or unreadable page is not re-fetched every run.
        cache[uid] = {}
        STATS["failed"] += 1
        print(f"[simplify-resolve] {uid[:8]}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


if __name__ == "__main__":
    reset_run()
    for u in sys.argv[1:]:
        print(u, "->", resolve(u))
    save_cache()
    print(STATS, "cache:", cache_path())
