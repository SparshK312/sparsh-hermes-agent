#!/usr/bin/env python3
"""
internship_triage.py — frontier triage for the internship watcher (the "proper" fix).

Replaces the OLD agent-loop half of the internship-watcher cron. That half ran the
small model as a 6-turn agent that climbed to ~55K context, reached for blocked
tools (execute_code) every morning, and risked the codex broken-pipe. This does the
same job as ONE focused GPT-5.5 call — smart, cheap, no thrash:

    scrape (reuse internship_scraper) -> NEW postings
      -> deterministic rule verdict per posting (the fallback)
      -> ONE GPT-5.5 structured call refines verdicts + drafts cover lines + a digest
      -> append to postings_seen.json (final verdict)
      -> rebuild the Pipeline 'Open Opportunities' table (deterministic, reused)
      -> send a Telegram digest IF anything is actionable
      -> print the wake-gate so Hermes runs no agent

Runs on system python (stdlib + bs4, both present). Frontier model = GPT-5.5, the
same one the coach uses. Stdout is ONLY the wake-gate line (everything else is
Telegram / stderr) so Hermes' cron never wakes an agent.

  internship_triage.py [--dry-run] [--no-llm] [--max-llm N]
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import internship_scraper as S          # noqa: E402 — reuse the proven parsers + writers
from internship_sources import SOURCES  # noqa: E402

ENV_FILE = Path.home() / ".hermes" / ".env"
CHAT_ID = "696500863"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-5.5"
WAKE_GATE = '{"wakeAgent": false}'
VALID_VERDICTS = {"apply now", "wait", "consider", "skip"}

DRY = "--dry-run" in sys.argv
NO_LLM = "--no-llm" in sys.argv
MAX_LLM = 40
if "--max-llm" in sys.argv:
    try:
        MAX_LLM = int(sys.argv[sys.argv.index("--max-llm") + 1])
    except (ValueError, IndexError):
        pass


# ----------------------------------------------------------------- env / io
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
    print(f"[internship-triage] {msg}", file=sys.stderr)


# ----------------------------------------------------------------- scrape (reuse the scraper)
def collect_new() -> tuple[list, list]:
    """Fetch every source, parse, dedup against postings_seen.json + within the
    batch. Returns (new_postings, failures). Mirrors internship_scraper.main()'s
    collection, reusing its parsers/fetchers so there's one source of truth."""
    seen = S.load_seen_ids()
    allp, fails = [], []
    for src in SOURCES:
        try:
            raw = S.fetch_source(src["url"])
        except Exception as e:  # noqa: BLE001
            fails.append((src["name"], f"fetch: {type(e).__name__}: {e}"))
            continue
        try:
            if src["format"] == "html_table":
                allp.extend(S.parse_html_table(raw, src["name"]))
            elif src["format"] == "markdown_table":
                allp.extend(S.parse_markdown_table(raw, src["name"]))
            else:
                fails.append((src["name"], f"unknown format: {src['format']}"))
        except Exception as e:  # noqa: BLE001
            fails.append((src["name"], f"parse: {type(e).__name__}: {e}"))
    new, batch = [], set()
    for p in allp:
        if p.canonical_id in seen or p.canonical_id in batch:
            continue
        batch.add(p.canonical_id)
        new.append(p)
    return new, fails


def rule_entry(p) -> dict:
    """A postings_seen.json entry built from the deterministic rule triage — the
    fallback if the frontier call is unavailable. Field names match what
    build_open_opportunities_section() renders."""
    t = S.triage_posting(p)
    return {
        "id": p.canonical_id, "first_seen": datetime.now().strftime("%Y-%m-%d"),
        "verdict": t["verdict"], "reason": t.get("reason", ""),
        "company": p.company, "role": p.title, "location": p.location, "url": p.url,
        "source": p.source, "posted_date": p.posted_date, "age_days": p.age_days,
        "terms": p.terms,
    }


# ----------------------------------------------------------------- frontier triage (one call)
SYS_PROMPT = (
    "You are Sparsh's internship-pipeline triage analyst. He's a UofT ECE student doing "
    "4 back-to-back PEY rotations, targeting SWE / AI-ML / Data INTERN roles for Fall 2026, "
    "Winter/Spring 2027, or Summer 2027, in the US or Canada. He is a US permanent resident "
    "(US work-authorized, no sponsorship) and Canadian-status; SKIP roles requiring US "
    "citizenship/clearance (US Person, Top Secret, ITAR). 4-month roles preferred; SKIP "
    "12/16-month placements (don't fit the 4-rotation plan).\n\n"
    "Watchlist (highest priority): Meta, Apple, Netflix, Microsoft, Nvidia, Anthropic, OpenAI, "
    "xAI, Cohere, Mistral, Google DeepMind, Stripe, Plaid, Mercury, Ramp, Brex, Robinhood, "
    "Vercel, Linear, Notion, Figma, Scale AI, Databricks, Perplexity, Cursor (Anysphere), "
    "Replit, Amazon, Mercor, Google, Shopify.\n\n"
    "Verdict per posting:\n"
    "- 'apply now' = on watchlist, all filters pass, posted <=7 days ago.\n"
    "- 'wait' = on watchlist + applicable but posted >7 days ago (likely past early-bird).\n"
    "- 'consider' = applicable (right role/period/location) but NOT on the watchlist.\n"
    "- 'skip' = fails a filter (wrong period/location/role, citizenship-gated, 12+ month).\n"
    "Each posting comes with a deterministic rule verdict; correct it when the title/terms make "
    "the real period or role clearer. For every 'apply now', write a ONE-sentence cover opener "
    "using his closest hook: AI labs -> Claude Ambassador; commerce/payments/dev-tools -> Shopify "
    "PEY; early-stage/founder-y -> Call Fusion->Perfecti acqui-hire; else -> Shopify PEY.\n\n"
    "Return a JSON object EXACTLY:\n"
    "{\n"
    '  "items": [{"id": str, "verdict": str, "reason": str (<=12 words), "cover_line": str|null}],\n'
    '  "digest": str  // a terse Telegram summary: counts + the apply-now companies/roles, plain markdown, no preamble\n'
    "}\n"
    "Include EVERY posting id in items. Use ONLY the postings given."
)


def frontier_triage(new_postings: list) -> tuple[dict, str | None]:
    """One GPT-5.5 call. Returns ({id: {verdict,reason,cover_line}}, digest|None).
    Empty dict + None on any failure (caller falls back to rule verdicts)."""
    key = env("OPENAI_API_KEY")
    if not key or NO_LLM:
        return {}, None
    compact = [{
        "id": p.canonical_id, "company": p.company, "title": p.title,
        "location": p.location, "terms": p.terms, "posted_date": p.posted_date,
        "age_days": p.age_days, "rule_verdict": S.triage_posting(p)["verdict"],
    } for p in new_postings[:MAX_LLM]]
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": SYS_PROMPT},
                     {"role": "user", "content": "Triage these new postings:\n"
                      + json.dumps(compact, ensure_ascii=False)}],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 4000,
    }).encode("utf-8")
    for _ in range(3):
        try:
            req = urllib.request.Request(OPENAI_URL, data=body, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                parsed = json.loads(json.loads(r.read())["choices"][0]["message"]["content"])
            by_id = {}
            for it in parsed.get("items", []):
                v = (it.get("verdict") or "").strip().lower()
                if it.get("id") and v in VALID_VERDICTS:
                    by_id[it["id"]] = {"verdict": v, "reason": it.get("reason", ""),
                                       "cover_line": it.get("cover_line")}
            return by_id, parsed.get("digest")
        except Exception as e:  # noqa: BLE001
            log(f"frontier call failed: {e}")
    return {}, None


# ----------------------------------------------------------------- pipeline + send
def all_seen_entries() -> list:
    try:
        return json.loads(Path(S.POSTINGS_SEEN_PATH).read_text(encoding="utf-8")).get("seen", [])
    except Exception:  # noqa: BLE001
        return []


def send_message(text: str) -> bool:
    import urllib.parse
    token = env("TELEGRAM_BOT_TOKEN")
    if not token:
        log("no TELEGRAM_BOT_TOKEN")
        return False
    for pm in ("Markdown", None):
        payload = {"chat_id": CHAT_ID, "text": text}
        if pm:
            payload["parse_mode"] = pm
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=urllib.parse.urlencode(payload).encode())
            with urllib.request.urlopen(req, timeout=20) as r:
                if json.loads(r.read()).get("ok"):
                    return True
        except Exception as e:  # noqa: BLE001
            log(f"send ({pm}) failed: {e}")
    return False


def templated_digest(entries: list) -> str:
    """Offline fallback digest from the final entries (used if the LLM digest is absent)."""
    apply_now = [e for e in entries if e["verdict"] == "apply now"]
    wait = [e for e in entries if e["verdict"] == "wait"]
    consider = [e for e in entries if e["verdict"] == "consider"]
    L = [f"💼 *Internship watch* — {len(apply_now)} apply / {len(wait)} wait / {len(consider)} consider"]
    for e in apply_now[:8]:
        L.append(f"🟢 *{e['company']}* — {e['role']}  ({e.get('location','')})")
        if e.get("cover_line"):
            L.append(f"   _{e['cover_line']}_")
    for e in wait[:4]:
        L.append(f"⏳ {e['company']} — {e['role']}")
    L.append("\nFull queue → Internship Pipeline.md")
    return "\n".join(L)


# ----------------------------------------------------------------- main
def main() -> int:
    new, fails = collect_new()
    for name, err in fails:
        log(f"source-failure {name}: {err}")

    if not new:
        log(f"no new postings ({len(fails)} source failures)")
        print(WAKE_GATE)
        return 0

    # rule verdict per posting (the fallback) → refine with one frontier call
    entries = {p.canonical_id: rule_entry(p) for p in new}
    refined, llm_digest = frontier_triage(new)
    for cid, r in refined.items():
        if cid in entries:
            entries[cid]["verdict"] = r["verdict"]
            entries[cid]["reason"] = r["reason"] or entries[cid]["reason"]
            if r.get("cover_line"):
                entries[cid]["cover_line"] = r["cover_line"]
    final = list(entries.values())
    actionable = [e for e in final if e["verdict"] in ("apply now", "wait", "consider")]
    log(f"{len(new)} new · {sum(e['verdict']=='apply now' for e in final)} apply / "
        f"{sum(e['verdict']=='wait' for e in final)} wait / "
        f"{sum(e['verdict']=='consider' for e in final)} consider / "
        f"{sum(e['verdict']=='skip' for e in final)} skip · llm={'yes' if refined else 'no'}")

    if DRY:
        digest = llm_digest or templated_digest(actionable)
        print("----- would write seen + pipeline; would send digest: -----", file=sys.stderr)
        print(digest, file=sys.stderr)
        print(WAKE_GATE)
        return 0

    # persist: append to seen.json, then rebuild the Pipeline table from ALL seen
    S.append_seen_entries(final)
    try:
        section = S.build_open_opportunities_section(all_seen_entries())
        S.write_open_opportunities_to_pipeline(section)
    except Exception as e:  # noqa: BLE001
        log(f"pipeline write failed: {e}")

    # ping only if something is actionable (never nag on an all-skip morning)
    if actionable:
        send_message(llm_digest or templated_digest(actionable))

    print(WAKE_GATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
