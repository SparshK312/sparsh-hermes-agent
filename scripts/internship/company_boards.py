#!/usr/bin/env python3
"""
company_boards.py — the brand-first target list for lane-1 curation.

One entry per target company → its ATS board, so curate.py can pull every open
SWE/ML/PM intern role with the full JD directly from the JSON API (no browser).

Schema per entry:
  name       display name
  tier       "S" | "A" | "B"  — brand tier (drives hotness; see hotness.py)
  ats_type   "greenhouse" | "lever" | "ashby" | "workable" | "workday" |
             "smartrecruiters" | "manual"
  + ats-specific identifiers:
     greenhouse:      token=<board_token>
     lever:           site=<site>
     ashby:           org=<job_board_name>
     workable:        account=<account_slug>
     workday:         host=<tenant.wdN.myworkdayjobs.com>, site=<site>
     smartrecruiters: company=<Company>
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
    {"name": "Affirm", "tier": "B", "ats_type": "greenhouse", "token": "affirm"},

    # ── manual / custom (no public API) — click-through, ranked by brand ────
    {"name": "Tesla", "tier": "S", "ats_type": "manual", "url": "https://www.tesla.com/careers/search/?type=3"},
    {"name": "Apple", "tier": "S", "ats_type": "manual", "url": "https://jobs.apple.com/en-us/search?team=internships"},
    {"name": "Google", "tier": "S", "ats_type": "manual", "url": "https://www.google.com/about/careers/applications/jobs/results/?employment_type=INTERN"},
    {"name": "Meta", "tier": "S", "ats_type": "manual", "url": "https://www.metacareers.com/jobs?is_intern=true"},
    {"name": "Microsoft", "tier": "S", "ats_type": "manual", "url": "https://careers.microsoft.com/v2/global/en/students"},
    {"name": "Netflix", "tier": "S", "ats_type": "manual", "url": "https://explore.jobs.netflix.net/careers"},
    {"name": "Uber", "tier": "A", "ats_type": "manual", "url": "https://www.uber.com/us/en/careers/list/?query=intern"},
    {"name": "Rippling", "tier": "B", "ats_type": "manual", "url": "https://ats.rippling.com/rippling/jobs"},
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
