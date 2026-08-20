#!/usr/bin/env python3
"""
curate.py — the on-demand "refresh" for the living brand-first board.

Run it whenever you want a fresh board:

    .venv/bin/python curate.py                 # refresh the board
    .venv/bin/python curate.py --validate-boards   # ping every board, report counts

Flow (round-trip preserve — your manual edits survive):
  1. abort if the xlsx is open in Excel (~$ lock)
  2. load curated_postings.json (the store)
  3. read your manual edits back out of the existing board (keyed by hidden _id)
     and merge them into the store
  4. one-time: seed My Applications from the old Application Tracker history
  5. pull every target company's board (lane-1 brand-first), full JDs via the
     ATS JSON APIs; score each by hotness; upsert into the store
  6. re-score every stored posting (recency decays daily); brand-board postings
     that fell off their board for 2 runs -> stale (drop from queue, keep in JSON)
  7. atomically write the store + regenerate Curated Board.xlsx

Runs locally (no 120s wall). The existing Application Tracker.xlsx is never touched.
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path


def _wait_for_network(hosts=("boards-api.greenhouse.io", "openrouter.ai", "api.telegram.org"),
                      tries=18, delay=5) -> bool:
    """Scheduled runs fire as the Mac wakes, and the network settles UNEVENLY — the job
    boards resolve before OpenAI/Telegram do. Wait (up to ~90s) until ALL critical hosts
    resolve, so a wake-run doesn't harvest fine but then DNS-fail the fit pass + Telegram
    notify (observed 2026-06-24: 94 unscored + no Telegram on a 9:36 wake-run). If a host
    never comes up, proceed anyway — harvest may still work, fit/notify degrade gracefully,
    and the harvest-collapse guard protects the board."""
    for i in range(tries):
        try:
            for h in hosts:
                socket.gethostbyname(h)
            return True
        except OSError:
            if i == 0:
                print("[refresh] network still settling — waiting for full connectivity…",
                      file=sys.stderr)
            time.sleep(delay)
    print("[refresh] some hosts still unresolved after wait — proceeding (fit/notify may degrade)",
          file=sys.stderr)
    return False

# Telegram (reuse the Hermes bot — same chat the other crons send to)
TELEGRAM_CHAT_ID = "696500863"
HERMES_ENV = Path.home() / ".hermes" / ".env"


def _hermes_env(key: str) -> str | None:
    if HERMES_ENV.exists():
        for line in HERMES_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    import os
    return os.environ.get(key)


def _send_telegram(text: str) -> bool:
    token = _hermes_env("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[notify] no TELEGRAM_BOT_TOKEN in ~/.hermes/.env — skipped", file=sys.stderr)
        return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID, "text": text,
            "parse_mode": "Markdown", "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:  # noqa: BLE001
        print(f"[notify] telegram send failed: {e}", file=sys.stderr)
        return False


def _new_postings_digest(store, new_cids: set) -> str | None:
    rows = []
    for cid in new_cids:
        m = (store.get(cid) or {}).get("machine", {})
        if m.get("dead") or not m.get("company"):
            continue
        rows.append(m)
    if not rows:
        return None
    # clean (applyable) vs disqualified — surface both, separated
    def _disq(m):
        return (m.get("fit_disqualifier") or "none") not in ("none", "", None)
    clean = sorted([m for m in rows if not _disq(m)], key=lambda m: -int(m.get("hotness", 0) or 0))
    flagged = [m for m in rows if _disq(m)]
    n = len(rows)

    def _fmt(m):
        fit = m.get("fit_score")
        fitstr = f" · fit {fit}" if isinstance(fit, (int, float)) else ""
        return (f"• [{m.get('tier', '?')}] *{m.get('company', '?')}* — "
                f"{str(m.get('role', '?'))[:46]}{fitstr} {m.get('fresh', '')}".rstrip())

    # CYCLE-OPEN WATCHER: a new tier-S/A role means a top brand just opened (or
    # added) an intern req. These are rare + time-sensitive (top spots fill fast),
    # so lead with them so they never get buried under B-tier volume.
    elite = [m for m in clean if str(m.get("tier", "")).upper() in ("S", "A")]
    rest = [m for m in clean if str(m.get("tier", "")).upper() not in ("S", "A")]

    lines = [f"🔥 *{n} new internship role{'s' if n != 1 else ''}* on your Curated Board:"]
    if elite:
        lines.append(f"\n🚨 *{len(elite)} TOP-BRAND role{'s' if len(elite) != 1 else ''} "
                     f"just opened* — apply early, these fill fast:")
        lines += [_fmt(m) for m in elite[:8]]
        if len(elite) > 8:
            lines.append(f"…and {len(elite) - 8} more top-brand.")
    if rest:
        if elite:
            lines.append("\n*Other new roles:*")
        lines += [_fmt(m) for m in rest[:8]]
        if len(rest) > 8:
            lines.append(f"…and {len(rest) - 8} more.")
    if flagged:
        lines.append(f"\n⚠️ {len(flagged)} flagged (sunk, AI found a disqualifier):")
        for m in flagged[:4]:
            lines.append(f"  • {m.get('company', '?')} — {str(m.get('role', '?'))[:34]}: "
                         f"_{m.get('fit_disqualifier')}_")
    lines.append("\nOpen *Curated Board.xlsx* → 🔥 Curated Queue to apply.")
    return "\n".join(lines)

# vault paths (Mac-local; do NOT import the VPS POSTINGS_SEEN_PATH).
# Override via env for testing (CURATED_XLSX / CURATED_STORE).
import os  # noqa: E402
VAULT = Path(os.environ.get("HERMES_VAULT")
             or ("/home/hermes/vault" if Path("/home/hermes/vault").exists()
                 else str(Path.home() / "Documents" / "School Vault - UofT")))
STORE_PATH = Path(os.environ.get("CURATED_STORE",
                  VAULT / "06 - Internships" / "Job Search" / "curated_postings.json"))
XLSX_PATH = Path(os.environ.get("CURATED_XLSX",
                 VAULT / "06 - Internships" / "Job Search" / "Curated Board.xlsx"))
OLD_TRACKER = VAULT / "06 - Internships" / "Job Search" / "Legacy" / "Application Tracker.xlsx"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(VAULT / "Scripts"))

import brand_first_source  # noqa: E402
import wide_net_source  # noqa: E402
from company_boards import boards as _boards  # noqa: E402
from build_curated_xlsx import is_locked, read_back_human, write_board  # noqa: E402
from curated_store import CuratedStore  # noqa: E402
from hotness import hotness, role_lane  # noqa: E402
from internship_scraper import canonical_id, normalize_company_name  # noqa: E402

STALE_STRIKES = 2
# Wide-net sources are ROLLING WINDOWS — falling off a GitHub README is not
# evidence a req closed (measured 2026-08-18: 54% of dead wide-net rows were
# still open on the employer's own ATS). Needs many more strikes than a direct
# API dropout before we believe it.
WIDE_STALE_STRIKES = 14          # ~1 week at 2 runs/day
# A healthy lane-1 harvest is ~68 roles (median over 108 logged runs, min 25).
# Below this, treat lane 1 as collapsed and exempt its postings from striking.
LANE1_MIN_HEALTHY = 25
_ACTIONED = {"applied", "oa", "phone screen", "onsite", "offer", "rejected",
             "networking", "on hold"}

import re as _re  # noqa: E402
_EMOJI = _re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⬀-⯿]")


def _clean_company(name: str) -> str:
    """Strip aggregator emoji/symbols (e.g. '🔥Tesla' -> 'Tesla') and tidy."""
    if not name:
        return name
    s = _EMOJI.sub("", name)
    s = _re.sub(r"^[^\w(]+", "", s)            # leading symbols
    return _re.sub(r"\s+", " ", s).strip()


def _score_into(store, cid, base: dict, *, first_seen: str, source: str):
    """Compute hotness for a posting and upsert its machine fields."""
    h = hotness(base["company"], base["role"], base.get("age_days"),
                lane=role_lane(base["role"]))
    existing = (store.get(cid) or {}).get("machine", {})
    store.upsert_machine(cid, {
        **base,
        "source": source,
        "lane": h["lane"], "tier": h["tier"], "brand": h["brand"],
        "role_score": h["role"], "recency": h["recency"],
        "hotness": h["hotness"], "fresh": h["fresh"],
        "dead": False, "fail_count": 0,
        "first_seen": existing.get("first_seen") or first_seen,
        "last_seen": first_seen,
    })


def import_old_tracker_history(store) -> int:
    """One-time, idempotent: pull the actioned rows (Offer/Rejected/Applied/…) from
    the old Application Tracker.xlsx into My Applications so history isn't lost.
    Skips 'To Apply' rows (brand-first rediscovers live openings fresh)."""
    if not OLD_TRACKER.exists():
        return 0
    try:
        from openpyxl import load_workbook
        wb = load_workbook(OLD_TRACKER, read_only=True, data_only=True)
    except Exception:  # noqa: BLE001
        return 0
    seeded = 0
    try:
        ws = wb["Apply Board"] if "Apply Board" in wb.sheetnames else wb.worksheets[0]
        header = None
        col = {}
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() if c is not None else "" for c in row]
            if header is None:
                if "Company" in cells and "Status" in cells:
                    header = cells
                    col = {h.lower(): i for i, h in enumerate(header)}
                continue

            def g(name):
                i = col.get(name.lower())
                return cells[i] if i is not None and i < len(cells) else ""
            company, role, status = g("company"), g("role"), g("status")
            if not company or not role:
                continue
            if status.strip().lower() not in _ACTIONED:
                continue
            url = g("apply") if g("apply").startswith("http") else ""
            # the old "Apply" cell is just the label "Apply ↗"; real url isn't stored
            # readably, so synthesize a stable history id from company+role
            cid = canonical_id(url) if url.startswith("http") else \
                f"hist/{normalize_company_name(company)}/{role.lower()[:40]}"
            if cid in store.postings:
                continue
            store.upsert_machine(cid, {
                "company": company, "role": role, "location": g("location"),
                "url": url, "ats_type": "manual", "source": "history",
                "cycle": g("cycle"), "lane": g("lane") or "Other",
                "posted_date": "", "age_days": None, "full_jd": "",
                "tier": "", "hotness": 0, "fresh": "", "dead": False,
            })
            store.set_human(cid, {"status": status, "applied_date": g("applied"),
                                  "notes": g("notes")})
            seeded += 1
    finally:
        wb.close()
    return seeded


async def refresh(notify: bool = False) -> int:
    # An open-in-Excel lock no longer aborts the whole refresh — we still harvest, score,
    # and update the store JSON, and only DEFER the xlsx render (step 5). That keeps the
    # board data fresh even when it's left open in Excel for days (the old behavior froze
    # the board until Excel was closed). read_back_human can still read an open .xlsx.
    _wait_for_network()      # scheduled runs fire on wake before wifi is up — wait for it
    today = date.today().isoformat()
    store = CuratedStore(STORE_PATH).load()
    prior_cids = set(store.postings.keys())   # to detect genuinely-new postings

    # 1) read back manual edits from the existing curated board
    back = read_back_human(XLSX_PATH)
    for cid, human in back.items():
        if cid in store.postings:
            store.set_human(cid, human)
        else:
            store.add_orphan(cid, human)
    if back:
        print(f"[refresh] merged manual edits on {len(back)} rows", file=sys.stderr)

    # 2) one-time history seed
    seeded = import_old_tracker_history(store)
    if seeded:
        print(f"[refresh] seeded {seeded} history rows from old tracker", file=sys.stderr)

    # 3) harvest — lane 1 (brand-first boards) + lane 2 (wide net: aggregators + Gmail)
    print("[refresh] lane 1: pulling target-company boards…", file=sys.stderr)
    lane1 = await brand_first_source.collect()
    for r in lane1:
        r["_src"] = "brand-board"
    print("[refresh] lane 2: wide net (aggregators + Gmail)…", file=sys.stderr)
    lane2 = await wide_net_source.collect()
    for r in lane2:
        r["_src"] = r["source"]

    # clean display company names (strip aggregator emoji/symbols) + cross-lane dedup
    # by (company, role): same role from two sources -> keep the better one
    # (brand-board > more JD > fresher).
    for r in lane1 + lane2:
        r["company"] = _clean_company(r["company"])

    def _better(a, b):
        a1, b1 = a["_src"] == "brand-board", b["_src"] == "brand-board"
        if a1 != b1:
            return a1
        if len(a.get("full_jd", "")) != len(b.get("full_jd", "")):
            return len(a.get("full_jd", "")) > len(b.get("full_jd", ""))
        # `or 999` is WRONG here: age_days == 0 is falsy, so a role posted TODAY
        # scored 999 and LOST dedup to an older duplicate — exactly inverted, and
        # it penalised precisely the postings most worth applying to first.
        a_age = a.get("age_days")
        b_age = b.get("age_days")
        return (999 if a_age is None else a_age) < (999 if b_age is None else b_age)

    best: dict[tuple, dict] = {}
    for r in lane1 + lane2:
        key = (normalize_company_name(r["company"]), r["role"].strip().lower())
        if key not in best or _better(r, best[key]):
            best[key] = r

    harvested: set[str] = set()
    for r in best.values():
        cid = r["canonical_id"]
        harvested.add(cid)
        _score_into(store, cid, {
            "company": r["company"], "role": r["role"], "location": r["location"],
            "url": r["url"], "ats_type": r["ats_type"], "cycle": r["cycle"],
            "posted_date": r["posted_date"], "age_days": r["age_days"],
            "full_jd": r["full_jd"], "req_id": r.get("req_id", ""),
        }, first_seen=today, source=r["_src"])
    print(f"[refresh] {len(lane1)} lane-1 + {len(lane2)} lane-2 -> "
          f"{len(harvested)} after cross-lane dedup", file=sys.stderr)

    # SAFETY GUARD: a network-less cron run (DNS failures on a sleeping/just-woke Mac)
    # harvests ~nothing. WITHOUT this, the stale-check below would mark every posting
    # dead and wipe the board to 0. If the harvest collapsed but we had a healthy board
    # before, abort and leave the store + xlsx UNTOUCHED (the next good run restores it).
    # A healthy run harvests 100+ roles. Under 50 (when we had a real board before) means
    # a network failure (full or partial), not that the postings genuinely vanished.
    if len(harvested) < 50 and len(prior_cids) > 80:
        print(f"[refresh] ⚠️ harvest collapsed ({len(harvested)} roles vs {len(prior_cids)} "
              f"stored) — almost certainly a network failure. Aborting WITHOUT touching the "
              f"board (no stale-check, no overwrite).", file=sys.stderr)
        return 0

    # 4) re-score everything (recency decays) + stale-check dropouts
    #
    # 2026-08-18 REWRITE — `dead` used to mean "my source stopped listing it",
    # which is NOT the same as "the job closed". Two independent false-dead
    # mechanisms were measured against employers' own ATS APIs:
    #   • wide-net roll-off: the GitHub/Gmail aggregators are ROLLING WINDOWS and
    #     drop older entries to stay readable. 54% of dead wide-net rows were
    #     still open (Palantir x3, IMC Summer 2027, Modal, Binance.US...).
    #   • brand-board fetch failure: a transient timeout looked identical to an
    #     empty board. 14% of dead brand-board rows were still open, incl.
    #     Anduril "2027 Software Engineer Intern".
    # Fixes, in order of how much they recover:
    #   (a) postings from a board that ERRORED this run are exempt entirely
    #   (b) `manual` boards (Google/Meta/Microsoft/Tesla/Apple/Netflix/Uber/
    #       Rippling) are never auto-dead — lane 1 can't fetch them at all, so a
    #       disappearance carries no information. They're the top brands.
    #   (c) wide-net rows need far more strikes, because roll-off is expected
    #   (d) PER-LANE collapse guard: the old guard only tested the COMBINED
    #       harvest (<50), so when lane 1 collapsed and lane 2 stayed healthy the
    #       total cleared the bar and every brand-board posting took a strike.
    #       That happened in 14 of 108 logged runs (13%) — including lane1=5
    #       against a healthy median of 68.
    failed_boards = {normalize_company_name(n)
                     for n in getattr(brand_first_source, "FAILED_BOARDS", set())}
    manual_boards = {normalize_company_name(b["name"]) for b in _boards()
                     if b.get("ats_type") == "manual"}

    lane1_ok = len(lane1) >= LANE1_MIN_HEALTHY
    if not lane1_ok:
        print(f"[refresh] ⚠️ lane-1 harvest is anomalously low ({len(lane1)} < "
              f"{LANE1_MIN_HEALTHY}) — brand-board postings are EXEMPT from the "
              f"stale-check this run (partial-collapse guard).", file=sys.stderr)

    stale = 0
    for cid, rec in store.items():
        m = rec.get("machine", {})
        src = m.get("source", "")
        is_brand = src == "brand-board"
        is_wide = src.startswith("wide:")
        nn = normalize_company_name(m.get("company", ""))

        exempt = (
            nn in failed_boards            # (a) its board errored this run
            or nn in manual_boards         # (b) no API exists to confirm death
            or (is_brand and not lane1_ok)  # (d) lane-1 partial collapse
        )
        strikes_needed = WIDE_STALE_STRIKES if is_wide else STALE_STRIKES

        if (is_brand or is_wide) and cid not in harvested and not exempt:
            m["fail_count"] = int(m.get("fail_count", 0)) + 1
            if m["fail_count"] >= strikes_needed and not m.get("dead"):
                m["dead"] = True
                stale += 1
        elif cid in harvested:
            m["fail_count"] = 0
            m["dead"] = False               # reappeared -> it was never dead
        # refresh age + re-score from stored posted_date
        if m.get("company") and m.get("role"):
            age = brand_first_source._age_from_date(m.get("posted_date", ""))
            h = hotness(m["company"], m["role"], age, lane=role_lane(m["role"]))
            m.update({"age_days": age, "hotness": h["hotness"], "fresh": h["fresh"],
                      "recency": h["recency"], "tier": h["tier"], "brand": h["brand"],
                      "lane": h["lane"], "role_score": h["role"]})

    # 4b) FIT PASS — the AI reads each JD: fit score + why + disqualifier flag.
    #     Cached by JD-hash, so only new/changed postings cost anything (idempotent).
    try:
        from fit_pass import run_fit_pass
        fs = run_fit_pass(store)
        print(f"[refresh] fit: {fs.scored} scored, {fs.cached} cached, "
              f"{fs.no_jd} no-JD, {fs.errors} errors · ~${fs.cost:.4f}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — never block the refresh on the LLM
        print(f"[refresh] fit pass skipped: {type(e).__name__}: {e}", file=sys.stderr)

    # 5) persist the store ALWAYS; render the xlsx unless Excel has it open (defer if so)
    gen = datetime.now().strftime("%Y-%m-%d %H:%M")
    store.save(gen)
    if is_locked(XLSX_PATH):
        print(f"\n⏸️  Store updated ({len(store.postings)} postings, {stale} newly stale) — xlsx "
              f"render DEFERRED ('{XLSX_PATH.name}' is open in Excel). The board re-renders on the "
              f"next run with Excel closed; no data lost, notify still runs.", file=sys.stderr)
    else:
        counts = write_board(store, XLSX_PATH, gen)
        print(f"\n✅ Curated Board refreshed — {counts['queue']} in queue, "
              f"{counts['applications']} applications, {counts.get('reviewed', 0)} reviewed/skipped "
              f"({len(harvested)} live brand-board roles, {stale} newly stale)")
        print(f"   {XLSX_PATH}")

    # 6) notify (only when scheduled, only on genuinely-new postings, never on the
    #    first-ever run; silent otherwise — matches the other crons' discipline)
    new_cids = harvested - prior_cids
    new_live = {c for c in new_cids
                if not (store.get(c) or {}).get("machine", {}).get("dead")}
    if notify and prior_cids and new_live:
        msg = _new_postings_digest(store, new_live)
        if msg and _send_telegram(msg):
            print(f"[notify] sent Telegram digest: {len(new_live)} new role(s)", file=sys.stderr)
    elif notify:
        print(f"[notify] {len(new_live)} new (prior_store={bool(prior_cids)}) — "
              f"no message sent", file=sys.stderr)
    return 0


async def validate_boards() -> int:
    import ats_router as A
    from company_boards import boards
    print(f"{'COMPANY':22} {'ATS':16} {'ROLES':>6} {'INTERN':>7}")
    dead = []
    async with A.make_client() as c:
        async def chk(b):
            if b["ats_type"] == "manual":
                return b["name"], "manual", 0, 0
            try:
                recs = await A.fetch_board(c, b)
                return (b["name"], b["ats_type"], len(recs),
                        len([r for r in recs if A.default_intern_filter(r.title)]))
            except Exception as e:  # noqa: BLE001
                return b["name"], f"ERR {type(e).__name__}", -1, -1
        for name, ats, roles, interns in await asyncio.gather(*[chk(b) for b in boards()]):
            flag = "✓" if roles > 0 else ("· manual" if ats == "manual" else "✗ DEAD")
            if roles == 0 and ats != "manual":
                dead.append(name)
            print(f"{name:22} {ats:16} {roles:>6} {interns:>7}  {flag}")
    if dead:
        print("\nDEAD/EMPTY — fix token in company_boards.py:", ", ".join(dead))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh the curated brand-first board.")
    ap.add_argument("--validate-boards", action="store_true",
                    help="Ping every board and report role counts (no write).")
    ap.add_argument("--notify", action="store_true",
                    help="Send a Telegram digest if genuinely-new postings appeared "
                         "(silent otherwise). Used by the scheduled run.")
    args = ap.parse_args()
    if args.validate_boards:
        return asyncio.run(validate_boards())
    return asyncio.run(refresh(notify=args.notify))


if __name__ == "__main__":
    sys.exit(main())
