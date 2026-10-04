#!/usr/bin/env python3
"""
target_bar.py — WHICH COMPANIES ARE WORTH APPLYING TO, per season. The single source.

Added 2026-10-04, the day after Microsoft Summer 2027 was accepted. His words:
  "there is a difference between the companies that i actually want to accept an
   offer from vs companies that i don't mind just applying to and just seeing
   whatever happens."

So there are THREE answers, not two:
  accept  -> worth reneging on Microsoft for (Google, Meta, Apple, frontier labs,
             Jane Street, Citadel). Sorted first.
  apply   -> apply and see (named big tech, quants, good startups, Shopify-level+).
  below   -> everything else. NOT rejected: it renders on the "Below Bar" tab
             ("we considered them, they wouldn't be bad, we're aiming higher").

🔴 THIS IS DELIBERATELY NOT THE TIER TABLE (hotness.TIER_* / company_boards tier).
Tier drives HARVESTING — SWElist tier-C rows are dropped at wide_net_source.py:199,
the enrichment cap protects only S/A/B, hot_watch polls S/A boards, coverage_digest
reports tier C. Retiering to encode the bar would DELETE below-bar rows at harvest
instead of SHOWING them on Below Bar, and board tiers override the hotness sets for
any company with a board entry. A separate module changes routing only.

🔴 ROUTING ONLY, NEVER A STATUS. The bar decides which TAB a 'To Apply' row renders
on. It never writes Status — a machine value rendered into a human column is read
back as his decision (CLAUDE.md, 2026-09-08, 279 fabricated "Closed" rows).

TO PROMOTE OR DEMOTE A COMPANY: edit the sets below, run test_board_invariants.py,
deploy. Names are NORMALIZED (internship_scraper.normalize_company_name: lowercase,
punctuation -> space, suffixes inc/llc/ltd/corp/technologies/labs/group/holdings/
company/co stripped). Single-word names match a WHOLE WORD; multi-word names match as
a bounded phrase (hotness._name_matches). A one-word name that is also a common word
or another company's first word ("viking", "relativity", "toyota", "garda") must be
written as its multi-word form.

Dependency-free (no openpyxl) so board_facts / the coach crons on /usr/bin/python3
can import it.
"""
from __future__ import annotations

from hotness import TIER_EXCEPTIONS, _name_matches, normalize_company_name, words0

ACCEPT, APPLY, BELOW = "accept", "apply", "below"
WINTER, SUMMER = "winter", "summer"

# ── ACCEPT: he would renege on Microsoft Summer 2027 for these ────────────────
ACCEPT_NAMES = {
    "google", "alphabet", "meta", "apple",
    "openai", "anthropic", "deepmind", "google deepmind", "xai", "x ai",
    # Top-pay quants, his call 2026-10-04: "they pay top dollar"
    "jane street", "citadel", "citadel securities",
}

# ── APPLY: apply and see ───────────────────────────────────────────────────────
APPLY_NAMES = {
    # Round 1 (2026-10-04)
    "amazon", "aws", "nvidia", "netflix", "stripe", "databricks", "palantir",
    "tesla", "spacex", "waymo", "snowflake", "figma", "ramp", "scale ai", "notion",
    "two sigma", "de shaw", "d e shaw", "susquehanna", "optiver", "imc",
    "imc trading", "five rings", "drw",
    "lyft", "robinhood", "coinbase", "doordash", "adobe", "salesforce",
    "together ai", "harvey",
    # Round 2 (2026-10-04)
    "pinterest", "uber", "airbnb", "datadog", "plaid", "vercel", "duolingo",
    "anduril", "neuralink", "perplexity", "glean", "epic games", "rubrik",
    "autodesk", "intuit", "american express", "amex", "nasdaq", "intel",
    "qualcomm", "waabi", "superhuman", "mercury", "rivian",
    # Extras under his "if unsure, include" rule (2026-10-04)
    "g research", "viking global", "aqr", "aqr capital", "schonfeld", "blackstone",
    "chicago trading", "virtu", "virtu financial", "belvedere trading",
    "hyannis port", "garda capital", "walleye", "walleye capital", "old mission",
    "transmarket", "dv trading", "marvell", "hubspot", "red hat", "mastercard",
    "walt disney", "disney", "zipline", "skydio", "relativity space", "astranis",
    "toyota research institute", "klaviyo",
    # Winter "Shopify-level or better" (2026-10-04): Wealthsimple is about the line
    "wealthsimple", "cohere",
    # Already applied to / in-process and plainly at the bar; included under the same
    # "if unsure, include" rule so the queue never hides a live target. ⬜ He has not
    # ruled on these individually — strike any he doesn't want.
    "mercor", "tiktok", "bytedance", "bloomberg", "point72", "jump trading",
    "hudson river trading", "pdt partners", "sierra", "replit", "cursor",
    "anysphere", "cognition", "mistral", "elevenlabs", "etched", "cerebras",
    "dropbox", "paypal", "visa", "blue origin", "roblox", "discord", "reddit",
}

# Applies only to WINTER rows. Microsoft Summer 2027 is accepted (2026-10-02), so a
# Summer Microsoft req is moot; a Winter one stays worth applying to (his call
# 2026-10-04: "Keep Winter reqs on the Winter tab").
WINTER_ONLY_APPLY = {"microsoft"}

# Look-alikes that borrow a listed name but are a different company. Measured
# 2026-10-04 against the 887-name corpus + adversarial probes. hotness.TIER_EXCEPTIONS
# is checked too (Sierra Nevada, Mercury Insurance, Citadel Credit Union, ...).
EXTRA_EXCEPTIONS = {
    "imc companies", "imc health", "ramp network", "mercury marine",
    "mercury financial", "cohere health", "citadel federal credit union",
    "epic systems", "stand together", "intel 471", "uber freight",
}


def _matches_any(names: set[str], nn: str, words: set[str]) -> bool:
    return any(_name_matches(n, nn, words) for n in names)


def is_excepted(company: str) -> bool:
    nn = normalize_company_name(company or "")
    w = words0(nn)
    return (_matches_any(TIER_EXCEPTIONS, nn, w)
            or _matches_any(EXTRA_EXCEPTIONS, nn, w))


def season_of(cycle: str) -> str:
    """Which queue tab a row's cycle belongs on.

    Winter if the cycle names Winter 2027 OR Spring 2027 — US employers label the
    Jan-Apr term "Spring" (internship_scraper.py:473-476, worklist.py:45-47) — even
    when Summer is also listed ("Summer or Winter" rows go where he looks first).
    EVERYTHING ELSE is Summer, including blank: an unlabelled "2027 Software Engineer
    Intern" is almost always a summer role (his call 2026-10-04). The Cycle cell
    stays blank on the Sheet, so an unconfirmed cycle remains visible as such.
    """
    c = (cycle or "").lower()
    if "winter 2027" in c or "spring 2027" in c:
        return WINTER
    return SUMMER


def bar_of(company: str, season: str) -> str:
    """accept | apply | below for this company in this season."""
    nn = normalize_company_name(company or "")
    if not nn:
        return BELOW
    w = words0(nn)
    if _matches_any(TIER_EXCEPTIONS, nn, w) or _matches_any(EXTRA_EXCEPTIONS, nn, w):
        return BELOW
    if _matches_any(ACCEPT_NAMES, nn, w):
        return ACCEPT
    if _matches_any(APPLY_NAMES, nn, w):
        return APPLY
    if season == WINTER and _matches_any(WINTER_ONLY_APPLY, nn, w):
        return APPLY
    return BELOW


BAR_RANK = {ACCEPT: 0, APPLY: 1, BELOW: 2}


if __name__ == "__main__":
    import sys
    for name in sys.argv[1:] or ["Google", "D. E. Shaw & Co.", "Rippling", "Microsoft"]:
        print(f"{name!r:40} summer={bar_of(name, SUMMER):6} winter={bar_of(name, WINTER)}")
