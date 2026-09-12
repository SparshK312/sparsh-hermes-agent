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
#
# 2026-08-18: this used to be forward-order only and only scanned jd[:600], which
# left 46% of the board with no cycle tag — the one field that matters most now
# that Fall 2026 is filled. TikTok/ByteDance (a large share of current Summer-2027
# inventory) write the term BACKWARDS: "ML Engineer Intern - Search Ads - 2027
# Summer". Adding the reversed pattern and scanning the whole JD recovers 27 of
# 101 untagged live roles at zero cost.
_SEASONS = "fall|winter|spring|summer"
_CYCLE_RE = re.compile(rf"\b({_SEASONS})\s+(20\d\d)\b", re.IGNORECASE)
_CYCLE_REV_RE = re.compile(rf"\b(20\d\d)\s+({_SEASONS})\b", re.IGNORECASE)
# 🔴 A GRADUATION TERM IS NOT THE WORK TERM (2026-09-12). JDs state the candidate's
# expected graduation as a season+year ("Graduating in Spring 2028 or later", "Must be
# graduating in December 2027 or Spring 2028"), and this labeler read those as the
# role's own term. Spring 2028 sits in PAST_PERIODS, so classify_period REJECTED the
# req. Two confirmed victims, both New York: Domino Data Lab's two 2027 intern reqs
# (never reached the board) and Palantir's FDSE Commercial reqs — the vault recorded
# "FDSE Commercial New York has NO BOARD ROW" on Sep 8 and again on Sep 12 without a
# cause. A season+year preceded (within ~80 chars) by graduation language is skipped.
# Over-skipping is safe: no label -> "unclear" -> the row is KEPT. Under-skipping is a
# silent false reject, which is the failure this exists to stop.
_GRAD_CONTEXT_RE = re.compile(
    r"graduat|class of|degree|obtained by|expected to|completion|enrolled|"
    r"rising (?:senior|junior|sophomore)|pursuing", re.IGNORECASE)
_GRAD_WINDOW = 80


_CLAUSE_BREAK_RE = re.compile(r"[.;!?\n]|\s{2,}")


def _in_grad_context(text: str, start: int) -> bool:
    """True when the season+year at `start` sits in the same clause as graduation
    language. Same CLAUSE, not just the last 80 chars: "You will graduate in fall 2027 or
    spring 2028. This application is ONLY for Winter 2027" must keep Winter 2027, so the
    window is cut at the last sentence break (or the double-space that clean_fragment
    leaves between list items) before the match."""
    window = text[max(0, start - _GRAD_WINDOW):start]
    clause = _CLAUSE_BREAK_RE.split(window)[-1]
    return bool(_GRAD_CONTEXT_RE.search(clause))


def _age_from_date(posted_date: str):
    if not posted_date:
        return None
    try:
        y, m, d = map(int, posted_date.split("-")[:3])
        return max(0, (date.today() - date(y, m, d)).days)
    except Exception:  # noqa: BLE001
        return None


def _cycle_label(title: str, jd: str) -> str:
    """'Summer 2027' from a title or JD, in either word order.

    Title wins over JD (a JD often mentions several terms in boilerplate); within
    each, forward order wins over reversed. The whole JD is scanned, not just the
    head — brand boards frequently state the term in a 'Program dates' block far
    below the fold."""
    # 🔴 A TARGET TERM ANYWHERE BEATS A NON-TARGET TERM EARLIER IN THE TEXT
    # (2026-09-08). This used to return the FIRST season+year it saw and stop,
    # which is wrong because eligibility boilerplate names a GRADUATION term
    # before the role names its own term. Worked example that cost a real role:
    #   Databricks "Software Engineering Intern (2027 Start) - Winter"
    #     JD: "You will graduate in fall 2027 or spring 2028"   <- matched first
    #         "This application is ONLY for Winter 2027 (January-April)"
    #   -> labelled "Fall 2027", which is in PAST_PERIODS (he is back at school
    #      then), so classify_period rejected a WINTER 2027 req -- the scarce
    #      cycle he is actually free for. Reordering classify_period alone did NOT
    #      fix it, because by then the label already said the wrong thing.
    # Collect every candidate in order, then prefer one that is an actual target.
    from internship_scraper import TARGET_PERIODS_LOWER
    found: list[str] = []
    for src, is_jd in ((title or "", False), (jd or "", True)):
        if not src:
            continue
        for m in _CYCLE_RE.finditer(src):
            if is_jd and _in_grad_context(src, m.start()):
                continue
            found.append(f"{m.group(1).title()} {m.group(2)}")
        for m in _CYCLE_REV_RE.finditer(src):
            if is_jd and _in_grad_context(src, m.start()):
                continue
            found.append(f"{m.group(2).title()} {m.group(1)}")
    if not found:
        return ""
    for f in found:                       # a target term wins wherever it appears
        if f.lower() in TARGET_PERIODS_LOWER:
            return f
    return found[0]                       # otherwise keep the old first-match answer


# ── Location policy ──────────────────────────────────────────────────────────
# Sparsh, 2026-09-05: "dont reject based on location, its ok to keep applying."
# The US and Canada remain the primary market, but a role outside them is no longer
# DROPPED — it is kept, ranked, and shown with its real location so he can judge the
# work-authorization question himself. fit_rubric.md was changed in the same pass so a
# non-US/CA location lowers the fit score instead of producing a disqualifier.
# Measured on the VPS before shipping: this takes lane-1 from 135 to 183 accepted rows
# (+36%), mostly London/Dublin/Berlin/Beijing/Seoul — a manageable widening, not a flood.
# Flip back to True to restore the old behaviour.
REJECT_NON_NA_LOCATIONS = False


def _accept(rec: A.JobRecord, tier: str = "C") -> bool:
    # intern / co-op / new-grad only (board pulls return full-time roles too)
    if not A.default_intern_filter(rec.title):
        return False
    # ⛔ REMOVED 2026-09-05, same day it was added — a tier-S/A "surface anything
    # role_lane() cannot classify" fallback. The intent was that a novel title at a
    # deliberately-targeted company should never vanish silently (that is how Replit's
    # "Cohort 0" stayed invisible). MEASURED on the VPS with hot_watch --dry-run before
    # any cron fired: it took the alert from 0 to 47 new roles, almost all of them
    # NVIDIA silicon pools and SpaceX "New Graduate Engineer" reqs. Adding hardware
    # hard-negatives cut it to 38 and the tail kept going (asic engineer, GNC,
    # civil/structural...). It was endless whack-a-mole against a list that is supposed
    # to be an ALLOW-list. Its one unique win, "Cohort 0", is now covered explicitly by
    # "cohort" in _INTERN_RE + _GENERIC_TECH_KEYWORDS, so the fallback bought nothing.
    # 📌 The underlying concern is real and still open: an unclassifiable title at a
    # tier-S/A company is dropped with no trace. The fix is to LOG those for review,
    # not to push them onto the board.
    if role_lane(rec.title) is None:
        return False
    if REJECT_NON_NA_LOCATIONS and classify_location(rec.location)[0] == "reject":
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


# ── boards that FAILED to fetch on the most recent collect() ─────────────────
# 2026-08-18: _one_board() used to swallow every fetch error and return [], which
# the orchestrator's stale-check could not distinguish from "this board genuinely
# has no matching roles" — so a transient ReadTimeout struck every posting from
# that board, and two consecutive failures (the board runs twice daily) killed the
# company outright. The runtime log showed 1,755 such failures across 87 runs
# (NVIDIA x24, Anthropic x19, Anduril x19, OpenAI/Stripe x18 each) and Anduril's
# live "2027 Software Engineer Intern" req was confirmed false-dead because of it.
# Recording the failures lets curate.py skip striking those postings.
FAILED_BOARDS: set[str] = set()


async def _one_board(client, board: dict) -> list[dict]:
    if board.get("ats_type") == "manual":
        return []
    try:
        recs = await asyncio.wait_for(
            A.fetch_board(client, board, _listing_prefilter), timeout=BOARD_TIMEOUT)
    except asyncio.TimeoutError:
        print(f"[brand-first] {board['name']} timed out (>{BOARD_TIMEOUT}s) — skipped",
              file=sys.stderr)
        FAILED_BOARDS.add(board["name"])
        return []
    except Exception as e:  # noqa: BLE001
        print(f"[brand-first] {board['name']} failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        FAILED_BOARDS.add(board["name"])
        return []
    out = []
    for r in recs:
        if not r.title or not r.url:
            continue
        if _accept(r, board.get("tier", "C")):
            out.append(_to_record(r, board["name"], board.get("tier", "C")))
    return out


async def collect(client=None) -> list[dict]:
    """Pull + filter every board concurrently. Returns deduped store-ready dicts."""
    own_client = client is None
    if own_client:
        client = A.make_client()
    FAILED_BOARDS.clear()          # per-run; curate.py reads it right after
    getattr(A, "BOARD_FETCH_FAILURES", set()).clear()
    try:
        results = await asyncio.gather(*[_one_board(client, b) for b in boards()])
    finally:
        if own_client:
            await client.aclose()
    # Boards that failed INSIDE ats_router.fetch_board never raised up to
    # _one_board (it swallows and returns []), so they were invisible here and
    # their postings were not exempt from the stale-strike. Merge them in.
    FAILED_BOARDS.update(getattr(A, "BOARD_FETCH_FAILURES", set()))
    if FAILED_BOARDS:
        print(f"[brand-first] {len(FAILED_BOARDS)} board(s) failed this run — their "
              f"postings are EXEMPT from the stale-check: "
              f"{', '.join(sorted(FAILED_BOARDS))}", file=sys.stderr)

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

    # soft dedup: collapse TRUE duplicates of one posting that reached us under
    # two URLs — keep the freshest (lowest age).
    #
    # 🔴 LOCATION IS PART OF THE KEY (added 2026-09-08). It was (company, role)
    # only, which silently destroyed genuinely DIFFERENT reqs that happen to share
    # a title. Measured on a live run: 22 groups, 39 roles lost. The worst were
    #   Palantir  — the same title posted separately in New York / Palo Alto /
    #               Seattle / D.C. / London / Denver / Honolulu / Seoul, each its
    #               own Lever req with its own application form. The board showed
    #               ONE, chosen by whichever was freshest, and NEW YORK (his target
    #               city) was routinely the one dropped.
    #   Datadog   — "Product Management Intern" in New York AND Paris; the Paris
    #               req won a live probe and the New York one, which he had
    #               applied to, was the casualty.
    #   SpaceX    — "New Graduate Engineer, Software Security" across 4 sites.
    #   Perplexity— Search MLE in London AND Belgrade.
    # A single req that genuinely spans cities arrives as ONE record with a
    # multi-city location string, so it still collapses correctly.
    best: dict[tuple, dict] = {}
    for rec in deduped:
        key = (rec["company"].strip().lower(), rec["role"].strip().lower(),
               (rec.get("location") or "").strip().lower())
        cur = best.get(key)
        # `or 999` is WRONG: age_days == 0 is falsy, so a role posted TODAY was
        # scored 999 and lost "keep the freshest" to an older duplicate.
        r_age = rec.get("age_days")
        c_age = cur.get("age_days") if cur else None
        if cur is None or (999 if r_age is None else r_age) < (999 if c_age is None else c_age):
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
