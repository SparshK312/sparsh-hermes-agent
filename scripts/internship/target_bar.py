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

import re

from hotness import TIER_EXCEPTIONS, _name_matches, normalize_company_name, words0

ACCEPT, APPLY, BELOW = "accept", "apply", "below"
WINTER, SUMMER = "winter", "summer"
# 2026-11-11, his call: Fall 2027 (Sep-Dec) is takeable alongside capstone in Toronto,
# for a TOP-TIER name only. Its own season with its own, much shorter list (FALL_NAMES).
FALL = "fall"

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
    # 2026-10-09, his call: Figure AI is A tier, both seasons (was Winter-only for an
    # hour as part of the group-A batch).
    "figure", "figure ai",
    # 2026-10-09, his OK: Exa (AI web-search lab, formerly Metaphor; boards still use
    # both names). BOTH seasons, not Winter-only: its intern posting names no term
    # ("take a semester off"), so the row's cycle is blank and blank routes as Summer.
    # Winter-only left it on Below Bar after the first deploy (8ace000).
    "exa", "metaphor",
    # 2026-10-11, his call: the Winter 2027 OUTREACH targets (prestigious, fast-growing
    # AI startups in SF/NYC; company_boards.py has the same list, wired at tier A). BOTH
    # seasons for the Exa reason above: most of their intern posts name no term, and a
    # blank cycle routes as Summer, so Winter-only would bury them on Below Bar.
    # Names match company_boards.py (normalized); "clay" and "warp" are single words that
    # hit 0 of the 976 store names on 2026-10-11 (TB-OR pins the look-alikes).
    "parallel web systems", "firecrawl", "langchain", "braintrust", "inferact",
    "prime intellect", "factory ai", "applied compute", "e2b", "modal",
    "general intuition", "baseten", "fireworks ai", "fal ai", "browserbase", "radixark",
    "lmarena", "rogo", "basis ai", "clay", "warp", "physical intelligence",
    "dyna robotics",
    # Round 2 (2026-10-12), same rule; names match company_boards.py.
    "reducto", "ampersand ai", "typesafe ai", "sapiom", "namespace", "worldlabs",
    # Round 3 (2026-10-12), same rule.
    "mecka ai", "xdof", "flow engineering", "stuut", "gimletlabs", "eliseai", "writer ai",
    "lightning ai", "normal computing",
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

# ── FALL 2027: top tier only (2026-11-11) ───────────────────────────────────────
# Fall ACCEPT = ACCEPT_NAMES (checked first in bar_of). Fall APPLY = THIS list and nothing
# else. 🔴 APPLY_NAMES does NOT carry over: bar_of's FALL branch returns before the
# APPLY_NAMES line, so ~150 apply-tier names cannot flood the Fall tab. Big tech, the
# frontier labs and unicorns with a real Toronto/Canada engineering presence, and two
# top quants. A Fall term means a term off capstone, so the bar is "worth that", not
# "worth applying to". ⬜ My pick from his examples (Amazon, Google, Meta, Apple,
# Microsoft, NVIDIA, Cohere, Shopify, Uber, Stripe, Databricks, OpenAI, Anthropic,
# Waabi); he has not ruled on each name. Strike or add here, then run the suite.
FALL_NAMES = {
    "amazon", "aws", "microsoft", "nvidia",
    "cohere", "shopify", "uber", "stripe", "databricks", "waabi",
    "two sigma", "hudson river trading",
}

# Applies only to WINTER rows. Microsoft Summer 2027 is accepted (2026-10-02), so a
# Summer Microsoft req is moot; a Winter one stays worth applying to (his call
# 2026-10-04: "Keep Winter reqs on the Winter tab").
WINTER_ONLY_APPLY = {
    "microsoft",
    # 2026-10-09, his call the day Wealthsimple (the Winter backup) said no and the
    # Shopify extension became a 50/50 floor: "promote the group A companies".
    # Known tech/AI names that sat on Below Bar. WINTER ONLY: Summer is filled by
    # Microsoft, and several of these (Cloudflare, AMD, Abridge, Domino) are his
    # explicit Oct-4 Summer exclusions, which TB1 still pins.
    "cloudflare", "amd", "abridge", "zip", "kodiak robotics", "gemini",
    "geotab", "stackadapt", "kinaxis", "domino data lab", "formlabs", "prizepicks",
    "knowbe4", "entrust", "soti",
    # 2026-10-14, his call on a Winter-postings triage: Brain Co. (applied-AI
    # startup, ML co-op Jan 11 - Apr 30 2027, SF; NOT the BCI company BrainCo) and
    # Heliux (SF manufacturing-AI startup, Spring 2027 SWE intern). Winter only.
    # (Brain Co. is in WINTER_ONLY_APPLY_EXACT below: its name normalizes to "brain".)
    "heliux",
    # 2026-10-17, his call ("make sure we put all the ones worth applying to the winter
    # apply list"), from the SWElist Oct 9-17 triage. Winter only:
    #   Assort Health -- SF, $120M Series C, "Software Engineering Internship (Jan - Apr
    #     2027)", Jan 11 - Apr 30 (ashby assorthealth 3d8a2c74). Voice-first healthcare AI.
    #   Atoms -- Kalanick's City Storage Systems rebrand, $1.7B a16z round Jul 2026;
    #     "Software Engineering Intern - 2027 Winter Term", early Jan - late Apr
    #     (greenhouse atoms 8356071002). States a 3.7 GPA minimum; he applies anyway.
    "assort health", "atoms",
    # 2026-10-18, his call: Mechanize (SF RL-environments startup for frontier labs;
    #   backed by Collison, Friedman, Gross, Jeff Dean). Intern post states no term, so
    #   it rides in via manual_postings with cycle "Winter 2027". Winter only.
    "mechanize",
    # 2026-10-19, his call ("sure u can add heygen, datarobot"), from the SWElist / Intern
    #   Insider Oct 19 triage. Winter only:
    #   HeyGen -- AI video; "Software Engineer Intern (Winter 2027)" (greenhouse heygen
    #     4540716007), Toronto/LA/SF/Singapore. Quals: "within one year of graduation" (a stretch).
    #   DataRobot -- MCP & Memory Intern (Workday R-102755), via manual_postings (no term stated).
    "heygen", "datarobot",
    # 2026-10-22, his call ("yea sure you can promote it"): 1Password was never named on
    #   any list, so it defaulted to Below Bar; not a ruling. Six "Developer Intern ...
    #   Winter 2027" reqs posted Oct 22 (ashby 1password), Jan - Apr 2027, remote
    #   Canada/US. At least the level of the Oct-9 group A (Geotab, StackAdapt, Kinaxis).
    #   Winter only.
    "1password",
    # 2026-10-29, his call ("sure add this to the board"): Genius AI (formerly GlossGenius),
    #   NYC salon/wellness-booking software; $44M Series D led by Lux at a $1.15B valuation
    #   (Jul 21 2026). "Software Engineer Intern Winter 2026-2027", Jan 19 - Apr 30 2027,
    #   via manual_postings (ashby geniusai 96ebddc5). Winter only.
    "genius ai",
}

# Companies whose names collapse to something generic once normalized, so they are
# matched on the RAW name (lowercased, trailing "." stripped) instead. "Brain Co."
# normalizes to "brain", the same as "Brain Corp"; a word match would also hit
# "Second Brain Labs". (2026-10-14)
WINTER_ONLY_APPLY_EXACT = {"brain co"}

# 2026-10-09, same call, group B: big-name enterprises, but "promote only roles that
# are a direct fit for my resume". So the COMPANY alone never promotes these; a Winter
# row is promoted only when its role clears winter_fit_promotes() below. Everything
# else at these companies stays on Below Bar.
WINTER_FIT_ONLY = {
    "general motors", "johnson & johnson", "honeywell", "royal bank of canada", "rbc",
    "manulife", "sun life", "cibc", "intact", "liberty mutual", "home depot", "bose",
    "bosch", "nokia", "ge aerospace",
}
# "Direct fit": an SWE or AI/ML lane, a scored JD at or above this, no disqualifier.
# An unscored row (no JD read) is NOT promoted: unread is not evidence of fit.
WINTER_FIT_LANES = {"SWE", "AI/ML"}
WINTER_FIT_MIN = 80

# Look-alikes that borrow a listed name but are a different company. Measured
# 2026-10-04 against the 887-name corpus + adversarial probes. hotness.TIER_EXCEPTIONS
# is checked too (Sierra Nevada, Mercury Insurance, Citadel Credit Union, ...).
EXTRA_EXCEPTIONS = {
    "imc companies", "imc health", "ramp network", "mercury marine",
    "mercury financial", "cohere health", "citadel federal credit union",
    "epic systems", "stand together", "intel 471", "uber freight",
    "robert bosch venture capital",
}


def _matches_any(names: set[str], nn: str, words: set[str]) -> bool:
    return any(_name_matches(n, nn, words) for n in names)


def is_excepted(company: str) -> bool:
    nn = normalize_company_name(company or "")
    w = words0(nn)
    return (_matches_any(TIER_EXCEPTIONS, nn, w)
            or _matches_any(EXTRA_EXCEPTIONS, nn, w))


def season_of(cycle: str, title: str = "") -> str:
    """Which queue tab a row's cycle belongs on.

    Winter if the cycle names Winter 2027 OR Spring 2027 — US employers label the
    Jan-Apr term "Spring" (internship_scraper.py:473-476, worklist.py:45-47) — even
    when Summer is also listed ("Summer or Winter" rows go where he looks first).
    Fall (2026-11-11) ONLY when the cycle names Fall/Autumn 2027 and no Winter, Spring
    or Summer 2027: precedence is Winter > Summer > Fall, so "Summer 2027, Fall 2027"
    is a Summer row. A title saying "Fall" with no year never makes a row Fall.
    EVERYTHING ELSE is Summer, including blank: an unlabelled "2027 Software Engineer
    Intern" is almost always a summer role (his call 2026-10-04). The Cycle cell
    stays blank on the Sheet, so an unconfirmed cycle remains visible as such.
    """
    c = (cycle or "").lower()
    if "winter 2027" in c or "spring 2027" in c:
        return WINTER
    # 2026-10-04: a cycle-less row whose TITLE names the winter term. Jane Street's
    # "Software Engineer (Winter Co-Op)" (Jan-Apr, NYC) carries no year anywhere, so
    # _cycle_label leaves `cycle` blank and the row defaulted to the Summer tab — the
    # single most valuable Winter req on the board, filed under the wrong season. Only
    # consulted when the cycle names no season+year at all, so a labelled row is never
    # overridden by its title.
    if not re.search(r"\b(winter|spring|summer|fall|autumn)\s+20\d\d\b", c) and \
            re.search(r"\b(winter|spring)\b", (title or "").lower()):
        return WINTER
    if "summer 2027" in c:
        return SUMMER
    if re.search(r"\b(fall|autumn)\s+2027\b", c):
        return FALL
    return SUMMER


# Canadian signal for fall_location_ok. Province CODES are matched case-sensitively and
# only in the positions ATS boards put them ("Toronto, ON", "CA, ON, Toronto",
# "CA-ON-Toronto"), so "ON-SITE" or a stray "ab" cannot read as a province. A bare "CA"
# is NEVER Canadian here: "Mountain View, CA" is California.
_CA_CODES = "AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT"
# No `^` alternative (review, 2026-11-11): a LEADING code is how ISO country codes are
# written ("NL, Amsterdam", "PE, Lima", "SK, Bratislava"), and it read those as provinces.
# Amazon's Canadian form "CA, ON, Toronto" is still caught by the `,\s*` branch.
_CA_CODE_RE = re.compile(rf"(?:,\s*|\bCA-)({_CA_CODES})(?![A-Za-z])(?![\s-]*(?:SITE|Site|site))")
_CA_WORD_RE = re.compile(
    r"\bcanad|\bontario\b|\bquebec\b|\bqu[ée]bec\b|\bbritish columbia\b|\balberta\b|"
    r"\bmanitoba\b|\bsaskatchewan\b|\bnova scotia\b|\bnew brunswick\b|\bnewfoundland\b|"
    r"\bprince edward island\b|"
    r"\btoronto\b|\bwaterloo\b|\bkitchener\b|\bottawa\b|\bkanata\b|\bmontr[ée]al\b|"
    r"\bvancouver\b|\bburnaby\b|\bcalgary\b|\bedmonton\b|\bmarkham\b|\bmississauga\b|"
    r"\bnorth york\b|\boakville\b|\bvaughan\b|\brichmond hill\b|\bhalifax\b|\bwinnipeg\b")


def fall_location_ok(loc: str) -> bool:
    """Can a FALL 2027 row at this location be taken alongside capstone in Toronto?

    FAILS OPEN. True for Canada / a Canadian city or province / anything remote / blank /
    unclear text ("Multiple Locations", "Hybrid", "CANADA STORE SUPPORT CENTER"), and for a
    multi-city string with any Canadian city in it. False ONLY for a location that is
    clearly somewhere else and on-site ("Mountain View, CA", "New York, NY", "London, UK").
    A False row is routed to Below Bar, never dropped and never sent to Reviewed: the
    cost of a wrong False is a row he has to look one tab over for, so ambiguity is True.
    """
    s = (loc or "").strip()
    if not s:
        return True
    low = s.lower()
    if "remote" in low:
        return True
    if _CA_WORD_RE.search(low) or _CA_CODE_RE.search(s):
        return True
    from internship_scraper import classify_location
    status, reason = classify_location(s)
    if status == "match":                 # a US / North-American signal and no Canadian one
        return False
    if status == "reject" and not reason.startswith("no US/Canada signal"):
        return False                      # a named non-North-American country or city
    return True                           # no recognisable place: fail open


# 2026-10-14, his call: postings that DO offer a Jan-Apr 2027 term but never say so in
# a form the cycle reader can parse, so their Cycle is blank and season_of() routes
# them to Summer. Keyed on (normalized company, exact role title, lowercased) so a new
# posting at the same company is NOT swept in -- each entry is a verified, named req.
#   Clay  "Software Engineering Intern" (ashby claylabs 5b7eced2): the term lives only
#         in the application form -- "Winter (January~April)" / "Summer (May~August)".
#   Exa   "Software Engineer, Intern"   (ashby exa a9e01521): "willing to take off a
#         semester to work full time in SF"; staged for Winter Oct 9.
WINTER_TERM_OFFERED = {
    ("clay", "software engineering intern"),
    ("exa", "software engineer, intern"),
    # HeyGen  "Software Engineer Intern" (greenhouse heygen 4540716007): the employer title says
    #         "(Winter 2027)", the board row's title does not (2026-10-19).
    ("heygen", "software engineer intern"),
    # 2026-10-29 Apply - Summer audit (vault: "Apply - Summer Winter Audit (Oct 29)"). No term
    # in the JD; he picks the start in the form or the posting runs year-round. His call.
    # Uber   "Software Engineer Intern" (Oracle HCM 304178 Toronto, 303922 US): "internship
    #        and co-op program", term picker behind the email gate; check it when applying.
    #        The role string is generic: a later Summer-only Uber req with this exact title
    #        would route here too. Re-check Uber rows on Apply - Winter after the cycle turns.
    ("uber", "software engineer intern"),
    # Waabi  "Research Internship/Co-op" (lever waabi 0fd4e30b): the form asks the start date
    #        AND the number of months.
    ("waabi", "research internship/co-op"),
    # (Palantir Query Engines was added here on Oct 29 and removed the same night: its JD says
    #  "This should be your final internship before graduating", so a Jan-Apr term with Microsoft
    #  Summer 2027 after it doesn't qualify. Rows marked Skip, his call.)
    # Epic Games (greenhouse 6178818004, 6219626004, Cary NC): "flexible start date in 2027".
    ("epic games", "technical product management intern"),
    ("epic games", "sdet intern"),
    # 2026-11-09, his call. Etched "IA Research Engineer Intern" (ashby etched 05d67dff): the JD
    # says "Spring '27 and Summer '27" and the cycle reader takes Summer; the application form's
    # term picker reads "Winter (January - May)". Same shape as Clay.
    ("etched", "ia research engineer intern"),
}


# 2026-11-09, his call: the same idea keyed on the board row's _id, for when the TITLE cannot
# be the key. Tesla's own feed titles these "(Winter/Spring 2027)", but the board rows came in
# via Simplify with no term, so they routed to Summer. Title keys would be wrong here: Tesla
# 289240 (Winter/Spring 2027) and 289241 (Summer 2027) carry the IDENTICAL role string.
# Verified against tesla.com/cua-api/apps/careers/state, Nov 9 2026.
WINTER_TERM_IDS = {
    "www.tesla.com/careers/search/job/289240",  # SI Reinforcement Learning (Winter/Spring 2027)
    "www.tesla.com/careers/search/job/285801",  # Build & Simulation Platform
    "www.tesla.com/careers/search/job/284286",  # Supercharger SIL Test Infrastructure SWE
    "www.tesla.com/careers/search/job/282250",  # Software Validation, Steering/Chassis
    "www.tesla.com/careers/search/job/282246",  # Software Validation, Vehicle Firmware
}


def winter_term_offered_id(cid: str) -> bool:
    """True for a board row (_id) verified to be a Winter 2027 posting its text can't show."""
    return (cid or "").strip() in WINTER_TERM_IDS


# 2026-11-11, his call: the Fall twin of WINTER_TERM_IDS. Board rows (_id, i.e.
# internship_scraper.canonical_id of the URL, as stored and in the Sheet's hidden column A)
# verified to offer a FALL 2027 term that their cycle label cannot show. Routed to
# Apply - Fall whatever the label says. Amazon has no separate Fall posting: Fall 2027 is a
# season option inside these three Canadian reqs, labelled Summer / "2027". Verified on
# amazon.jobs Nov 11 2026 (vault: "Fall 2027 Watcher - Build Plan"). The _ids below are the
# store keys read off the VPS store on Nov 11 (the www. variants; the bare amazon.jobs/...
# twins are dead duplicates).
# 🔴 PRECEDENCE: Winter wins. A row whose cycle says Winter/Spring 2027, or that is in
# WINTER_TERM_OFFERED / WINTER_TERM_IDS, stays on Apply - Winter even if listed here
# (Winter 2027 is his priority slot; build_curated_xlsx.queue_placement applies this).
FALL_TERM_IDS = {
    # SDE Intern - Summer 2027 (CAN): "Fall 2027 (Start August/September; End
    # November/December)" is one of the season options; all Canadian intern sites.
    "www.amazon.jobs/en/jobs/10553947/software-development-engineer-intern-summer-2027-can",
    # ML Systems SDE Intern, Annapurna Labs, Toronto: "starts January 2027, May 2027,
    # September 2027".
    "www.amazon.jobs/en/jobs/10538066/ml-systems-software-development-engineer-intern-annapurna-labs-2027",
    # SDE Intern, ROBOTICS - 2027, Toronto: "summer intern and spring/fall co-op roles".
    "www.amazon.jobs/en/jobs/10535280/software-development-engineer-intern-robotics-2027",
}


def fall_term_offered_id(cid: str) -> bool:
    """True for a board row (_id) verified to offer a Fall 2027 term its label can't show."""
    return (cid or "").strip() in FALL_TERM_IDS


def winter_term_offered(company: str, role: str) -> bool:
    """True for a named req verified to offer a Winter 2027 term its text can't show."""
    return (normalize_company_name(company or ""),
            (role or "").strip().lower()) in WINTER_TERM_OFFERED


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
    # 🔴 FALL returns HERE, before APPLY_NAMES, which apply in every season. Fall is top
    # tier only: FALL_NAMES or nothing (2026-11-11).
    if season == FALL:
        return APPLY if _matches_any(FALL_NAMES, nn, w) else BELOW
    if _matches_any(APPLY_NAMES, nn, w):
        return APPLY
    if season == WINTER and (_matches_any(WINTER_ONLY_APPLY, nn, w)
                             or (company or "").strip().lower().rstrip(".")
                             in WINTER_ONLY_APPLY_EXACT):
        return APPLY
    return BELOW


def winter_fit_promotes(company: str, lane: str, fit_score, disqualifier: str = "") -> bool:
    """True when a WINTER_FIT_ONLY company's row is a direct résumé fit (group B rule).

    Called only for Winter rows that bar_of() put BELOW. Never promotes on the company
    alone, never on an unscored row, never past a fit disqualifier."""
    nn = normalize_company_name(company or "")
    if not nn or is_excepted(company):
        return False
    if not _matches_any(WINTER_FIT_ONLY, nn, words0(nn)):
        return False
    if (lane or "") not in WINTER_FIT_LANES:
        return False
    if (disqualifier or "none") not in ("none", ""):
        return False
    try:
        return fit_score is not None and float(fit_score) >= WINTER_FIT_MIN
    except (TypeError, ValueError):
        return False


BAR_RANK = {ACCEPT: 0, APPLY: 1, BELOW: 2}


if __name__ == "__main__":
    import sys
    for name in sys.argv[1:] or ["Google", "D. E. Shaw & Co.", "Rippling", "Microsoft"]:
        print(f"{name!r:40} summer={bar_of(name, SUMMER):6} winter={bar_of(name, WINTER):6} "
              f"fall={bar_of(name, FALL)}")
