#!/usr/bin/env python3
"""
company_boards.py — the brand-first target list for lane-1 curation.

One entry per target company → its ATS board, so curate.py can pull every open
SWE/ML/PM intern role with the full JD directly from the JSON API (no browser).

Schema per entry:
  name       display name
  tier       "S" | "A" | "B"  — brand tier (drives hotness; see hotness.py)
  ats_type   "greenhouse" | "lever" | "ashby" | "workable" | "workday" |
             "smartrecruiters" | "oracle" | "manual"
  + ats-specific identifiers:
     greenhouse:      token=<board_token>
     lever:           site=<site>
     ashby:           org=<job_board_name>
     workable:        account=<account_slug>
     workday:         host=<tenant.wdN.myworkdayjobs.com>, site=<site>
     smartrecruiters: company=<Company>
     oracle:          host=<tenant.fa.<region>.oraclecloud.com>, site=<CX site number>
                      (Oracle Cloud HCM "CandidateExperience" boards; public REST API)
     manual:          url=<careers page>   (no API → click-through only)

To add a company: append a dict here, then `python curate.py --validate-boards`
to confirm the token resolves (it pings each board and reports role counts).

Tiers seeded from the 32-company watchlist (internship_scraper.WATCHLIST):
  S = FAANG+ & top AI labs · A = fintech unicorns & top startups · B = tier-2/mid-cap
Most B-tier entries are derived from REAL verified URLs in the existing
Application Tracker; the S/A brand boards are validated by --validate-boards.
"""

BOARDS = [
    # ── S tier — FAANG+ & top AI labs ──────────────────────────────────────
    {"name": "NVIDIA", "tier": "S", "ats_type": "workday",
     "host": "nvidia.wd5.myworkdayjobs.com", "site": "NVIDIAExternalCareerSite"},
    {"name": "Anthropic", "tier": "S", "ats_type": "greenhouse", "token": "anthropic"},
    {"name": "OpenAI", "tier": "S", "ats_type": "ashby", "org": "openai"},
    {"name": "Amazon", "tier": "S", "ats_type": "amazon"},
    # SpaceX was never a tracked board — its roles only ever reached the queue
    # via the wide net, the lane whose dead-signal is least trustworthy. It is
    # also the single biggest beneficiary of the work-model location fix: every
    # posting reads "Flexible - Any SpaceX Site" with the real cities in
    # offices[], so all 2,166 hard-rejected before that landed.
    # Verified live 2026-08-20: token "spacex" returns 2,166 postings.
    {"name": "SpaceX", "tier": "S", "ats_type": "greenhouse", "token": "spacex"},

    # ── A tier — fintech unicorns & top startups ───────────────────────────
    {"name": "Stripe", "tier": "A", "ats_type": "greenhouse", "token": "stripe"},
    # Added 2026-09-05. PayPal had NO board at all (63 configured, PayPal not among
    # them), which is why the "Sep 5 batch: … PayPal …" line in Action Items pointed at
    # a company with zero rows in a 353-row queue. Its SWE Intern req appeared in the
    # SWElist digest of 2026-09-05 and was invisible here. Workday, verified live.
    {"name": "PayPal", "tier": "B", "ats_type": "workday",
     "host": "paypal.wd1.myworkdayjobs.com", "site": "jobs"},
    {"name": "Databricks", "tier": "A", "ats_type": "greenhouse", "token": "databricks"},
    {"name": "Scale AI", "tier": "A", "ats_type": "greenhouse", "token": "scaleai"},
    {"name": "Vercel", "tier": "A", "ats_type": "greenhouse", "token": "vercel"},
    {"name": "Figma", "tier": "A", "ats_type": "greenhouse", "token": "figma"},
    {"name": "Plaid", "tier": "A", "ats_type": "ashby", "org": "plaid"},
    {"name": "Robinhood", "tier": "A", "ats_type": "greenhouse", "token": "robinhood"},
    {"name": "Brex", "tier": "A", "ats_type": "greenhouse", "token": "brex"},
    {"name": "Ramp", "tier": "A", "ats_type": "ashby", "org": "ramp"},
    {"name": "Notion", "tier": "A", "ats_type": "ashby", "org": "notion"},
    {"name": "Linear", "tier": "A", "ats_type": "ashby", "org": "Linear"},
    {"name": "Perplexity", "tier": "A", "ats_type": "ashby", "org": "Perplexity"},
    {"name": "Cursor (Anysphere)", "tier": "A", "ats_type": "ashby", "org": "cursor"},
    {"name": "Replit", "tier": "A", "ats_type": "ashby", "org": "replit"},
    {"name": "Cohere", "tier": "A", "ats_type": "ashby", "org": "cohere"},
    {"name": "Mercury", "tier": "A", "ats_type": "greenhouse", "token": "mercury"},
    {"name": "Coinbase", "tier": "A", "ats_type": "greenhouse", "token": "coinbase"},
    {"name": "SoFi", "tier": "B", "ats_type": "greenhouse", "token": "sofi"},
    {"name": "Adobe", "tier": "A", "ats_type": "workday",
     "host": "adobe.wd5.myworkdayjobs.com", "site": "external_experienced"},
    {"name": "Waymo", "tier": "A", "ats_type": "greenhouse", "token": "waymo"},
    {"name": "MongoDB", "tier": "A", "ats_type": "greenhouse", "token": "mongodb"},
    {"name": "Datadog", "tier": "A", "ats_type": "greenhouse", "token": "datadog"},
    {"name": "Airbnb", "tier": "A", "ats_type": "greenhouse", "token": "airbnb"},
    {"name": "Snowflake", "tier": "A", "ats_type": "ashby", "org": "snowflake"},
    {"name": "Pinterest", "tier": "B", "ats_type": "greenhouse", "token": "pinterest"},
    {"name": "Reddit", "tier": "B", "ats_type": "greenhouse", "token": "reddit"},

    # ── B tier — verified from the existing Application Tracker URLs ────────
    {"name": "Intel", "tier": "B", "ats_type": "workday",
     "host": "intel.wd1.myworkdayjobs.com", "site": "External"},
    {"name": "Autodesk", "tier": "B", "ats_type": "workday",
     "host": "autodesk.wd1.myworkdayjobs.com", "site": "uni"},
    {"name": "Campbell's", "tier": "B", "ats_type": "workday",
     "host": "campbellsoup.wd5.myworkdayjobs.com", "site": "ExternalCareers_GlobalSite"},
    {"name": "CIBC", "tier": "B", "ats_type": "workday",
     "host": "cibc.wd3.myworkdayjobs.com", "site": "campus"},
    # Added 2026-09-08. Capital One was reachable ONLY through aggregators, and when
    # one stopped listing its Toronto Winter-2027 intern reqs they took 24 strikes and
    # died -- while Capital One's own Workday still served all four (verified live).
    # A lane-1 board is the fix: the employer's own list can't "stop listing" a job
    # that is open.
    {"name": "Capital One", "tier": "B", "ats_type": "workday",
     "host": "capitalone.wd12.myworkdayjobs.com", "site": "Capital_One"},
    {"name": "Ciena", "tier": "B", "ats_type": "workday",
     "host": "ciena.wd5.myworkdayjobs.com", "site": "Careers"},
    {"name": "DPR Construction", "tier": "B", "ats_type": "workday",
     "host": "mydpr.wd5.myworkdayjobs.com", "site": "11212017"},
    {"name": "1Password", "tier": "B", "ats_type": "ashby", "org": "1password"},
    {"name": "Rivian / VW", "tier": "B", "ats_type": "ashby", "org": "rivianvw.tech"},
    {"name": "Cerebras", "tier": "B", "ats_type": "ashby", "org": "cerebras"},
    {"name": "Lila Sciences", "tier": "B", "ats_type": "greenhouse", "token": "lilasciences"},
    {"name": "Bosch", "tier": "B", "ats_type": "smartrecruiters", "company": "BoschGroup"},
    {"name": "Eversana", "tier": "B", "ats_type": "smartrecruiters", "company": "EVERSANA1"},

    # ── ADDED 2026-07: elite brands missing from the list (tokens validated via
    #    --validate-boards; dead ones pruned). ────────────────────────────────
    {"name": "Palantir", "tier": "S", "ats_type": "lever", "site": "palantir"},
    {"name": "Anduril", "tier": "A", "ats_type": "greenhouse", "token": "andurilindustries"},
    {"name": "DoorDash", "tier": "A", "ats_type": "greenhouse", "token": "doordashusa"},
    {"name": "Dropbox", "tier": "A", "ats_type": "greenhouse", "token": "dropbox"},
    {"name": "Cloudflare", "tier": "A", "ats_type": "greenhouse", "token": "cloudflare"},
    {"name": "Roblox", "tier": "A", "ats_type": "greenhouse", "token": "roblox"},
    {"name": "Discord", "tier": "A", "ats_type": "greenhouse", "token": "discord"},
    {"name": "Verkada", "tier": "B", "ats_type": "greenhouse", "token": "verkada"},
    {"name": "Nuro", "tier": "B", "ats_type": "greenhouse", "token": "nuro"},
    {"name": "Hudson River Trading", "tier": "A", "ats_type": "greenhouse", "token": "wehrtyou"},
    {"name": "Samsara", "tier": "B", "ats_type": "greenhouse", "token": "samsara"},

    # ── ADDED 2026-09-07 after a sourcing audit prompted by Sparsh asking what was
    #    open at Apple / Waymo / Neuralink / Wealthsimple. Both tokens validated
    #    live against the ATS API before being added. ─────────────────────────
    # Neuralink had NO board entry at all, so the queue held ZERO Neuralink rows
    # while 20 internships were open, 7 of them SWE/ML. Two of those (BCI
    # Applications, Internal Apps) had résumé variants staged since Jul 2026 and
    # were never fired. Verified live: token "neuralink" returns 78 postings.
    {"name": "Neuralink", "tier": "A", "ats_type": "greenhouse", "token": "neuralink"},
    # Wealthsimple reached the queue only via the wide net, which is why a fit-85
    # Winter 2027 Toronto intern req sat on one thinly-sourced row. Winter is the
    # scarce cycle he is free for from Dec 18. Verified live: org "wealthsimple"
    # returns 47 postings.
    {"name": "Wealthsimple", "tier": "B", "ats_type": "ashby", "org": "wealthsimple"},
    {"name": "Affirm", "tier": "B", "ats_type": "greenhouse", "token": "affirm"},

    # ── ADDED 2026-09-12 from the SWElist coverage audit (16 digests, Aug 27 - Sep 11,
    #    diffed against the board). Every one of these had ZERO rows on any tab while
    #    holding an open SWE/AI/PM intern req in the US or Canada on its own ATS —
    #    all were tier C by absence from hotness.py, and tier C is deleted by the
    #    wide net before any other check. Each token below was validated live the
    #    same day (listing call + intern count). Tiers are B: funded, real, and a
    #    rung below the A bar (Stripe/Datadog/Palantir). ───────────────────────
    {"name": "Epic Games", "tier": "B", "ats_type": "greenhouse", "token": "epicgames"},
    {"name": "Akuna Capital", "tier": "B", "ats_type": "greenhouse", "token": "akunacapital"},
    {"name": "Tower Research Capital", "tier": "B", "ats_type": "greenhouse",
     "token": "towerresearchcapital"},
    {"name": "Domino Data Lab", "tier": "B", "ats_type": "greenhouse", "token": "dominodatalab"},
    {"name": "Tanium", "tier": "B", "ats_type": "greenhouse", "token": "tanium"},
    {"name": "Formlabs", "tier": "B", "ats_type": "greenhouse", "token": "formlabs"},
    {"name": "Hudl", "tier": "B", "ats_type": "greenhouse", "token": "hudl"},
    {"name": "Visier", "tier": "B", "ats_type": "greenhouse", "token": "visiersolutionsinc"},
    {"name": "D2L", "tier": "B", "ats_type": "greenhouse", "token": "d2l"},
    {"name": "Saronic", "tier": "B", "ats_type": "ashby", "org": "saronic"},
    {"name": "Hadrian", "tier": "B", "ats_type": "ashby", "org": "hadrian-automation"},
    {"name": "Bedrock Robotics", "tier": "B", "ats_type": "ashby", "org": "bedrock-robotics"},
    # Lyft was in hotness.TIER_B since Aug 18 but never had a board here, so its 16
    # Summer-2027 intern reqs reached the queue only as Simplify rows with
    # app.careerpuck.com URLs that no JD fetcher can read — ten rows rendered "👀 no JD"
    # on 2026-09-12 while the Greenhouse API served every JD. Verified live that day.
    {"name": "Lyft", "tier": "B", "ats_type": "greenhouse", "token": "lyft"},
    # Oracle Cloud HCM boards. American Express was the single biggest miss in the
    # audit: 57 rows on the Simplify feed, 29 of them in-lane (Software Engineer /
    # AI Engineer / Digital PM interns under Enterprise Technology Services, 11 in
    # NYC) — cut by the enrichment cap on every run for at least a week (the refresh
    # log names it in "Most-dropped" six runs running). Tradeweb posted 10 SWE intern
    # reqs in Jersey City on Sep 10. The REST endpoint was verified against all four
    # tenants on 2026-09-12 (listing + detail; Amex paginates past 200).
    {"name": "American Express", "tier": "B", "ats_type": "oracle",
     "host": "egug.fa.us2.oraclecloud.com", "site": "CX_1"},
    {"name": "Tradeweb", "tier": "B", "ats_type": "oracle",
     "host": "ecnf.fa.us2.oraclecloud.com", "site": "CX"},
    {"name": "Dell Technologies", "tier": "B", "ats_type": "oracle",
     "host": "iawmqy.fa.ocs.oraclecloud.com", "site": "careers"},
    {"name": "Honeywell", "tier": "B", "ats_type": "oracle",
     "host": "ibqbjb.fa.ocs.oraclecloud.com", "site": "Honeywell"},

    # ── manual / custom (no public API) — click-through, ranked by brand ────
    {"name": "Tesla", "tier": "S", "ats_type": "manual", "url": "https://www.tesla.com/careers/search/?type=3"},
    {"name": "Apple", "tier": "S", "ats_type": "manual", "url": "https://jobs.apple.com/en-us/search?team=internships"},
    {"name": "Google", "tier": "S", "ats_type": "manual", "url": "https://www.google.com/about/careers/applications/jobs/results/?employment_type=INTERN"},
    {"name": "Meta", "tier": "S", "ats_type": "manual", "url": "https://www.metacareers.com/jobs?is_intern=true"},
    {"name": "Microsoft", "tier": "S", "ats_type": "manual", "url": "https://careers.microsoft.com/v2/global/en/students"},
    {"name": "Netflix", "tier": "S", "ats_type": "manual", "url": "https://explore.jobs.netflix.net/careers"},
    {"name": "Uber", "tier": "A", "ats_type": "manual", "url": "https://www.uber.com/us/en/careers/list/?query=intern"},
    {"name": "Rippling", "tier": "B", "ats_type": "manual", "url": "https://ats.rippling.com/rippling/jobs"},
    # Added 2026-09-12. Avature, no public API. Tier A so hot_watch's S/A scope and the
    # coverage digest both treat it as a target the moment the aggregators list it.
    {"name": "Bloomberg", "tier": "A", "ats_type": "manual",
     "url": "https://bloomberg.avature.net/careers/SearchJobs/intern"},
]


def boards() -> list[dict]:
    return list(BOARDS)


def tier_of(name: str) -> str:
    """Brand tier for a company name (substring-tolerant). Default 'C'."""
    from internship_scraper import normalize_company_name
    nn = normalize_company_name(name)
    for b in BOARDS:
        bn = normalize_company_name(b["name"])
        if nn and (nn == bn or nn in bn or bn in nn):
            return b["tier"]
    return "C"
