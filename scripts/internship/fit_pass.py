#!/usr/bin/env python3
"""
fit_pass.py — LLM "fit pass" for the curated internship board.

The board fetches the full JD for every posting but the rule-based relevance only
reads the TITLE. This reads the actual JD with a cheap model and returns, per posting:
  • fit_score 0-100  • fit_why (one line)  • disqualifier flag  • jd_summary
catching the JD-only disqualifiers title-rules miss (wrong cycle, PhD-required,
US-citizen/clearance-gated, 12/16-month placements).

Cost control: results are CACHED in each posting's machine record keyed by a JD
hash, so the LLM only ever scores genuinely-new/changed postings. First run ~94;
steady state ~0-10/run; a no-change re-run spends $0.

Model: gpt-5.4-mini by default (cheap classifier). Empirically validated by
test_fit_pass.py; flip FIT_MODEL to gpt-5.5 or a claude-* model if the eval fails.
Reuses internship_triage.py's proven urllib pattern (no SDK). Key from ~/.hermes/.env.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Applicant profile is loaded from a gitignored local file (not hardcoded here) so
# personal residency/citizenship details never live in this public repo.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from applicant_profile import load_profile  # noqa: E402

# ── config (swapping the model is a one-line edit) ────────────────────────────
FIT_MODEL = "gpt-5.4-mini"          # EMPIRICALLY VALIDATED 2026-06-20 (test_fit_pass.py: 11/11 recall,
#                                     0 false-pos incl. ITAR decoy, bands clean). -> "gpt-5.5" or
#                                     "claude-haiku-4-5" only if a future eval fails.
JD_TRUNC_CHARS = 2500               # head of the JD — eligibility/term language lives here (~714 tok)
CHUNK_SIZE = 8                      # postings per LLM call
MAX_LLM_PER_RUN = 120              # hard cost guard (first run is ~94)
PROMPT_VERSION = "fit-v1"          # bump to force re-score on prompt changes (part of cache key)

ENV_FILE = Path.home() / ".hermes" / ".env"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
VALID_DQ = {"none", "wrong-cycle", "phd-required", "citizenship",
            "clearance", "long-placement", "other"}
# rough per-MTok pricing (USD) for the cost log — confirm against the live dashboard
PRICE = {"gpt-5.4-mini": (0.25, 2.0), "gpt-5.5": (1.0, 8.0),
         "claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-4-6": (3.0, 15.0)}


def env(key: str) -> str | None:
    v = os.environ.get(key)
    if v:
        return v
    try:
        for ln in ENV_FILE.read_text().splitlines():
            if ln.startswith(f"{key}="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return None


def log(msg: str) -> None:
    print(f"[fit-pass] {msg}", file=sys.stderr)


# ── system prompt (eligibility language mirrors internship_triage.py) ─────────
SYS_PROMPT = (
    "You screen internship job descriptions for " + load_profile() + "\n\n"
    "For EACH posting you get the real job description (jd). Read it and return per posting:\n"
    "- fit_score: integer 0-100. 85-100 = target role (SWE/ML/Data intern) at a strong brand, "
    "eligible, right cycle. 60-84 = eligible, good role, weaker brand or vaguer fit. 40-59 = "
    "adjacent/uncertain. 0-39 = poor fit. ANY disqualifier => score <= 30.\n"
    "- fit_why: <= 14 words, plain, why this score (cite the JD when relevant).\n"
    "- disqualifier: ONE of [none, wrong-cycle, phd-required, citizenship, clearance, "
    "long-placement, other]. If several apply pick the most severe "
    "(citizenship/clearance > wrong-cycle > long-placement > phd-required > other).\n"
    "- jd_summary: 1-2 sentences (<= 240 chars) summarizing the role from the JD.\n\n"
    "Disqualifier definitions — be strict and literal:\n"
    "- wrong-cycle: the JD EXPLICITLY names a term OUTSIDE {Fall 2026, Winter 2027, Spring 2027, "
    "Summer 2027}, e.g. 'Summer 2026', 'Spring 2026', 'May-Aug 2026'. IMPORTANT: if the JD does NOT "
    "state a specific term, leave disqualifier=none. Do NOT guess a cycle.\n"
    "- phd-required: the JD REQUIRES an enrolled or completed PhD (not 'preferred', not Master's).\n"
    "- citizenship: requires US CITIZENSHIP specifically. NOTE: ITAR, 'US Person', "
    "green-card-acceptable, or 'must be authorized to work in the US' are NOT disqualifiers if the "
    "applicant is US-work-authorized (see the profile above). Flag ONLY if it says US Citizen / citizenship.\n"
    "- clearance: requires an ACTIVE security clearance (Secret / Top Secret / TS-SCI).\n"
    "- long-placement: the JD states a 12-month or 16-month (or 'year-long', '8+ month') placement. "
    "4-month / one-term / summer / standard co-op terms are FINE.\n"
    "- other: a genuine disqualifier not above (e.g. role physically located outside US/Canada). "
    "Use sparingly.\n"
    "- none: default. When in doubt, prefer none — a hallucinated disqualifier on a good role is "
    "WORSE than a missed one.\n\n"
    'Return a JSON object EXACTLY: {"items":[{"id":str,"fit_score":int,"fit_why":str,'
    '"disqualifier":str,"jd_summary":str}]}. Include EVERY id given. Use ONLY the postings provided.'
)


# ── model call (OpenAI + Claude branches, selected by model string) ───────────
def _call_model(model: str, user_content: str, max_tokens: int = 1500):
    """Return parsed JSON dict from the model, or None on failure (3 retries)."""
    is_claude = model.startswith("claude")
    key = env("ANTHROPIC_API_KEY" if is_claude else "OPENAI_API_KEY")
    if not key:
        log(f"no API key for {model}")
        return None
    if is_claude:
        body = json.dumps({
            "model": model, "max_tokens": max_tokens, "system": SYS_PROMPT,
            "messages": [{"role": "user", "content": user_content}],
        }).encode()
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        url = ANTHROPIC_URL
    else:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "system", "content": SYS_PROMPT},
                         {"role": "user", "content": user_content}],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": max_tokens,
        }).encode()
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        url = OPENAI_URL
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = json.loads(r.read())
            text = (raw["content"][0]["text"] if is_claude
                    else raw["choices"][0]["message"]["content"])
            usage = raw.get("usage", {})
            return _extract_json(text), usage
        except Exception as e:  # noqa: BLE001
            log(f"call failed ({model}, try {attempt + 1}): {e}")
    return None, {}


def _extract_json(text: str):
    """Parse JSON, tolerating a code-fence or leading prose (Claude sometimes wraps)."""
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1:
            try:
                return json.loads(text[s:e + 1])
            except Exception:  # noqa: BLE001
                return None
    return None


# ── per-item validation ───────────────────────────────────────────────────────
def _validate_item(it: dict, valid_ids: set) -> dict | None:
    if not isinstance(it, dict) or it.get("id") not in valid_ids:
        return None
    try:
        score = max(0, min(100, int(round(float(it.get("fit_score", 0))))))
    except (TypeError, ValueError):
        return None
    dq = str(it.get("disqualifier", "none")).strip().lower()
    if dq not in VALID_DQ:
        dq = "other" if dq and dq != "null" else "none"
    return {
        "fit_score": score,
        "fit_why": str(it.get("fit_why", ""))[:160].strip(),
        "fit_disqualifier": dq,
        "fit_jd_summary": str(it.get("jd_summary", ""))[:240].strip(),
    }


# ── shared scoring core (used by run_fit_pass AND the eval) ────────────────────
def score_batch(items: list[dict], model: str = FIT_MODEL) -> tuple[dict, dict]:
    """items: [{id,company,title,location,cycle,jd}]. Returns ({id: fit_result}, usage_totals).
    Chunks, calls the model, validates per item. JDs are already truncated by the caller."""
    out, usage_tot = {}, {"prompt_tokens": 0, "completion_tokens": 0}
    for i in range(0, len(items), CHUNK_SIZE):
        chunk = items[i:i + CHUNK_SIZE]
        valid_ids = {it["id"] for it in chunk}
        user = "Score these postings:\n" + json.dumps(chunk, ensure_ascii=False)
        parsed, usage = _call_model(model, user)
        usage_tot["prompt_tokens"] += usage.get("prompt_tokens", usage.get("input_tokens", 0))
        usage_tot["completion_tokens"] += usage.get("completion_tokens", usage.get("output_tokens", 0))
        if not parsed:
            continue
        for it in parsed.get("items", []):
            v = _validate_item(it, valid_ids)
            if v:
                out[it["id"]] = v
    return out, usage_tot


def _cost(model: str, usage: dict) -> float:
    pin, pout = PRICE.get(model, (1.0, 5.0))
    return usage.get("prompt_tokens", 0) / 1e6 * pin + usage.get("completion_tokens", 0) / 1e6 * pout


# ── public: run over the store with caching ────────────────────────────────────
@dataclass
class FitStats:
    scored: int = 0
    cached: int = 0
    no_jd: int = 0
    errors: int = 0
    cost: float = 0.0


def _jd_hash(jd: str) -> str:
    return hashlib.sha1(jd.encode("utf-8", "replace")).hexdigest()


def _needs_scoring(m: dict, model: str) -> bool:
    jd = (m.get("full_jd") or "").strip()
    if not jd:
        return False
    if not m.get("fit_score") and m.get("fit_score") != 0:
        return True
    return (m.get("fit_jd_hash") != _jd_hash(jd)
            or m.get("fit_model") != model
            or m.get("fit_prompt_ver") != PROMPT_VERSION)


def run_fit_pass(store, model: str = FIT_MODEL, max_llm: int = MAX_LLM_PER_RUN,
                 dry_run: bool = False) -> FitStats:
    """Score JD-bearing postings that are new/changed; cache results in the store.
    Never raises — a model failure just leaves those postings unscored (retried next run)."""
    st = FitStats()
    if not env("ANTHROPIC_API_KEY" if model.startswith("claude") else "OPENAI_API_KEY"):
        log("no API key — skipping fit pass (board renders without Fit)")
        return st

    todo = []
    for cid, rec in store.items():
        m = rec.get("machine", {})
        jd = (m.get("full_jd") or "").strip()
        if not jd:
            st.no_jd += 1
            continue
        if _needs_scoring(m, model):
            todo.append((cid, m))
        else:
            st.cached += 1
    # highest-hotness first, capped
    todo.sort(key=lambda t: -int(t[1].get("hotness", 0) or 0))
    if len(todo) > max_llm:
        log(f"{len(todo)} need scoring; capping at {max_llm} (rest next run)")
        todo = todo[:max_llm]
    if not todo:
        return st
    if dry_run:
        log(f"DRY: would score {len(todo)} postings on {model}")
        st.scored = len(todo)
        return st

    items = [{"id": cid, "company": m.get("company", ""), "title": m.get("role", ""),
              "location": m.get("location", ""), "cycle": m.get("cycle", ""),
              "jd": (m.get("full_jd") or "")[:JD_TRUNC_CHARS]} for cid, m in todo]
    results, usage = score_batch(items, model)
    today = datetime.now().strftime("%Y-%m-%d")
    for cid, m in todo:
        r = results.get(cid)
        if not r:
            st.errors += 1
            continue
        store.upsert_machine(cid, {
            **r, "fit_model": model, "fit_prompt_ver": PROMPT_VERSION,
            "fit_at": today, "fit_jd_hash": _jd_hash((m.get("full_jd") or "").strip()),
        })
        st.scored += 1
    st.cost = _cost(model, usage)
    log(f"{model}: scored {st.scored}, cached {st.cached}, no-JD {st.no_jd}, "
        f"errors {st.errors} · ~${st.cost:.4f} "
        f"({usage.get('prompt_tokens',0)}+{usage.get('completion_tokens',0)} tok)")
    return st


if __name__ == "__main__":
    # smoke: score the live store (uses cache; cheap on a warm store)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from curated_store import CuratedStore
    VAULT = Path(os.environ.get("HERMES_VAULT")
                 or ("/home/hermes/vault" if Path("/home/hermes/vault").exists()
                     else str(Path.home() / "Documents" / "School Vault - UofT")))
    store = CuratedStore(VAULT / "06 - Internships" / "Internship Pipeline" / "curated_postings.json").load()
    stats = run_fit_pass(store, dry_run="--dry-run" in sys.argv)
    print(stats)
