#!/usr/bin/env python3
"""
brand_first_source.py — lane-1: pull every target company's board and keep the
open SWE/ML/PM intern roles that fit Sparsh's targeting.

For each board in company_boards.BOARDS:
  ats_router.fetch_board() -> all open roles with full JDs (one JSON pull)
Filter each role:
  • role_lane(title) is not None         (SWE/ML/Data/PM; embedded/HW rejected)
  • classify_location != "reject"         (US/Canada; remote/unclear kept)
  • classify_period != "reject"           (Fall 2026 / Winter 2027 / Summer 2027;
                                           past cycles like Summer 2026 dropped)
Dedup by canonical_id. Returns store-ready machine-field dicts (the orchestrator
scores them with hotness and upserts into curated_store).
"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import date

import ats_router as A
from company_boards import boards
from hotness import role_lane
from internship_scraper import (
    COUNTRY_NEGATIVE_SUBSTRINGS,
    canonical_id,
    classify_location,
    classify_period,
)

BOARD_TIMEOUT = 50  # seconds per board — one slow board can't hang the whole run


def _listing_prefilter(title: str, location: str = "") -> bool:
    """Applied at the Workday/SmartRecruiters LISTING stage (before detail calls).
    Lenient on location — only drops CLEARLY international (Workday's 'US-CA-...'
    format doesn't match the strict classifier, so the real US/CA gate is _accept
    on the detail location)."""
    if not A.default_intern_filter(title):
        return False
    loc = (location or "").lower()
    if any(neg in loc for neg in COUNTRY_NEGATIVE_SUBSTRINGS):
        return False
    return True

# cycle labels we surface in the board
_CYCLE_RE = re.compile(
    r"(fall 20\d\d|winter 20\d\d|spring 20\d\d|summer 20\d\d)", re.IGNORECASE)


def _age_from_date(posted_date: str):
    if not posted_date:
        return None
    try:
        y, m, d = map(int, posted_date.split("-")[:3])
        return max(0, (date.today() - date(y, m, d)).days)
    except Exception:  # noqa: BLE001
        return None


def _cycle_label(title: str, jd: str) -> str:
    for src in (title, jd[:600] if jd else ""):
        m = _CYCLE_RE.search(src or "")
        if m:
            return m.group(1).title()
    return ""


def _accept(rec: A.JobRecord) -> bool:
    # intern / co-op / new-grad only (board pulls return full-time roles too)
    if not A.default_intern_filter(rec.title):
        return False
    if role_lane(rec.title) is None:
        return False
    if classify_location(rec.location)[0] == "reject":
        return False
    # period: title + the JD head (brand boards rarely put the term in the title)
    bag_terms = _cycle_label(rec.title, rec.full_jd)
    if classify_period(rec.title, bag_terms, "brand-first")[0] == "reject":
        return False
    return True


def _to_record(rec: A.JobRecord, company: str, tier: str) -> dict:
    age = _age_from_date(rec.posted_date)
    return {
        "company": company,
        "role": rec.title,
        "location": rec.location,
        "url": rec.url,
        "ats_type": rec.ats_type,
        "source": "brand-board",
        "cycle": _cycle_label(rec.title, rec.full_jd),
        "posted_date": rec.posted_date,
        "age_days": age,
        "full_jd": rec.full_jd,
        "req_id": rec.req_id,
        "_board_tier": tier,
    }


async def _one_board(client, board: dict) -> list[dict]:
    if board.get("ats_type") == "manual":
        return []
    try:
        recs = await asyncio.wait_for(
            A.fetch_board(client, board, _listing_prefilter), timeout=BOARD_TIMEOUT)
    except asyncio.TimeoutError:
        print(f"[brand-first] {board['name']} timed out (>{BOARD_TIMEOUT}s) — skipped",
              file=sys.stderr)
        return []
    except Exception as e:  # noqa: BLE001
        print(f"[brand-first] {board['name']} failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        return []
    out = []
    for r in recs:
        if not r.title or not r.url:
            continue
        if _accept(r):
            out.append(_to_record(r, board["name"], board.get("tier", "C")))
    return out


async def collect(client=None) -> list[dict]:
    """Pull + filter every board concurrently. Returns deduped store-ready dicts."""
    own_client = client is None
    if own_client:
        client = A.make_client()
    try:
        results = await asyncio.gather(*[_one_board(client, b) for b in boards()])
    finally:
        if own_client:
            await client.aclose()

    seen: set[str] = set()
    deduped: list[dict] = []
    for board_recs in results:
        for rec in board_recs:
            cid = canonical_id(rec["url"])
            if cid in seen:
                continue
            seen.add(cid)
            rec["canonical_id"] = cid
            deduped.append(rec)

    # soft dedup: the same role posted to multiple locations (different URLs ->
    # different canonical_id) collapses to one — keep the freshest (lowest age).
    best: dict[tuple, dict] = {}
    for rec in deduped:
        key = (rec["company"].strip().lower(), rec["role"].strip().lower())
        cur = best.get(key)
        if cur is None or (rec.get("age_days") or 999) < (cur.get("age_days") or 999):
            best[key] = rec
    return list(best.values())


if __name__ == "__main__":
    async def _main():
        recs = await collect()
        recs.sort(key=lambda r: (r["company"], r["role"]))
        print(f"{len(recs)} accepted brand-first intern roles\n")
        by_co: dict[str, int] = {}
        for r in recs:
            by_co[r["company"]] = by_co.get(r["company"], 0) + 1
        for co, n in sorted(by_co.items(), key=lambda x: -x[1]):
            print(f"  {n:>3}  {co}")
        print("\nsample:")
        for r in recs[:8]:
            print(f"  • [{r['_board_tier']}] {r['company'][:16]:16} | "
                  f"{r['role'][:46]:46} | {r['location'][:22]:22} | "
                  f"{r['posted_date'] or '?':10} | JD {len(r['full_jd'])}")
    asyncio.run(_main())
