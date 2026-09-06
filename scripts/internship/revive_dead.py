#!/usr/bin/env python3
"""
revive_dead.py — one-time (and re-runnable) repair for false-dead postings.

WHY THIS EXISTS
---------------
Until 2026-08-18 the board's `dead` flag meant "my source stopped listing it",
which is not the same as "the job closed". Two independent mechanisms produced
false deaths:

  • wide-net roll-off — the GitHub/Gmail aggregators are ROLLING WINDOWS and drop
    older entries to stay readable. Measured against employers' own ATS APIs:
    54% of dead wide-net rows were still open (Palantir x3, IMC Trading Summer
    2027, Modal, Binance.US, Truveta, CTGT...).
  • brand-board fetch failure — `_one_board()` swallowed every error and returned
    [], indistinguishable from an empty board, so a transient ReadTimeout struck
    every posting from that company. The runtime log shows 1,755 such failures
    across 87 runs. 14% of dead brand-board rows were still open, including
    Anduril's live "2027 Software Engineer Intern".

curate.py now prevents both going forward. This script repairs the backlog by
asking each employer's OWN ATS whether the req is still posted, and reviving the
ones that are. It is the "verify, don't infer" rule applied retroactively.

DESIGN
------
Three states, never two (the model career-ops converged on independently):
    active    -> revive
    expired   -> stays dead
    uncertain -> stays dead, but fail_count is reset so it gets a fair re-check
                 rather than being condemned by an error we caused

Only ATSs with a real public listing API are consulted; anything else is left
alone rather than guessed at.

USAGE
    python revive_dead.py --dry-run      # report only, touches nothing
    python revive_dead.py                # apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from curated_store import CuratedStore  # noqa: E402

import store_paths  # noqa: E402
VAULT = store_paths.vault_root()
STORE_PATH = store_paths.store_path()

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
TIMEOUT = 20
_board_cache: dict[str, set[str]] = {}


def _get(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _board_ids(kind: str, org: str) -> set[str]:
    """Every currently-posted job id on one board. Cached — many rows share a board."""
    key = f"{kind}:{org}"
    if key in _board_cache:
        return _board_cache[key]
    ids: set[str] = set()
    if kind == "greenhouse":
        d = _get(f"https://boards-api.greenhouse.io/v1/boards/{org}/jobs")
        ids = {str(j.get("id")) for j in d.get("jobs", [])}
    elif kind == "ashby":
        d = _get(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
        ids = {str(j.get("id")) for j in d.get("jobs", [])}
    elif kind == "lever":
        d = _get(f"https://api.lever.co/v0/postings/{org}?mode=json")
        ids = {str(j.get("id")) for j in d}
    _board_cache[key] = ids
    return ids


def classify(url: str) -> str:
    """'active' | 'expired' | 'uncertain' — never a guess dressed as a fact."""
    if not url:
        return "uncertain"
    try:
        m = re.search(r"greenhouse\.io/(?:embed/job_app\?token=)?([a-z0-9_-]+)/jobs/(\d+)", url, re.I)
        if m:
            return "active" if m.group(2) in _board_ids("greenhouse", m.group(1)) else "expired"
        m = re.search(r"ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", url, re.I)
        if m:
            return "active" if m.group(2) in _board_ids("ashby", m.group(1)) else "expired"
        m = re.search(r"lever\.co/([^/?#]+)/([0-9a-f-]{36})", url, re.I)
        if m:
            return "active" if m.group(2) in _board_ids("lever", m.group(1)) else "expired"
    except Exception:            # network/API failure is OUR problem, not the job's
        return "uncertain"
    return "uncertain"           # no public API -> refuse to guess


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = ap.parse_args()

    store = CuratedStore(STORE_PATH).load()
    dead = [(cid, rec["machine"]) for cid, rec in store.items()
            if rec.get("machine", {}).get("dead")]
    print(f"{len(dead)} postings currently marked dead", file=sys.stderr)

    checkable = [(c, m) for c, m in dead
                 if any(k in (m.get("url") or "")
                        for k in ("greenhouse", "ashbyhq", "lever.co"))]
    print(f"{len(checkable)} have a public ATS API and can be verified\n", file=sys.stderr)

    with ThreadPoolExecutor(10) as ex:
        verdicts = list(ex.map(lambda t: classify(t[1].get("url") or ""), checkable))

    counts, revived = Counter(verdicts), []
    for (cid, m), v in zip(checkable, verdicts):
        if v == "active":
            revived.append(m)
            if not args.dry_run:
                store.upsert_machine(cid, {"dead": False, "fail_count": 0})
        elif v == "uncertain" and not args.dry_run:
            # our failure, not the employer's — give it a clean slate to re-check
            store.upsert_machine(cid, {"fail_count": 0})

    print(f"  active (revive) : {counts['active']}")
    print(f"  expired (stays) : {counts['expired']}")
    print(f"  uncertain       : {counts['uncertain']}  (left dead, strikes reset)")

    if revived:
        print("\nREVIVED — these reqs are open on the employer's own board:")
        for m in sorted(revived, key=lambda x: -(x.get("hotness") or 0))[:40]:
            print(f"  [{m.get('tier','?')}] {str(m.get('company'))[:22]:22s} | "
                  f"{str(m.get('role'))[:52]:52s} | {m.get('cycle') or '-'}")
        if len(revived) > 40:
            print(f"  …and {len(revived) - 40} more")

    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0
    from datetime import datetime
    store.save(datetime.now().strftime("%Y-%m-%d %H:%M"))
    print(f"\n✅ store updated — {len(revived)} postings revived. "
          f"Re-run curate.py to regenerate the xlsx.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
