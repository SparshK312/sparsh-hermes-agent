#!/usr/bin/env python3
"""
worklist.py — the apply-now query, as CODE instead of an ad-hoc one-liner.

WHY THIS EXISTS
---------------
Every worklist since Aug 10 was produced by hand-writing a filter against the
store, and each rewrite dropped something different:

  • all of them filtered `fit_score >= 70`, which SILENTLY EXCLUDES every posting
    the fit pass couldn't read (no JD -> no score). That hid ~96 live roles
    including Notion x4 and Tesla x15.
  • none excluded Fall 2027 — a term he cannot take, because his final academic
    year is Fall 2027 - Winter 2028. Four Fall-2027 roles at fit 84-88 sat in the
    Aug-11 "ranked queue" as recommendations.
  • none capped per-company, so a single employer posting 20 near-identical reqs
    (TikTok, 2026-08-18) takes over the entire top of the list.

One query, one place, so a fix lands everywhere.

USAGE
    python worklist.py                     # ranked, capped, markdown table
    python worklist.py --cycle "Winter 2027"
    python worklist.py --limit 40 --per-company 3
    python worklist.py --unscored          # the no-JD "verify manually" lane
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store_paths  # noqa: E402
VAULT = store_paths.vault_root()
STORE = store_paths.store_path()

# The only terms he can actually take. Rotation #3 = Winter 2027 (Jan-Apr),
# rotation #4 = Summer 2027 (May-Aug). US employers often label Jan-Apr "Spring".
# Fall 2027 onward = final academic year, NOT takeable.
TAKEABLE = ("Winter 2027", "Spring 2027", "Summer 2027")


def load():
    return json.loads(STORE.read_text())["postings"]


def takeable(cycle: str | None) -> bool:
    """Untagged passes (46% of the board has no cycle and the good stuff hides
    there) — but an explicitly out-of-window term is rejected."""
    c = cycle or ""
    if not c.strip():
        return True
    return any(t in c for t in TAKEABLE)


def rows(min_fit=70, cycle=None, unscored=False):
    out = []
    for rec in load().values():
        m, h = rec["machine"], rec.get("human", {})
        if m.get("dead"):
            continue
        if (h.get("status") or "To Apply") != "To Apply":
            continue
        if not takeable(m.get("cycle")):
            continue
        if (m.get("fit_disqualifier") or "none") not in ("none", ""):
            continue
        if cycle and cycle.lower() not in (m.get("cycle") or "").lower():
            continue
        fs = m.get("fit_score")
        if unscored:
            if fs is not None:
                continue
        elif fs is None or fs < min_fit:
            continue
        out.append(m)
    return out


def rank(items, per_company=2, limit=40):
    """Hotness desc, then fit. Capped per company so one employer's 20 near-identical
    reqs can't take the whole list — he applies to companies, not to req IDs."""
    items.sort(key=lambda m: (-(m.get("hotness") or 0), -(m.get("fit_score") or 0)))
    seen, kept, overflow = {}, [], {}
    for m in items:
        co = (m.get("company") or "?").lower()
        if seen.get(co, 0) >= per_company:
            overflow[co] = overflow.get(co, 0) + 1
            continue
        seen[co] = seen.get(co, 0) + 1
        kept.append(m)
        if len(kept) >= limit:
            break
    return kept, overflow


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-fit", type=int, default=70)
    ap.add_argument("--cycle", default=None, help='e.g. "Winter 2027"')
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--per-company", type=int, default=2)
    ap.add_argument("--unscored", action="store_true",
                    help="the no-JD lane the fit pass could not read — verify by hand")
    a = ap.parse_args()

    items = rows(a.min_fit, a.cycle, a.unscored)
    kept, overflow = rank(items, a.per_company, a.limit)

    label = "UNSCORED (no JD — verify manually)" if a.unscored else f"fit >= {a.min_fit}"
    print(f"# Apply-now worklist — {label}"
          f"{' · ' + a.cycle if a.cycle else ''}")
    print(f"\n{len(items)} in pool · showing {len(kept)} "
          f"(max {a.per_company}/company)\n")
    print("| Hot | Fit | T | Company | Role | Location | Cycle | Age | Apply |")
    print("|---|---|---|---|---|---|---|---|---|")
    for m in kept:
        fit = m.get("fit_score")
        print(f"| {m.get('hotness') or 0} | {fit if fit is not None else '👀'} "
              f"| {m.get('tier') or '?'} | **{m.get('company')}** "
              f"| {str(m.get('role'))[:62]} | {str(m.get('location'))[:26]} "
              f"| {m.get('cycle') or '—'} | {m.get('age_days')}d "
              f"| [↗]({m.get('url')}) |")
    if overflow:
        extra = ", ".join(f"{c} +{n}" for c, n in
                          sorted(overflow.items(), key=lambda x: -x[1])[:8])
        print(f"\n_Capped (more reqs available at the same company): {extra}_")
    return 0


if __name__ == "__main__":
    sys.exit(main())
