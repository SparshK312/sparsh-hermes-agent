#!/usr/bin/env python3
"""
short_link_resolve.py — turn an aggregator SHORT LINK (go.vansh.nyc/<code>) into the
employer's posting URL, so the row dedups against the same requisition from any source.

WHY (2026-10-26). The vanshb03 READMEs (sources vansh-main / vansh-offseason) switched
their Apply links to go.vansh.nyc short links. A short link carries no requisition id, so
canonical_id() minted a NEW id for every one and _collapse_duplicates could not match it
to the employer's row: 114 go.vansh rows sat in the store on Oct 26, duplicating real
rows (1Password x3, Brain Co., Figure DevProd seen on the board). Same shape as the
Simplify-UUID duplicates of 2026-09-08, fixed the same way as simplify_resolve.py.

The short link is a single 302 whose Location header IS the employer URL (verified
2026-10-26: go.vansh.nyc/9t -> jobs.ashbyhq.com/brainco/<uuid>/application). One HEAD-ish
request, no redirect following, cached forever by link code (a posting's URL does not
change). Stdlib only, synchronous (runs inside wide_net_source._gather_postings()).

🔴 THE BUDGET NAMES ITS VICTIMS (CLAUDE.md, "a cap that does not announce itself is a
silent data-loss bug"). Links skipped because the per-run budget ran out are collected in
BUDGET_SKIPPED and logged by the caller. A skipped link is harmless: the row keeps its
short-link id this run, exactly as before this module existed, and resolves next run.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import store_paths

# Hosts whose links are short redirects to an employer posting. Add a host here only after
# confirming its links 30x straight to the employer (one hop).
SHORT_HOSTS = ("go.vansh.nyc",)
_CODE_RE = re.compile(r"(?:https?://)?(go\.vansh\.nyc)/([A-Za-z0-9_-]+)", re.I)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
TIMEOUT = 5
# Per-run bound on NEW resolutions (cache hits are free). The two vansh READMEs carry a
# few hundred live rows; the first run clears ~the whole backlog, later runs only new ones.
MAX_RESOLVES_PER_RUN = 400
# 🔴 CIRCUIT BREAKERS (2026-10-26 audit). TIMEOUT x MAX_RESOLVES could reach 2000 s, over
# run_curate_vps.sh's `timeout 1800`: a slow go.vansh.nyc would have killed the refresh
# before store.save. A wall-clock budget and a consecutive-failure breaker bound it; every
# link either skips is a named victim in BUDGET_SKIPPED (it keeps its short id this run).
WALL_BUDGET_S = 300
MAX_CONSECUTIVE_FAILURES = 15
# Save the cache every N new resolutions, so a killed run keeps what it learned.
SAVE_EVERY = 25
# Definitive "this link is not a posting": cache the miss. Anything else non-30x (429 rate
# limit, 403 Cloudflare challenge, 5xx/52x) is TRANSIENT: never cached, retried next run.
_DEFINITIVE_MISS_CODES = (404, 410)
# A redirect landing on one of these is another aggregator, not a resolution.
_NON_EMPLOYER_HOSTS = ("go.vansh.nyc", "simplify.jobs", "jobright.ai", "swelist.com",
                       "linkedin.com", "indeed.com", "glassdoor.com", "bit.ly")

_CACHE: dict | None = None
_resolved_this_run = 0
_run_started = 0.0
_consecutive_failures = 0
_unsaved = 0
STATS = {"cache_hit": 0, "resolved": 0, "failed": 0, "budget_exhausted": 0,
         "wall_budget": 0, "breaker_open": 0}
# Every link the budget skipped this run (the cap's victims, by name).
BUDGET_SKIPPED: list[str] = []
# HTTP status (or exception class) of every failed resolution this run -> count.
FAILED_CODES: dict[str, int] = {}


def cache_path() -> Path:
    env = os.environ.get("SHORT_LINK_CACHE")
    if env:
        return Path(env)
    return store_paths.store_path(warn=False).parent / "short_link_cache.json"


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
        print(f"[short-link] cache not saved: {e}", file=sys.stderr)


def reset_run() -> None:
    """Call once at the start of a harvest so the per-run budgets, STATS and victims restart."""
    global _resolved_this_run, _run_started, _consecutive_failures, _unsaved
    _resolved_this_run = 0
    _run_started = time.monotonic()
    _consecutive_failures = 0
    _unsaved = 0
    for k in STATS:
        STATS[k] = 0
    BUDGET_SKIPPED.clear()
    FAILED_CODES.clear()


def key_of(url: str) -> str:
    """'go.vansh.nyc/<code>' for a short link (the ?s= tag is a source marker, not
    identity), '' for anything else."""
    m = _CODE_RE.search(url or "")
    return f"{m.group(1).lower()}/{m.group(2)}" if m else ""


def is_short_link(url: str) -> bool:
    return bool(key_of(url))


def _is_employer_url(url: str) -> bool:
    host = urlparse(url or "").netloc.lower()
    return bool(host) and not any(h in host for h in _NON_EMPLOYER_HOSTS)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _redirect_target(url: str):
    """One hop only, never following it. Returns (location, code):
      (Location header, "30x")  on a redirect
      ("", "404"/"410")         a definitive miss (cacheable)
      (None, "<code or exc>")   transient (429, 403 challenge, 5xx/52x, timeout): do NOT cache
      ("", "200")               the server answered without redirecting (definitive)"""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        opener.open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            return (e.headers.get("Location", "") or "", str(e.code))
        if e.code in _DEFINITIVE_MISS_CODES:
            return ("", str(e.code))
        return (None, str(e.code))
    except Exception as e:  # noqa: BLE001
        return (None, type(e).__name__)
    return ("", "200")


def cached(url: str) -> str:
    """Cache-only lookup (no network): the resolved employer URL, or ''. curate.py uses
    this to migrate rows already in the store without making a request."""
    k = key_of(url)
    if not k:
        return ""
    return (_load_cache().get(k) or "")


def _skip(k: str, reason: str) -> str:
    STATS[reason] += 1
    BUDGET_SKIPPED.append(k)
    return ""


def resolve(url: str, _fetch=_redirect_target) -> str:
    """Resolve a short link to the employer URL. Returns '' when it is not a short link,
    a budget or the breaker skipped it (recorded in BUDGET_SKIPPED), or no employer URL
    came back. Never raises. `_fetch(url) -> (location|None, code)` is injectable."""
    global _resolved_this_run, _consecutive_failures, _unsaved
    k = key_of(url)
    if not k:
        return ""
    cache = _load_cache()
    if k in cache:
        STATS["cache_hit"] += 1
        return cache[k] or ""          # a cached definitive miss is stored as ""
    if _resolved_this_run >= MAX_RESOLVES_PER_RUN:
        return _skip(k, "budget_exhausted")
    if _run_started and time.monotonic() - _run_started > WALL_BUDGET_S:
        return _skip(k, "wall_budget")
    if _consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        return _skip(k, "breaker_open")
    _resolved_this_run += 1
    try:
        target, code = _fetch(f"https://{k}")
    except Exception as e:  # noqa: BLE001
        target, code = None, type(e).__name__
    if target and _is_employer_url(target):
        cache[k] = target
        STATS["resolved"] += 1
        _consecutive_failures = 0
        _unsaved += 1
        if _unsaved >= SAVE_EVERY:
            save_cache()
            _unsaved = 0
        return target
    STATS["failed"] += 1
    FAILED_CODES[str(code)] = FAILED_CODES.get(str(code), 0) + 1
    if target is None:
        _consecutive_failures += 1   # transient: not cached, retried next run
    else:
        cache[k] = ""                # definitive: 404/410, a 200, or a non-employer redirect
        _consecutive_failures = 0
    return ""


if __name__ == "__main__":
    reset_run()
    for u in sys.argv[1:]:
        print(u, "->", resolve(u))
    save_cache()
    print(STATS, "cache:", cache_path())
