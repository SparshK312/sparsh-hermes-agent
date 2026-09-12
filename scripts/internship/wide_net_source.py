#!/usr/bin/env python3
"""
wide_net_source.py — lane-2: the wide net (GitHub aggregators + InternInsider Gmail).

Brand-first boards (lane-1) only cover target companies that have an ATS board in
company_boards.py. The wide net catches postings from EVERY recognized brand
(Meta/Apple/Microsoft/Netflix/Google/Amazon/xAI/Mistral/…) the moment they appear
in the community lists or the InternInsider newsletter — even when we don't have
that company's board wired. Then it JD-enriches each via ats_router (real ATS URLs
→ real JDs) and scores by the same hotness model.

Filter: keep only RECOGNIZED BRANDS (hotness tier S/A/B) — this is the "all target
companies tracked" lane, not a firehose. Non-brand postings are dropped (the user
weights company name heavily). Role/location/period gating reused from the scraper.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import ats_router as A
from brand_first_source import _age_from_date, _cycle_label
from hotness import brand_tier, normalize_company_name, role_lane
from internship_scraper import (
    SOURCES,
    canonical_id,
    classify_location,
    classify_period,
    fetch_source,
    parse_html_table,
    parse_markdown_table,
)
import gmail_source

# Comprehensive mode: keep ALL relevant intern roles (incl. non-brand "C" tier),
# not just recognized brands. Brand still wins ranking via hotness; C-tier is
# clearly labelled in the Tier column and sinks to the bottom of the queue.
KEEP_NONBRAND = True
# See brand_first_source.REJECT_NON_NA_LOCATIONS for the rationale (2026-09-05).
REJECT_NON_NA_LOCATIONS = False
JD_TIMEOUT = 12
MAX_ENRICH = 160                  # cap JD fetches; brand-first so the cap keeps brands

# 🔴 WHAT THE CAP DROPPED THIS RUN (added 2026-09-11). READ BY curate.py.
# The cap is an ENRICHMENT BUDGET, not a liveness signal: a posting the cap cut is
# one the aggregator STILL LISTS and we simply chose not to fetch a JD for. Before
# this existed, curate.py's stale-check could not tell "the feed dropped it" from
# "we declined to look at it", struck the latter every run, and — because the cut is
# tier-sorted and therefore the SAME rows every time — those rows could never clear
# their strikes. At WIDE_STALE_STRIKES=14 and two runs a day that is a guaranteed
# 7-day death sentence for every row outside the top MAX_ENRICH, regardless of
# whether the job is open. Measured 2026-09-11: 38 of 40 checkable rows killed this
# way were still OPEN on the employer's own board.
# Keyed BOTH ways on purpose: canonical_id alone misses rows whose stored id came
# from an enrichment-rewritten URL (`url = rec.url if ok else p.url`), which measured
# as a 21-row leak against 159 covered — the (company, role, location) triple closes it.
CAPPED_OUT_IDS: set[str] = set()
CAPPED_OUT_TRIPLES: set[tuple] = set()


def _cap_triple(company: str, role: str, location: str) -> tuple:
    """Same normalisation curate.py uses for cross-lane dedup."""
    return (normalize_company_name(company or ""),
            re.sub(r"\s+", " ", (role or "").lower()).strip(),
            re.sub(r"\s+", " ", (location or "").lower()).strip())

ENV_PATH = Path.home() / ".hermes" / ".env"


def _load_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _fetch_github() -> list:
    posts = []
    for src in SOURCES:
        try:
            raw = fetch_source(src["url"])
            if src["format"] == "html_table":
                posts.extend(parse_html_table(raw, src["name"]))
            else:
                posts.extend(parse_markdown_table(raw, src["name"]))
        except Exception as e:  # noqa: BLE001
            print(f"[wide-net] source {src['name']} failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
    return posts


def _gather_postings() -> list:
    """Sync fetch of GitHub + Gmail (urllib/imaplib are blocking) -> filtered candidates."""
    github = _fetch_github()
    env = _load_env()
    env_get = env.get
    gmail, gfails = gmail_source.fetch_email_postings(env_get)
    for src, err in gfails:
        print(f"[wide-net] {src}: {err}", file=sys.stderr)

    _TIER_RANK = {"S": 0, "A": 1, "B": 2, "C": 3}
    seen: set[str] = set()
    # Secondary dedup key, added 2026-09-05. canonical_id is URL-ONLY, so the SAME job
    # reached through two aggregators mints two ids and lands as two rows: the
    # SimplifyJobs repo yields the employer's greenhouse/workday URL, while the SWElist
    # newsletter yields a simplify.jobs/p/<uuid> rewrite of the identical role.
    # Measured on the 2026-09-05 digest: 12 of 20 sampled companies appear in BOTH.
    # `github` is iterated before `gmail` on purpose, so the surviving row is the one
    # carrying a real ATS URL, a location and a posted date.
    seen_ct: set[tuple[str, str]] = set()
    cand = []
    for p in github + gmail:
        cid = p.canonical_id or canonical_id(p.url)
        if not cid or cid in seen:
            continue
        # 🔴 LOCATION IN THE KEY (2026-09-08) — same defect as brand_first_source
        # and curate.py's cross-lane pass. Measured live: 60 groups / 107
        # distinct-URL rows dropped, including TWO different Google "Software
        # Engineer Intern - Multiple Teams" reqs (job ids 94172495052972742 and
        # 100648618540573382), Stripe SF/NYC vs Toronto, and TikTok "Product
        # Manager Intern - PGC" LA vs San Jose.
        ct = (normalize_company_name(p.company or ""),
              re.sub(r"\s+", " ", (p.title or "").lower()).strip(),
              re.sub(r"\s+", " ", (p.location or "").lower()).strip())
        if all(ct[:2]) and ct in seen_ct:
            continue
        seen.add(cid)
        if all(ct):
            seen_ct.add(ct)
        tier = brand_tier(p.company)
        if not KEEP_NONBRAND and tier == "C":             # brand-only mode
            continue
        # 🔴 SWElist rows are STRICTLY WORSE DATA than the same role from a GitHub
        # source: the newsletter carries no location at all and its links are
        # simplify.jobs/p/<uuid> rewrites that ats_router cannot JD-enrich. Empty
        # location classifies as "unclear", which is KEPT, so nothing downstream
        # filters them. Measured on the VPS 2026-09-05 (SINCE_DAYS=12): 1,114 raw ->
        # 948 unique -> 524 surviving, 442 of them tier C. That would have taken the
        # queue from 353 to ~880 rows, almost all with no location, no JD and no fit
        # score. Restricted to recognized brands, where thin data is still worth
        # having; the (company, title) dedup above already removes the repo overlap.
        if p.source == "swelist" and tier == "C":
            continue
        if role_lane(p.title) is None:
            continue
        if REJECT_NON_NA_LOCATIONS and classify_location(p.location)[0] == "reject":
            continue
        if classify_period(p.title, p.terms, p.source)[0] == "reject":
            continue
        cand.append(p)
    # brand-first so the MAX_ENRICH cap (if hit) keeps the recognizable brands
    cand.sort(key=lambda p: _TIER_RANK.get(brand_tier(p.company), 3))
    return cand


async def collect(client=None) -> list[dict]:
    # 🔴 CLEARED FIRST, BEFORE ANYTHING THAT CAN RAISE. If _gather_postings() throws,
    # a stale set from the PREVIOUS run would otherwise survive and curate.py's
    # stale-check would exempt rows on last run's evidence — a silent
    # wrong-direction failure (rows wrongly exempted never die, so the board
    # quietly fills with closed postings). Empty means "exempt nothing", which is
    # the safe default.
    CAPPED_OUT_IDS.clear()
    CAPPED_OUT_TRIPLES.clear()
    cand = _gather_postings()
    # 🔴 ANNOUNCE THE CAP (2026-09-08). This truncation was silent, and the amount
    # it silently discarded was not small: a live measurement on 2026-09-08 found
    # 705 candidates -> 545 CUT, every one of them tier C. KEEP_NONBRAND = True
    # says "keep ALL relevant intern roles" and this line quietly negated it.
    # Worse, _gather_postings() sorts by TIER ONLY and Python's sort is stable, so
    # the survivors are whichever tier-C rows happened to come first in the source
    # order -- every tier-C row from the later feeds is cut on EVERY run, the same
    # ones each time, and then takes stale-strikes for never being harvested.
    # CLAUDE.md: "Any limit must log when it is reached. A result set that exactly
    # equals your cap is a red flag, never a coincidence."
    if len(cand) > MAX_ENRICH:
        from collections import Counter
        dropped = cand[MAX_ENRICH:]
        # record BEFORE truncating so the stale-check can exempt them (see above)
        for _p in dropped:
            _cid = _p.canonical_id or canonical_id(_p.url)
            if _cid:
                CAPPED_OUT_IDS.add(_cid)
            CAPPED_OUT_TRIPLES.add(_cap_triple(_p.company, _p.title, _p.location))
        top = ", ".join(f"{c}×{n}" for c, n in
                        Counter(p.company for p in dropped).most_common(8))
        print(f"[wide-net] ⚠️ MAX_ENRICH cap HIT: {len(cand)} candidates -> keeping "
              f"{MAX_ENRICH}, DROPPING {len(dropped)} (tier-sorted, so the cut is all "
              f"low-tier and is the SAME rows every run). Most-dropped: {top}",
              file=sys.stderr)
    cand = cand[:MAX_ENRICH]
    own = client is None
    if own:
        client = A.make_client()

    # bot-friendly ATS APIs: a non-200 here = genuinely dead -> drop. Manual/iCIMS/
    # Oracle can 403 from bot-blocking even when live, so we DON'T drop those on a
    # failed fetch (we trust the aggregator's freshness — it removes closed roles).
    DROP_DEAD_ATS = {"greenhouse", "ashby", "lever", "workday", "smartrecruiters", "amazon"}

    async def _enrich(p):
        rec = None
        try:
            rec = await asyncio.wait_for(A.fetch_jd_record(client, p.url), timeout=JD_TIMEOUT)
        except Exception:  # noqa: BLE001
            rec = None
        if rec and rec.dead and rec.ats_type in DROP_DEAD_ATS:
            return None                          # confirmed-dead via a real API -> drop
        ok = rec and not rec.dead
        jd = (rec.full_jd if ok else "") or ""
        posted = (rec.posted_date if ok else "") or p.posted_date
        loc = (rec.location if ok else "") or p.location
        url = rec.url if (ok and rec.url) else p.url   # clean board URL collapses dups
        age = _age_from_date(posted) if posted else p.age_days
        return {
            "company": p.company, "role": p.title, "location": loc,
            "url": url, "ats_type": A.detect_ats(url), "source": f"wide:{p.source}",
            "cycle": _cycle_label(p.title, jd) or (p.terms or ""),
            "posted_date": posted, "age_days": age, "full_jd": jd,
            "canonical_id": canonical_id(url),
        }
    try:
        results = await asyncio.gather(*[_enrich(p) for p in cand])
    finally:
        if own:
            await client.aclose()
    return [r for r in results if r]   # drop the confirmed-dead


if __name__ == "__main__":
    async def _main():
        recs = await collect()
        from collections import Counter
        print(f"{len(recs)} brand postings from the wide net\n")
        for co, n in sorted(Counter(r["company"] for r in recs).items(), key=lambda x: -x[1]):
            print(f"  {n:>2}  {co}  [{brand_tier(co)}]")
        print("\nsample:")
        for r in recs[:10]:
            print(f"  • {r['company'][:16]:16} | {r['role'][:42]:42} | "
                  f"{r['source']:22} | JD {len(r['full_jd'])}")
    asyncio.run(_main())
