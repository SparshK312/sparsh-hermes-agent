#!/usr/bin/env python3
"""
jd_backfill.py — re-fetch JDs for stored rows that have none.

One-off / occasional. The normal refresh only enriches rows it harvests; a role that
failed its JD fetch once keeps an empty `full_jd` forever and is permanently unscoreable
by fit_pass, which renders it as 👀 "AI couldn't read this" on the board.

Only MACHINE fields are written. Human fields (status / applied_date / notes /
priority_override) are never touched — the sheet owns those.

  --dry      probe only, change nothing (default is to write)
  --limit N  cap the number attempted
  --browser  after the cheap rungs, retry the residue through a real browser
             (rung 3, ~5s/page — see browser_fetch.py). Off by default.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ats_router as A  # noqa: E402

STORE = Path("/Users/sparshk/Documents/School Vault - UofT/06 - Internships/Job Search/curated_postings.json")
CONCURRENCY = 6
MIN_USABLE = 500          # below this it is nav chrome, not a job description


async def main() -> int:
    dry = "--dry" in sys.argv
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0

    doc = json.loads(STORE.read_text())
    ps = doc["postings"]
    todo = [(cid, v["machine"]) for cid, v in ps.items()
            if not v["machine"].get("dead")
            and not (v["machine"].get("full_jd") or "").strip()
            and (v["machine"].get("url") or "").strip()]
    if limit:
        todo = todo[:limit]
    print(f"[backfill] {len(todo)} live rows with no JD{' (dry run)' if dry else ''}")

    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(client, cid, m):
        async with sem:
            try:
                rec = await A.fetch_jd_record(client, m["url"])
            except Exception as e:  # noqa: BLE001
                return cid, m, None, f"{type(e).__name__}: {e}"
            return cid, m, rec, rec.error or ""

    async with A.make_client() as client:
        results = await asyncio.gather(*[one(client, cid, m) for cid, m in todo])

    recovered = dead = failed = 0
    by_ats: Counter = Counter()
    for cid, m, rec, err in results:
        if rec is None:
            failed += 1
            continue
        jd = (rec.full_jd or "").strip()
        if len(jd) >= MIN_USABLE:
            recovered += 1
            by_ats[m.get("ats_type") or "?"] += 1
            if not dry:
                mm = ps[cid]["machine"]
                mm["full_jd"] = jd
                # Refresh the cheap metadata the same call already returned, but never
                # overwrite something good with something empty.
                for src, key in ((rec.title, "role"), (rec.location, "location"),
                                 (rec.posted_date, "posted_date")):
                    if src and not mm.get(key):
                        mm[key] = src
                mm.pop("fit_score", None)      # force a re-score now that a JD exists
                mm.pop("fit_why", None)
                mm.pop("fit_disqualifier", None)
        elif rec.dead or " 404" in f" {err}" or " 410" in f" {err}":
            dead += 1
            if not dry:
                ps[cid]["machine"]["dead"] = True
                ps[cid]["machine"]["dead_reason"] = f"jd-backfill {date.today().isoformat()}: {err[:80]}"
        else:
            failed += 1

    # ── rung 3: a real browser, only for what rungs 1-2 could not read ──────────
    if "--browser" in sys.argv:
        residue = [(cid, m) for cid, m, rec, err in results
                   if rec is not None and not rec.dead
                   and len((rec.full_jd or "").strip()) < MIN_USABLE]
        if residue:
            from browser_fetch import fetch_rendered
            print(f"[backfill] rung 3: rendering {len(residue)} page(s) in a browser…")
            rendered = await fetch_rendered([m["url"] for _, m in residue])
            for cid, m in residue:
                txt = rendered.get(m["url"], "")
                if len(txt) >= MIN_USABLE:
                    recovered += 1
                    failed -= 1
                    by_ats[(m.get("ats_type") or "?") + "+browser"] += 1
                    if not dry:
                        mm = ps[cid]["machine"]
                        mm["full_jd"] = txt[:12_000]
                        mm.pop("fit_score", None)
                        mm.pop("fit_why", None)
                        mm.pop("fit_disqualifier", None)
            print(f"[backfill] rung 3 recovered {len([1 for _, m in residue if len(rendered.get(m['url'],''))>=MIN_USABLE])}")

    print(f"[backfill] recovered {recovered} · marked dead {dead} · still failing {failed}")
    if by_ats:
        print("[backfill] recovered by ats: " + ", ".join(f"{k}={v}" for k, v in by_ats.most_common()))
    if dry:
        print("[backfill] dry run — nothing written")
        return 0
    if recovered or dead:
        bak = STORE.with_suffix(f".json.bak-jdbackfill-{date.today():%Y%m%d}")
        shutil.copy2(STORE, bak)
        STORE.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
        print(f"[backfill] store written; backup at {bak.name}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
