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
import simplify_resolve

# Comprehensive mode: keep ALL relevant intern roles (incl. non-brand "C" tier),
# not just recognized brands. Brand still wins ranking via hotness; C-tier is
# clearly labelled in the Tier column and sinks to the bottom of the queue.
KEEP_NONBRAND = True
# See brand_first_source.REJECT_NON_NA_LOCATIONS for the rationale (2026-09-05).
REJECT_NON_NA_LOCATIONS = False
JD_TIMEOUT = 12
MAX_ENRICH = 160                  # total JD-enrichment budget per run (see _apply_enrich_cap)
# 🔴 THE CAP NEVER CUTS A TARGET COMPANY (2026-09-12). Until today the cut was
# `cand[:MAX_ENRICH]` over a tier-sorted list, which was fine while tier S/A/B fitted
# inside 160 — and on 2026-09-08 they did. By 2026-09-12 the refresh log read
# "1,159 candidates -> keeping 160, DROPPING 999 ... Most-dropped: ... RTX×20, AMD×16,
# 🔥AMD×15": TIER-B EMPLOYERS WERE BEING CUT, silently, on every run, and only the
# capped-out exemption kept their existing rows alive. A tier table only means
# something if being on it guarantees the row is looked at. So: every S/A/B candidate
# is enriched unconditionally; MAX_ENRICH bounds the tier-C remainder, with a floor so
# tier-C discovery never starves as the target set grows. Within tier C the budget goes
# to the FRESHEST rows (age ascending), so a new posting gets its window instead of the
# same first-in-feed-order rows winning forever.
MIN_NONBRAND_ENRICH = 40

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
# 🔴 WHAT THE EMPLOYER'S ATS CONFIRMED CLOSED THIS RUN (added 2026-09-12). READ BY curate.py.
# _enrich() drops a candidate when a real ATS API answers "gone" (Greenhouse 404, Oracle
# empty items, ...). Dropping it from the harvest was correct, but the STORE row then sat
# on the queue taking one strike per run — 14 for a wide-net row, i.e. a week of a posting
# the employer had already pulled. Confirmed-dead is the one dead signal that IS evidence,
# so curate marks those rows dead in the same run. Cleared with CAPPED_OUT_* before the
# gather for the same reason those are.
CONFIRMED_DEAD_IDS: set[str] = set()
# ATS types whose "dead" answer is a real API saying the requisition is gone (not a
# timeout, not a WAF page). Module-level since 2026-09-12 so the invariant suite can see it.
# oracle: _single_oracle reports dead only on an empty `items` list from a 200 (verified:
# three pulled Tradeweb reqs -> 0 items; a live one -> 1 item).
DROP_DEAD_ATS = {"greenhouse", "ashby", "lever", "workday", "smartrecruiters", "amazon", "oracle"}


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
    simplify_resolve.reset_run()
    # newest newsletter rows first, so the per-run resolve budget goes to this week's
    # postings rather than the tail of the 12-day IMAP window
    gmail = sorted(gmail, key=lambda p: 999 if p.age_days is None else p.age_days)
    for p in github + gmail:
        # SWElist rows carry a simplify.jobs/p/<uuid> link and NO location. For a
        # recognised brand (the only rows that reach the board from this feed), resolve
        # the link to the employer's URL + real location FIRST, so the id/triple dedup
        # below can match it to the same req from the repo or a brand board. Cheap
        # gates run before the network call; tier-C rows are never resolved (they are
        # the coverage digest's population, not the board's).
        if (p.source == "swelist" and simplify_resolve.uuid_of(p.url)
                and brand_tier(p.company) != "C"
                and role_lane(p.title) is not None
                and classify_period(p.title, p.terms, p.source)[0] != "reject"):
            hit = simplify_resolve.resolve(p.url)
            if hit:
                p.url = hit["url"]
                p.canonical_id = canonical_id(hit["url"])
                p.location = hit.get("location") or p.location
                p.posted_date = hit.get("posted_date") or p.posted_date
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
        # having. 2026-09-12: these rows are no longer LOST — coverage_digest.py reads
        # the same feed and reports every in-lane tier-C posting weekly, so an employer
        # missing from the tier table surfaces as a line to promote, not a silent hole.
        # (They stay off the board: no location, no JD, and they would be the first
        # rows the enrichment cap cuts anyway.)
        if p.source == "swelist" and tier == "C":
            continue
        if role_lane(p.title) is None:
            continue
        if REJECT_NON_NA_LOCATIONS and classify_location(p.location)[0] == "reject":
            continue
        if classify_period(p.title, p.terms, p.source)[0] == "reject":
            continue
        cand.append(p)
    if simplify_resolve.STATS["resolved"] or simplify_resolve.STATS["failed"] \
            or simplify_resolve.STATS["budget_exhausted"]:
        print(f"[wide-net] simplify links: {simplify_resolve.STATS}", file=sys.stderr)
    simplify_resolve.save_cache()
    # tier first (the cap protects S/A/B outright), then FRESHEST first within a tier so
    # the tier-C budget rotates onto new postings instead of the same first-seen rows.
    cand.sort(key=lambda p: (_TIER_RANK.get(brand_tier(p.company), 3),
                             999 if p.age_days is None else p.age_days))
    return cand


def _apply_enrich_cap(cand: list) -> tuple[list, list]:
    """Pure. Split the candidate list into (keep, dropped).

    Every tier-S/A/B candidate is kept. Tier C gets whatever is left of MAX_ENRICH,
    never less than MIN_NONBRAND_ENRICH. Order within each group is preserved, so the
    caller's (tier, age) sort decides WHICH tier-C rows survive."""
    target = [p for p in cand if brand_tier(p.company) != "C"]
    rest = [p for p in cand if brand_tier(p.company) == "C"]
    budget = max(MIN_NONBRAND_ENRICH, MAX_ENRICH - len(target))
    return target + rest[:budget], rest[budget:]


async def collect(client=None) -> list[dict]:
    # 🔴 CLEARED FIRST, BEFORE ANYTHING THAT CAN RAISE. If _gather_postings() throws,
    # a stale set from the PREVIOUS run would otherwise survive and curate.py's
    # stale-check would exempt rows on last run's evidence — a silent
    # wrong-direction failure (rows wrongly exempted never die, so the board
    # quietly fills with closed postings). Empty means "exempt nothing", which is
    # the safe default.
    CAPPED_OUT_IDS.clear()
    CAPPED_OUT_TRIPLES.clear()
    CONFIRMED_DEAD_IDS.clear()
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
    cand, dropped = _apply_enrich_cap(cand)
    if dropped:
        from collections import Counter
        # record BEFORE truncating so the stale-check can exempt them (see above)
        for _p in dropped:
            _cid = _p.canonical_id or canonical_id(_p.url)
            if _cid:
                CAPPED_OUT_IDS.add(_cid)
            CAPPED_OUT_TRIPLES.add(_cap_triple(_p.company, _p.title, _p.location))
        top = ", ".join(f"{c}×{n}" for c, n in
                        Counter(p.company for p in dropped).most_common(8))
        n_target = sum(1 for p in cand if brand_tier(p.company) != "C")
        print(f"[wide-net] ⚠️ enrichment cap HIT: keeping {len(cand)} "
              f"({n_target} tier-S/A/B, all of them + {len(cand) - n_target} tier-C by "
              f"freshness), DROPPING {len(dropped)} tier-C. Most-dropped: {top}",
              file=sys.stderr)
    own = client is None
    if own:
        client = A.make_client()

    # bot-friendly ATS APIs: a non-200 here = genuinely dead -> drop. Manual/iCIMS/
    # Oracle can 403 from bot-blocking even when live, so we DON'T drop those on a
    # failed fetch (we trust the aggregator's freshness — it removes closed roles).

    async def _enrich(p):
        rec = None
        try:
            rec = await asyncio.wait_for(A.fetch_jd_record(client, p.url), timeout=JD_TIMEOUT)
        except Exception:  # noqa: BLE001
            rec = None
        if rec and rec.dead and rec.ats_type in DROP_DEAD_ATS:
            CONFIRMED_DEAD_IDS.add(p.canonical_id or canonical_id(p.url))
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
