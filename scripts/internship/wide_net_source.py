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
import sys
from pathlib import Path

import ats_router as A
from brand_first_source import _age_from_date, _cycle_label
from hotness import brand_tier, role_lane
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
JD_TIMEOUT = 12
MAX_ENRICH = 160                  # cap JD fetches; brand-first so the cap keeps brands

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
    cand = []
    for p in github + gmail:
        cid = p.canonical_id or canonical_id(p.url)
        if not cid or cid in seen:
            continue
        seen.add(cid)
        tier = brand_tier(p.company)
        if not KEEP_NONBRAND and tier == "C":             # brand-only mode
            continue
        if role_lane(p.title) is None:
            continue
        if classify_location(p.location)[0] == "reject":
            continue
        if classify_period(p.title, p.terms, p.source)[0] == "reject":
            continue
        cand.append(p)
    # brand-first so the MAX_ENRICH cap (if hit) keeps the recognizable brands
    cand.sort(key=lambda p: _TIER_RANK.get(brand_tier(p.company), 3))
    return cand


async def collect(client=None) -> list[dict]:
    cand = _gather_postings()
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
