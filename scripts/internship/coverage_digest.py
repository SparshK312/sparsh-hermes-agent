#!/usr/bin/env python3
"""
coverage_digest.py — the weekly "what is the board NOT showing me?" report.

WHY (2026-09-12). The board decides what to show by a name lookup: a company not in the
tier table is tier C by default, and tier C is deleted before any other check (SWElist
rows outright; Simplify rows by the enrichment cap). Over 16 SWElist digests, 79% of
the in-lane postings were at companies the table did not name — American Express,
Tradeweb, Epic Games, Akuna, Domino, Tanium, Formlabs among them — and every one had
zero rows on any tab. The table only ever grew after someone noticed a miss.

This script makes the miss visible on a schedule instead. Once a week it reads the same
feeds the wide net reads (six aggregator READMEs + the SWElist and InternInsider
newsletters over IMAP), keeps the in-lane intern postings from the last N days, and
reports two things:

  A. "Not on the target list" — in-lane postings at tier-C companies with no row in the
     store, grouped by company. He glances, promotes what he wants (hotness.py TIER_B,
     or a board in company_boards.py), done. Nothing is silently dropped.
  B. "Coverage gaps at target companies" — postings at tier-S/A/B companies that the
     store does not hold, by id OR by (company, title, location). This is the Lyft×16
     detector: a target company whose own board is not wired, or a fetcher that broke.

Output: a Telegram message (bounded) + a Markdown report written into the vault at
`06 - Internships/Job Search/Coverage Digest.md` (overwritten weekly, so Obsidian holds
the full list the message truncates). `--dry-run` prints and writes nothing.

Runs on the VPS (needs the live store): hermes cron `coverage-digest`, Saturdays 09:00,
via run_coverage_digest.sh.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gmail_source                                                       # noqa: E402
import store_paths                                                        # noqa: E402
import wide_net_source                                                    # noqa: E402
from curated_store import CuratedStore                                    # noqa: E402
from hotness import brand_tier, normalize_company_name, role_lane          # noqa: E402
from internship_scraper import canonical_id, classify_location, classify_period  # noqa: E402

TARGET_LANES = ("SWE", "AI/ML", "PM")
# Standing rule (Sparsh, 2026-09-08): firmware / embedded roles are Not a Fit and should
# not resurface. role_lane() files them under SWE, and the digest has no fit pass to
# apply the firmware-embedded disqualifier, so the title is filtered here.
_FIRMWARE_RE = re.compile(r"\bfirmware\b|\bembedded\b", re.I)
# Bare city abbreviations the aggregators use that classify_location() does not know.
_NA_ABBREV = {"sf", "nyc", "la", "bay area", "sf bay area"}
DEFAULT_DAYS = 7
TELEGRAM_CHAT_ID = "696500863"
TELEGRAM_MAX = 3600            # Telegram's hard limit is 4096; leave room for the footer
TOP_COMPANIES = 25             # in the message; the vault report is complete
REPORT_REL = Path("06 - Internships") / "Job Search" / "Coverage Digest.md"


# ── pure helpers (unit-tested in test_board_invariants.py) ───────────────────
def _triple(company: str, title: str, location: str) -> tuple:
    return (normalize_company_name(company or ""),
            re.sub(r"\s+", " ", (title or "").lower()).strip(),
            re.sub(r"\s+", " ", (location or "").lower()).strip())


def store_index(store_postings: dict) -> dict:
    """Everything the digest needs to know about the store, in one pass."""
    ids, triples, pairs, companies = set(), set(), set(), set()
    for cid, e in store_postings.items():
        m = (e or {}).get("machine") or {}
        if not m.get("company"):
            continue                       # identity-less orphan (see curate.py step 1)
        ids.add(cid)
        t = _triple(m.get("company"), m.get("role"), m.get("location"))
        triples.add(t)
        pairs.add(t[:2])
        companies.add(normalize_company_name(m.get("company")))
    return {"ids": ids, "triples": triples, "pairs": pairs, "companies": companies}


def in_window(p, days: int) -> bool:
    age = getattr(p, "age_days", None)
    return age is not None and 0 <= age <= days


def is_in_lane(p) -> bool:
    if role_lane(p.title) not in TARGET_LANES:
        return False
    if _FIRMWARE_RE.search(p.title or ""):
        return False
    return classify_period(p.title, p.terms, p.source)[0] != "reject"


def partition(postings: list, idx: dict, days: int = DEFAULT_DAYS) -> tuple[dict, list]:
    """Pure. Returns (nontarget_by_company, target_gaps).

    nontarget_by_company: {display company: [postings]} for tier-C companies with NO
    store row at all. target_gaps: postings at tier-S/A/B companies the store does not
    hold by id or by (company, title, location)."""
    nontarget: dict[str, list] = collections.defaultdict(list)
    gaps: list = []
    seen_keys: set = set()
    seen_pairs: set = set()
    # Aggregator rows come before newsletter rows (gather() builds the list that way), so a
    # location-less SWElist row meets its located twin's (company, title) already seen.
    for p in postings:
        if not in_window(p, days) or not is_in_lane(p):
            continue
        key = _triple(p.company, p.title, p.location)
        if key in seen_keys or (not key[2] and key[:2] in seen_pairs):
            continue
        seen_keys.add(key)
        seen_pairs.add(key[:2])
        tier = brand_tier(p.company)
        nc = normalize_company_name(p.company)
        if tier == "C":
            if nc not in idx["companies"]:
                nontarget[p.company.strip()].append(p)
        else:
            cid = p.canonical_id or canonical_id(p.url)
            held = cid in idx["ids"] or key in idx["triples"] or (not key[2] and key[:2] in idx["pairs"])
            if not held:
                gaps.append(p)
    return dict(nontarget), gaps


def _loc_tag(loc: str) -> str:
    if not loc:
        return "?"
    if loc.strip().lower() in _NA_ABBREV:
        return loc
    verdict = classify_location(loc)[0]
    return loc if verdict != "reject" else f"{loc} ⚠️non-NA"


def format_message(nontarget: dict, gaps: list, days: int, generated: str) -> str:
    ranked = sorted(nontarget.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))
    total_rows = sum(len(v) for v in nontarget.values())
    lines = [f"📋 COVERAGE DIGEST · last {days} days · {generated}",
             f"{total_rows} in-lane intern reqs at {len(ranked)} companies NOT on the target list "
             f"(tier C, zero board rows). Promote the ones you want; the rest stay invisible."]
    if gaps:
        lines.append(f"\n🔴 {len(gaps)} at TARGET companies the store does not hold "
                     f"(board not wired, or a fetcher broke):")
        by_co = collections.Counter(p.company.strip() for p in gaps)
        for co, n in by_co.most_common(10):
            ex = next(p for p in gaps if p.company.strip() == co)
            lines.append(f"• {co} ×{n} — {ex.title[:55]} @ {_loc_tag(ex.location)[:30]}")
    lines.append("\n🆕 NOT ON THE LIST:")
    for co, ps in ranked[:TOP_COMPANIES]:
        titles = []
        for p in ps:
            t = p.title[:48]
            if t not in titles:
                titles.append(t)
        locs = []
        for p in ps:
            l = _loc_tag(p.location)[:26]
            if l not in locs:
                locs.append(l)
        lines.append(f"• {co} ×{len(ps)} · {' / '.join(locs[:2])}")
        lines.append(f"  {' · '.join(titles[:2])}")
        lines.append(f"  {ps[0].url}")
    if len(ranked) > TOP_COMPANIES:
        lines.append(f"\n…and {len(ranked) - TOP_COMPANIES} more companies in the vault report "
                     f"(06 - Internships/Job Search/Coverage Digest.md).")
    text = "\n".join(lines)
    if len(text) > TELEGRAM_MAX:
        cut = text[:TELEGRAM_MAX].rsplit("\n", 1)[0]
        text = cut + "\n\n…truncated; the vault report is complete."
    return text


def format_report(nontarget: dict, gaps: list, days: int, generated: str) -> str:
    ranked = sorted(nontarget.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))
    total_rows = sum(len(v) for v in nontarget.values())
    out = [
        "---",
        "type: coverage-digest",
        "category: career",
        "status: active",
        f"last_updated: {generated[:10]}",
        f"window_days: {days}",
        f"nontarget_companies: {len(ranked)}",
        f"nontarget_rows: {total_rows}",
        f"target_gaps: {len(gaps)}",
        "tags:",
        "  - career",
        "  - internships",
        "  - curation",
        "---",
        "",
        "# Coverage Digest",
        "",
        f"> Generated {generated} by `coverage_digest.py` (hermes cron `coverage-digest`, "
        f"Saturdays 09:00). Rewritten weekly; the previous week is in git/Log history only.",
        ">",
        "> **What this is.** The board shows only companies its tier table names; everything "
        "else is tier C and deleted before any other check. This report lists what that rule "
        "hid this week so a real employer becomes a line to promote instead of a silent hole. "
        "To promote: add the name to `hotness.py` TIER_B (or a board to `company_boards.py`), "
        "run the invariant suite, deploy. See [[Curated Board System]] § Coverage digest.",
        "",
        "## 🔴 Coverage gaps at target companies",
        "",
        "Postings at tier-S/A/B companies that the store does not hold by id or by "
        "(company, title, location). Expected to be empty; anything here means a board is "
        "not wired or a fetcher is broken.",
        "",
    ]
    if gaps:
        out.append("| Company | Tier | Title | Location | Source | Link |")
        out.append("|---|---|---|---|---|---|")
        for p in sorted(gaps, key=lambda p: (brand_tier(p.company), p.company.lower())):
            out.append(f"| {p.company} | {brand_tier(p.company)} | {p.title} | "
                       f"{_loc_tag(p.location)} | {p.source} | [open]({p.url}) |")
    else:
        out.append("_None this week._")
    out += ["", f"## 🆕 Not on the target list — {len(ranked)} companies, {total_rows} in-lane reqs", "",
            "Tier-C companies with zero board rows. Sorted by number of in-lane reqs this week.", ""]
    for co, ps in ranked:
        out.append(f"### {co} ×{len(ps)}")
        out.append("")
        for p in ps:
            out.append(f"- {p.title} — {_loc_tag(p.location)} · {p.source} · "
                       f"{p.posted_date or 'date ?'} · [open]({p.url})")
        out.append("")
    return "\n".join(out)


# ── IO ────────────────────────────────────────────────────────────────────────
def _hermes_env(key: str) -> str:
    return wide_net_source._load_env().get(key, "")


def send_telegram(text: str) -> bool:
    token = _hermes_env("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[coverage] no TELEGRAM_BOT_TOKEN in ~/.hermes/.env — not sent", file=sys.stderr)
        return False
    body = json.dumps({"chat_id": _hermes_env("TELEGRAM_CHAT_ID") or TELEGRAM_CHAT_ID,
                       "text": text, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ok = json.loads(r.read().decode()).get("ok", False)
    except Exception as e:  # noqa: BLE001
        print(f"[coverage] telegram send failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False
    if not ok:
        print("[coverage] telegram answered ok=false", file=sys.stderr)
    return bool(ok)


def gather(days: int) -> list:
    github = wide_net_source._fetch_github()
    env = wide_net_source._load_env()
    gmail, fails = gmail_source.fetch_email_postings(env.get, since_days=days + 1)
    for src, err in fails:
        print(f"[coverage] {src}: {err}", file=sys.stderr)
    print(f"[coverage] {len(github)} aggregator rows + {len(gmail)} newsletter rows "
          f"(gmail {'configured' if env.get('EMAIL_APP_PASSWORD') else 'NOT configured'})",
          file=sys.stderr)
    return github + gmail


def main() -> int:
    ap = argparse.ArgumentParser(description="Weekly coverage digest for the curated board.")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--dry-run", action="store_true", help="print; no Telegram, no vault write")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--no-vault", action="store_true")
    args = ap.parse_args()

    sp = store_paths.store_path()
    store = CuratedStore(sp).load()
    if len(store) == 0:
        print(f"[coverage] 🛑 store at {sp} is EMPTY — refusing to report every company as "
              f"missing. Is this the right machine / CURATED_STORE?", file=sys.stderr)
        return 2
    idx = store_index(store.postings)
    postings = gather(args.days)
    nontarget, gaps = partition(postings, idx, args.days)
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    msg = format_message(nontarget, gaps, args.days, generated)
    report = format_report(nontarget, gaps, args.days, generated)
    print(f"[coverage] store rows {len(store)} · feed rows {len(postings)} · "
          f"non-target companies {len(nontarget)} ({sum(len(v) for v in nontarget.values())} reqs) · "
          f"target gaps {len(gaps)}", file=sys.stderr)
    if args.dry_run:
        print(msg)
        print("\n--- report head ---\n" + "\n".join(report.splitlines()[:40]))
        return 0
    if not args.no_vault:
        dest = store_paths.vault_root() / REPORT_REL
        if dest.parent.is_dir():
            dest.write_text(report, encoding="utf-8")
            print(f"[coverage] wrote {dest}", file=sys.stderr)
        else:
            print(f"[coverage] vault dir missing, report not written: {dest.parent}", file=sys.stderr)
    if not args.no_telegram:
        send_telegram(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
