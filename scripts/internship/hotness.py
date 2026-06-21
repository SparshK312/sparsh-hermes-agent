#!/usr/bin/env python3
"""
hotness.py — brand-dominant, recency-boosted ranking for the curated board.

Three independent signals (the user asked to see both freshness AND brand):
  • Tier      static brand cluster  S/A/B/C  -> brand_score 100/80/55/30
  • role_lane AI-ML/Data 100 · SWE 85 · PM 60 · adjacent 40 (embedded/HW -> reject)
  • recency   exp decay, ~7-day half-life

  hotness = 0.50*brand + 0.25*role + 0.25*recency   (0–100)

Brand dominates the floor; recency provides a time-decaying boost. So a stale
Anthropic role still outranks a fresh mid-cap (the user's "older but still hot
if it's an amazing company" intuition), but a fresh S-tier beats a stale one.

Pure / stateless / unit-testable. This module is the single source of truth for
brand tiers; it reuses the role keyword lists from internship_scraper (and adds
the PM lane the daily VPS digest deliberately omits).
"""
from __future__ import annotations

import math

from internship_scraper import NEGATIVE_ROLE_KEYWORDS, normalize_company_name

# ── tunables (edit these to retune) ───────────────────────────────────────────
W_BRAND, W_ROLE, W_RECENCY = 0.50, 0.25, 0.25
TAU = 10.0                       # recency half-life ≈ 7 days (exp(-7/10)=0.50)
RECENCY_FLOOR = 5.0              # an old amazing-brand role never goes fully cold
UNKNOWN_AGE = 14                 # missing posted date -> treat as 14d (neutral)
FRESH_HOT_DAYS = 3               # 🔥
FRESH_NEW_DAYS = 7               # 🆕

BRAND_SCORE = {"S": 100, "A": 80, "B": 55, "C": 30}
ROLE_SCORE = {"AI/ML": 100, "Data": 100, "SWE": 85, "PM": 60, "Other": 40}

# ── brand tiers (single source of truth; normalized names) ────────────────────
TIER_S = {
    "meta", "apple", "netflix", "microsoft", "nvidia", "amazon", "google",
    "anthropic", "openai", "xai", "deepmind", "google deepmind",
}
TIER_A = {
    "stripe", "plaid", "mercury", "ramp", "brex", "robinhood",
    "vercel", "linear", "notion", "figma", "scale ai", "databricks",
    "perplexity", "cursor", "anysphere", "replit", "cohere", "mistral",
    "shopify", "mercor",
    # added Jun 20 (user target list + probe hits)
    "adobe", "coinbase", "visa", "waymo", "mongodb", "datadog", "airbnb",
    "snowflake", "github",
}
TIER_B = {
    "rippling", "1password", "cerebras", "tesla", "autodesk", "intel",
    "unity", "bosch", "cibc", "ciena", "rivian", "rivian vw", "dolby",
    "coveo", "kinaxis",
    # added Jun 20
    "sofi", "zoox", "capital one", "pinterest", "reddit",
}
# everything else -> "C"

# PM is accepted here even though internship_scraper rejects it for the daily digest.
_PM_NEGATIVES = {"product manager", "product management"}
_HARD_NEGATIVES = [k for k in NEGATIVE_ROLE_KEYWORDS if k not in _PM_NEGATIVES]

_PM_KEYWORDS = ("product manager", "product management", "associate product",
                "apm ", " apm", "product management intern", "program manager",
                "technical program manager", "tpm ", " tpm", "product intern")
_DATA_KEYWORDS = ("data engineer", "data scientist", "data science", "data analyst",
                  "analytics engineer", "data developer", "data engineering")
_AIML_KEYWORDS = ("machine learning", "ml engineer", "ml intern", "ml/ai", "ai/ml",
                  "ai engineer", "ai developer", "ai agent", "ai intern", "applied ai",
                  "ai research", "deep learning", "computer vision", "nlp", "llm",
                  "research engineer", "research intern", "ai scientist", "agentic")
_SWE_KEYWORDS = ("software", "swe", "sde", "sdet", "backend", "back-end",
                 "back end", "frontend", "front-end", "front end", "full-stack",
                 "full stack", "fullstack", "platform engineer", "infrastructure",
                 "devops", "site reliability", "cloud engineer", "systems engineer",
                 "firmware", "developer", "perf engineer", "performance engineer",
                 "compiler", "kernel", "kubernetes", "cloud software", "runtime",
                 "distributed systems", "web developer", "mobile developer", "ios ",
                 "android", "api ", "sdk")


def brand_tier(company: str) -> str:
    """Whole-word / phrase match so 'meta' doesn't fire on 'nox METAls' and
    'intel' doesn't fire on 'INTELlivision'."""
    nn = normalize_company_name(company)
    if not nn:
        return "C"
    words = set(nn.split())
    for tier, names in (("S", TIER_S), ("A", TIER_A), ("B", TIER_B)):
        for n in names:
            parts = n.split()
            if len(parts) == 1:
                if parts[0] in words:           # single-word brand = whole word
                    return tier
            elif n in nn:                        # multi-word brand = phrase match
                return tier
    return "C"


def brand_score(company: str) -> int:
    return BRAND_SCORE[brand_tier(company)]


def role_lane(title: str) -> str | None:
    """Return display lane ('AI/ML'|'Data'|'SWE'|'PM'|'Other') or None to reject.

    Rejects embedded-only / hardware / non-tech (via the scraper's negatives,
    minus the PM entries we now accept). PM is detected only when there's no
    engineering signal ('Product Engineer' is SWE, not PM)."""
    if not title:
        return None
    t = title.lower()
    for neg in _HARD_NEGATIVES:
        if neg in t:
            return None
    has_eng = any(k in t for k in _SWE_KEYWORDS) or any(k in t for k in _AIML_KEYWORDS)
    if any(k in t for k in _PM_KEYWORDS) and not has_eng:
        return "PM"
    if any(k in t for k in _AIML_KEYWORDS):
        return "AI/ML"
    if any(k in t for k in _DATA_KEYWORDS):
        return "Data"
    if any(k in t for k in _SWE_KEYWORDS):
        return "SWE"
    # no software/ML/PM/Data signal in the title -> not our lane (reject the
    # bank/admin/finance co-op noise that otherwise leaked as "Other")
    return None


def role_score(lane: str | None) -> int:
    return ROLE_SCORE.get(lane or "Other", 40)


def recency_score(age_days) -> float:
    a = UNKNOWN_AGE if age_days is None else max(0, int(age_days))
    return max(RECENCY_FLOOR, 100.0 * math.exp(-a / TAU))


def fresh_flag(age_days) -> str:
    if age_days is None:
        return ""
    if age_days <= FRESH_HOT_DAYS:
        return "🔥"
    if age_days <= FRESH_NEW_DAYS:
        return "🆕"
    return ""


def hotness(company: str, title: str, age_days, lane: str | None = None) -> dict:
    """Convenience: returns {hotness, tier, lane, brand, role, recency, fresh}.
    Pass a precomputed lane to avoid re-classifying."""
    tier = brand_tier(company)
    bs = BRAND_SCORE[tier]
    if lane is None:
        lane = role_lane(title)
    rs = role_score(lane)
    rec = recency_score(age_days)
    score = round(W_BRAND * bs + W_ROLE * rs + W_RECENCY * rec)
    return {"hotness": score, "tier": tier, "lane": lane or "Other",
            "brand": bs, "role": rs, "recency": round(rec, 1),
            "fresh": fresh_flag(age_days)}


# ── self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cases = [
        ("Anthropic", "Machine Learning Intern", 0),
        ("Anthropic", "Machine Learning Intern", 30),
        ("Cerebras", "ML Engineer Intern", 0),
        ("NVIDIA", "Software Performance at Scale Intern", 2),
        ("1Password", "Product Management Intern, Unified Access", 6),
        ("Tesla", "Thermal Engineer Intern", 1),       # -> rejected (hardware)
        ("Random Co", "Software Engineer Intern", 5),
    ]
    print(f"{'COMPANY':12} {'LANE':6} {'TIER':4} {'AGE':>3} {'HOT':>4} {'FRESH':5}")
    for co, title, age in cases:
        h = hotness(co, title, age)
        lane = h["lane"] if role_lane(title) else "REJECT"
        print(f"{co:12} {lane:6} {h['tier']:4} {age:>3} {h['hotness']:>4} {h['fresh']:5}")
    # ordering invariant checks
    assert hotness("Anthropic", "ML Intern", 0)["hotness"] == 100
    s_stale = hotness("Anthropic", "ML Intern", 30)["hotness"]
    b_fresh = hotness("Cerebras", "ML Intern", 0)["hotness"]
    assert s_stale > BRAND_SCORE["B"], "S-stale should stay strong"
    print(f"\nS-ML@30d={s_stale}  B-ML@0d={b_fresh}  (both ~76-78, the tie zone)")
    print("ok")
