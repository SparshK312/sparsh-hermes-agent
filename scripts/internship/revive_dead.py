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
import urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from curated_store import CuratedStore  # noqa: E402
from internship_scraper import PAST_PERIODS_LOWER  # noqa: E402

import store_paths  # noqa: E402
VAULT = store_paths.vault_root()
STORE_PATH = store_paths.store_path()

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
TIMEOUT = 20
_board_cache: dict[str, set[str]] = {}


def _get(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=TIMEOUT) as r:
        return json.loads(r.read())


_WORKDAY_RE = re.compile(
    r"^https?://(?P<tenant>[^./]+)\.wd\d+\.myworkdayjobs\.com/"
    r"(?:[a-z]{2}-[A-Z]{2}/)?"          # optional /en-US/ locale segment
    r"(?P<site>[^/]+)/job/(?P<rest>.+)$",
    re.I,
)


def _workday_status(url: str) -> str:
    """Workday has no board-wide id list worth pulling (Capital One alone posts 1,178
    jobs, 20 per page), but it DOES serve one job as JSON at
    /wday/cxs/<tenant>/<site>/job/<path>. So check the single req directly.

    200 -> active · 404 -> expired · anything else -> uncertain. A wrong tenant guess
    reads as 404, i.e. 'stays dead', which is exactly today's behaviour — this can
    only ever recover rows, never bury a live one."""
    m = _WORKDAY_RE.match(url.strip())
    if not m:
        return "uncertain"
    origin = url.split("/", 3)[0] + "//" + url.split("/", 3)[2]
    api = f"{origin}/wday/cxs/{m.group('tenant').lower()}/{m.group('site')}/job/{m.group('rest')}"
    req = urllib.request.Request(api, headers={**UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            if r.status != 200:
                return "uncertain"
            return "active" if json.loads(r.read()).get("jobPostingInfo") else "expired"
    except urllib.error.HTTPError as e:
        return "expired" if e.code == 404 else "uncertain"
    except Exception:
        return "uncertain"


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
        if "myworkdayjobs.com" in url.lower():
            return _workday_status(url)
    except Exception:            # network/API failure is OUR problem, not the job's
        return "uncertain"
    return "uncertain"           # no public API -> refuse to guess


def worth_reviving(m: dict, store=None) -> tuple[bool, str]:
    """🔴 THE ONLY THING A REVIVAL MAY NOT BRING BACK IS A DUPLICATE.

    History, because the wrong version of this function shipped first. On 2026-09-08 the
    Workday branch above tripled the checkable set and one run revived 331 postings; the
    queue went 108 -> 276. The first fix gated on `tier == "C"` plus a stale `last_seen`.
    Both selectors were wrong and an audit caught it before it ran:

      * `last_seen` tracks the AGGREGATOR's rolling window, not the employer. All 172
        candidates sat at fail_count=1 -- ATS-confirmed open, then one strike from that
        day's roll-off. An independent check hit each employer's own board: 172/172 OPEN.
      * `tier == "C"` is a DEFAULT, not a judgement. brand_tier() returns "C" for any
        company absent from a ~121-name table, so the filter really meant "the table has
        never heard of this employer" -- which swept up 21 WINTER 2027 reqs in Toronto
        (BMO, RBC, Manulife, CAE, GM Markham), his scarcest cycle in his home city.

    Killing on staleness re-creates the exact false-dead bug this script exists to repair
    (its own header measures 54% of dead wide-net rows as still open). So relevance is NOT
    this script's job -- the ATS answer is. The one real exclusion is a row we ourselves
    marked dead as a duplicate: reviving that resurrects a twin of a req he already
    settled, and on 2026-09-08 it put three Autodesk 'Not a Fit' calls, an Intel
    Master's-only req and an NVIDIA req whose deadline passed back in front of him."""
    cycle = str(m.get("cycle") or "").strip().lower()
    if cycle and cycle in PAST_PERIODS_LOWER:
        # A calendar fact, not a judgement: he is at Shopify through Dec 18 2026, so a
        # Fall-2026 req cannot be worked no matter how open it is. Without this, one run
        # would have added 129 Fall-2026 rows to the queue.
        return False, "past-cycle"

    raw = (m.get("dead_reason") or "").strip()
    if not raw.lower().startswith("duplicate of"):
        return True, ""

    # 🔴 THE REASON MUST STILL BE TRUE. `dead_reason` is not durable: curate.py
    # resurrects a re-harvested row and (before 2026-09-08) left the string behind, so a
    # row could carry "duplicate of X" long after X was gone. Gating on the string alone
    # made that a PERMANENT burial nothing could clear -- ten rows were already primed
    # for it, including two Bank of Montreal Winter-2027 Toronto co-ops. So resolve the
    # named keeper and only refuse when it is genuinely still alive to represent this
    # requisition. Unknown or dead keeper -> check the row like any other.
    keeper = raw[len("duplicate of "):].split(" (already")[0].strip()
    rec = None
    if store is not None:
        try:
            rec = store.postings.get(keeper)
        except AttributeError:
            rec = store.get(keeper) if hasattr(store, "get") else None
    if rec is not None and not (rec.get("machine") or {}).get("dead"):
        return False, "duplicate"
    return True, ""


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
                        for k in ("greenhouse", "ashbyhq", "lever.co", "myworkdayjobs.com"))]
    print(f"{len(checkable)} have a public ATS API and can be verified\n", file=sys.stderr)

    # Relevance bar BEFORE the network calls -- cheaper, and it keeps the queue usable.
    skipped = Counter()
    relevant = []
    for cid, m in checkable:
        ok, why = worth_reviving(m, store)
        if ok:
            relevant.append((cid, m))
        else:
            skipped.update([why])
    if skipped:
        print(f"  not worth reviving: {dict(skipped)} "
              f"(left dead on purpose — see worth_reviving())", file=sys.stderr)
    checkable = relevant
    print(f"{len(checkable)} pass the relevance bar and will be checked\n", file=sys.stderr)

    with ThreadPoolExecutor(10) as ex:
        verdicts = list(ex.map(lambda t: classify(t[1].get("url") or ""), checkable))

    counts, revived = Counter(verdicts), []
    for (cid, m), v in zip(checkable, verdicts):
        if v == "active":
            revived.append(m)
            if not args.dry_run:
                # 🔴 CLEAR dead_reason TOO. Leaving it behind produced 11 live rows
                # that literally read dead_reason="duplicate of <cid>" (2026-09-08) --
                # the row asserting its own duplicate origin while sitting in the queue.
                store.upsert_machine(cid, {"dead": False, "fail_count": 0, "dead_reason": ""})
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
