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
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# Applicant profile is loaded from a gitignored local file (not hardcoded here) so
# personal residency/citizenship details never live in this public repo.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from applicant_profile import load_profile  # noqa: E402

# ── config (swapping the model is a one-line edit) ────────────────────────────
FIT_MODEL = "claude-sonnet-4-6"       # Migrated off OpenRouter 2026-08-26 (one vendor).
#                                     RE-VALIDATED on that migration, 3 consecutive runs:
#                                     3/3 OVERALL PASS, 0 false-positive disqualifiers in all
#                                     three, recall 18/18, 18/18, 17/18 with hard-gate miss=none.
#                                     ~$0.125 per 26-case eval (~$0.005/role).
#                                     ❌ claude-haiku-4-5 FAILED the gate: perfect recall but a
#                                     false-positive disqualifier ('strong5' wrongly flagged
#                                     long-placement) — i.e. it silently drops good roles.
#                                     Prior: "openai/gpt-5.4-mini", EMPIRICALLY VALIDATED 2026-06-22 —
#                                     test_fit_pass.py 7/7 runs STABLE PASS, 14/14 recall, 0 false-pos.
#                                     The rich rubric made the cheap model consistent (raw mini was flaky).
#                                     ANY model change here MUST be re-validated against that same gate
#                                     before it ships — this scores the live job board.
#                                     Escalate -> "claude-sonnet-4-6" only if a future eval fails.
# JD truncation: eligibility/requirements live at the TOP, but application DEADLINES
# and legal text live at the BOTTOM. Pure-head truncation misses deadlines, so we keep
# the head AND the tail of long JDs (~900 tok total).
JD_HEAD = 2200
JD_TAIL = 1300
CHUNK_SIZE = 5                      # postings per LLM call (smaller = steadier recall, no dropped items)
MAX_LLM_PER_RUN = 120              # hard cost guard (first run is ~94)
PROMPT_VERSION = "fit-v3.4"        # bump to force re-score on prompt changes (part of cache key)
#                                    v3.1: head+tail JD truncation so deadlines (end of JD) are seen
#                                    v3.2: target cycles (Fall26/Win27/Spr27/Sum27) never wrong-cycle;
#                                          7-8mo Sep-Apr co-ops are long-placement (not wrong-cycle)
#                                    v3 (2026-06-22): externalized rubric (fit_rubric.md) — explicit
#                                    decision procedure + "does NOT apply" guards + worked examples

ENV_FILE = Path.home() / ".hermes" / ".env"
# Legacy escalation path. The OpenRouter key was REMOVED 2026-08-26, so a
# non-claude --model now fails fast with "no API key for <model>" rather than
# silently working. Kept only so the eval harness can still name a model.
OPENAI_URL = "https://openrouter.ai/api/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
VALID_DQ = {"none", "wrong-cycle", "phd-required", "citizenship",
            "clearance", "long-placement", "deadline-passed", "other"}
# Non-reasoning models that accept temperature=0. MUST list the OpenRouter-prefixed
# slug too: FIT_MODEL moved to "openai/gpt-5.4-mini" in the OpenRouter migration, and
# an unprefixed-only set silently made is_reasoning True — dropping `temperature: 0`
# and bumping max_tokens 1500 -> 8000. Determinism at temp 0 is the exact property
# the 2026-06-22 eval certified ("raw mini was flaky"; 7/7 stable, 14/14 recall), and
# every JD is scored through it. Keep both spellings in sync with FIT_MODEL.
_TEMPERATURE_OK = {"gpt-5.4-mini", "openai/gpt-5.4-mini"}
# rough per-MTok pricing (USD) for the cost log — confirm against the live dashboard.
# Both spellings, same reason as above: an unprefixed-only dict silently fell back to
# the (1.0, 5.0) default and mis-stated the cost line.
PRICE = {"gpt-5.4-mini": (0.25, 2.0), "gpt-5.5": (1.0, 8.0),
         "openai/gpt-5.4-mini": (0.25, 2.0), "openai/gpt-5.5": (1.0, 8.0),
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


def _truncate_jd(jd: str) -> str:
    """Keep the HEAD (eligibility/requirements) AND the TAIL (deadlines/legal) of a long
    JD. Pure-head truncation misses application deadlines, which sit at the bottom."""
    jd = jd or ""
    if len(jd) <= JD_HEAD + JD_TAIL:
        return jd
    return jd[:JD_HEAD] + "\n…[middle omitted]…\n" + jd[-JD_TAIL:]


_DATE = (r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+20\d\d"
         r"|\d{1,2}/\d{1,2}/20\d\d|20\d\d-\d{2}-\d{2}")
# Require a REAL application-deadline phrase before the date, not just any date near a loose
# keyword — otherwise start/posting/grad dates false-positive. (_DATE needs a DAY number, so
# bare "June 2026" start/grad dates can't match at all, which removes the dominant noise.)
_DL_CTX = (r"(?:application[s]?[^.\n]{0,40}?(?:accept|until|through|by|clos|deadline|due)"
           r"|accept(?:ed|ing|s)?[^.\n]{0,30}?(?:until|through|by)"
           r"|appl(?:y|ication[s]?)[^.\n]{0,5}?by"
           r"|deadline"
           r"|clos(?:e|es|ing)[^.\n]{0,15}?(?:date|on|:|until))")
_DEADLINE_RE = re.compile(_DL_CTX + r"[^.\n]{0,20}?(" + _DATE + r")", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _extract_deadline(jd: str) -> str:
    """Pull an application deadline/close date from the JD if stated (the model misses it
    in a long JD tail at batch scale). Returns the date string, or '' if none found."""
    m = _DEADLINE_RE.search(jd or "")
    return m.group(1).strip() if m else ""


def _parse_deadline(s: str):
    """Parse the date string _extract_deadline returns into a date, or None."""
    if not s:
        return None
    s = s.strip().rstrip(".")
    m = re.match(r"(20\d\d)-(\d{2})-(\d{2})$", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(20\d\d)$", s)
    if m:
        try:
            return date(int(m[3]), int(m[1]), int(m[2]))
        except ValueError:
            return None
    m = re.match(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(20\d\d)$", s)
    if m:
        mo = _MONTHS.get(m[1][:3].lower())
        if mo:
            try:
                return date(int(m[3]), mo, int(m[2]))
            except ValueError:
                return None
    return None


def _deadline_passed(jd: str) -> tuple[bool, str]:
    """Deterministic: (True, date-string) if the JD states an application deadline now in the
    PAST. The date comparison is done in CODE, never left to the model — the model mis-reads
    'accepted at least until <date>' as a floor, not a deadline, and lets dead roles through."""
    s = _extract_deadline(jd)
    d = _parse_deadline(s)
    return (bool(d and d < date.today()), s)


# ── system prompt: loaded from the externalized rubric (fit_rubric.md) ────────
# The rubric is the analyzer's full instruction set (decision procedure, disqualifier
# rules with "does NOT apply" guards, fit bands, worked examples). The personal profile
# is injected at the {{PROFILE}} token from the gitignored applicant_profile. Editing the
# rubric .md is how you tune the analyzer — bump PROMPT_VERSION after, then re-run the eval.
_RUBRIC_FILE = Path(__file__).resolve().parent / "fit_rubric.md"


def _build_sys_prompt() -> str:
    try:
        rubric = _RUBRIC_FILE.read_text(encoding="utf-8")
        if "{{PROFILE}}" in rubric:
            return rubric.replace("{{PROFILE}}", load_profile())
        return rubric
    except Exception as e:  # noqa: BLE001 — rubric missing/unreadable -> safe minimal fallback
        log(f"rubric file unreadable ({e}); using built-in fallback")
        return ("You screen internship job descriptions for " + load_profile() + " For each "
                "posting (with its jd) return JSON {\"items\":[{\"id\":str,\"fit_score\":int 0-100,"
                "\"fit_why\":str,\"disqualifier\":\"none|wrong-cycle|phd-required|citizenship|"
                "clearance|long-placement|deadline-passed|other\",\"jd_summary\":str}]}. Flag ONLY "
                "clearly-stated disqualifiers; the applicant is US+Canada work-authorized (green "
                "card, no sponsorship) so work-authorization requirements are NOT disqualifiers. "
                "When unsure, use none. Include every id.")


SYS_PROMPT = _build_sys_prompt()


# ── model call (OpenAI + Claude branches, selected by model string) ───────────
def _call_model(model: str, user_content: str, max_tokens: int = 1500):
    """Return parsed JSON dict from the model, or None on failure (3 retries)."""
    is_claude = model.startswith("claude")
    # reasoning OpenAI models (gpt-5.5) spend tokens on hidden reasoning BEFORE the
    # JSON output — give them headroom or the JSON truncates and items go missing.
    is_reasoning = (not is_claude) and (model not in _TEMPERATURE_OK)
    if is_reasoning:
        max_tokens = max(max_tokens, 8000)
    key = env("ANTHROPIC_API_KEY" if is_claude else "OPENROUTER_API_KEY")
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
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": SYS_PROMPT},
                         {"role": "user", "content": user_content}],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": max_tokens,
        }
        # only non-reasoning models accept an explicit temperature; reasoning models
        # (gpt-5.5) reject it. They're inherently steadier, so we just omit it there.
        if model in _TEMPERATURE_OK:
            payload["temperature"] = 0
        body = json.dumps(payload).encode()
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
        # explicitly extract any application deadline (it's buried in the JD tail and the
        # model misses it at batch scale) and hand it to the model as a field to compare.
        enriched = [{**it, "application_deadline": _extract_deadline(it.get("jd", ""))}
                    for it in chunk]
        user = (f"TODAY'S DATE is {date.today().isoformat()} (use it to judge deadline-passed; "
                "an 'application_deadline' before today => deadline-passed).\n"
                "Score these postings:\n" + json.dumps(enriched, ensure_ascii=False))
        parsed, usage = _call_model(model, user)
        usage_tot["prompt_tokens"] += usage.get("prompt_tokens", usage.get("input_tokens", 0))
        usage_tot["completion_tokens"] += usage.get("completion_tokens", usage.get("output_tokens", 0))
        if not parsed:
            continue
        jd_by_id = {it["id"]: it.get("jd", "") for it in chunk}
        for it in parsed.get("items", []):
            v = _validate_item(it, valid_ids)
            if v:
                # deterministic override: code, not the model, decides deadline-passed.
                passed, ddl = _deadline_passed(jd_by_id.get(it["id"], ""))
                if passed and v["fit_disqualifier"] != "deadline-passed":
                    v["fit_disqualifier"] = "deadline-passed"
                    v["fit_score"] = min(v["fit_score"], 10)
                    v["fit_why"] = f"Application window closed ({ddl})."
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


def _deadline_sweep(store) -> int:
    """Deterministic deadline pass over EVERY posting (not just LLM-scored ones). Pure date
    math, no API cost, so a role whose application window closes WHILE its fit is cached still
    gets sunk. Authoritative over the model (which mis-reads 'accepted at least until <date>').
    Returns how many roles it newly sank."""
    flagged = 0
    for cid, rec in store.items():
        m = rec.get("machine", {})
        passed, ddl = _deadline_passed((m.get("full_jd") or "").strip())
        if passed and m.get("fit_disqualifier") != "deadline-passed":
            store.upsert_machine(cid, {
                "fit_disqualifier": "deadline-passed",
                "fit_score": min(int(m.get("fit_score") or 100), 10),
                "fit_why": f"Application window closed ({ddl}).",
            })
            flagged += 1
    return flagged


def run_fit_pass(store, model: str = FIT_MODEL, max_llm: int = MAX_LLM_PER_RUN,
                 dry_run: bool = False) -> FitStats:
    """Score JD-bearing postings that are new/changed; cache results in the store.
    Never raises — a model failure just leaves those postings unscored (retried next run)."""
    st = FitStats()
    if not env("ANTHROPIC_API_KEY" if model.startswith("claude") else "OPENROUTER_API_KEY"):
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
    if dry_run:
        if todo:
            log(f"DRY: would score {len(todo)} postings on {model}")
        st.scored = len(todo)
        return st

    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    if todo:
        items = [{"id": cid, "company": m.get("company", ""), "title": m.get("role", ""),
                  "location": m.get("location", ""), "cycle": m.get("cycle", ""),
                  "jd": _truncate_jd(m.get("full_jd") or "")} for cid, m in todo]
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

    # deterministic deadline sweep — ALWAYS (covers cached-only refreshes too)
    sunk = _deadline_sweep(store)
    if sunk:
        log(f"deadline sweep: sank {sunk} past-deadline role(s)")

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
    store = CuratedStore(VAULT / "06 - Internships" / "Job Search" / "curated_postings.json").load()
    stats = run_fit_pass(store, dry_run="--dry-run" in sys.argv)
    print(stats)
