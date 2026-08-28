#!/usr/bin/env python3
"""
add_jd.py — paste a job description in by hand for a role the scrapers cannot read.

For the residue that no rung of the fetch ladder can reach — chiefly Tesla, which sits
behind Akamai Bot Manager and answers automation with 403 no matter the fingerprint.
Sparsh can open those in his own browser, copy the text, and drop it in here; the role
then scores exactly like any other instead of showing 👀 forever.

  add_jd.py <url-or-req-id> --file jd.txt
  add_jd.py <url-or-req-id> < jd.txt          # or piped on stdin
  add_jd.py --list                            # what is still unreadable

Matching accepts a full URL, or any distinctive fragment of one — a Tesla req id like
281271 is enough. Refuses to run if the fragment matches more than one role, because
writing a JD onto the wrong posting is worse than not writing it at all.

Sets machine.jd_source = "manual-paste" so these are auditable later, and clears the
cached fit score so the next refresh re-scores the role against the real text.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

STORE = Path("/Users/sparshk/Documents/School Vault - UofT/06 - Internships/Job Search/curated_postings.json")
MIN_CHARS = 400
MAX_CHARS = 12_000


def _clean(raw: str) -> str:
    t = re.sub(r"\r", "", raw)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def main() -> int:
    doc = json.loads(STORE.read_text())
    ps = doc["postings"]
    live = {cid: v["machine"] for cid, v in ps.items() if not v["machine"].get("dead")}

    if "--list" in sys.argv:
        rows = [(m.get("tier") or "?", m.get("company") or "?", m.get("role") or "",
                 m.get("url") or "") for m in live.values()
                if not (m.get("full_jd") or "").strip()]
        rows.sort()
        print(f"{len(rows)} live roles with no JD:\n")
        for t, co, role, u in rows:
            print(f"  [{t}] {co[:18]:<18} {role[:46]:<46} {u[-34:]}")
        return 0

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    needle = args[0]

    if "--file" in sys.argv:
        raw = Path(sys.argv[sys.argv.index("--file") + 1]).read_text()
    else:
        if sys.stdin.isatty():
            print("error: no JD given. Use --file <path> or pipe it on stdin.", file=sys.stderr)
            return 2
        raw = sys.stdin.read()

    jd = _clean(raw)
    if len(jd) < MIN_CHARS:
        print(f"error: that is only {len(jd)} chars — too short to be a real JD "
              f"(need {MIN_CHARS}+). Nothing written.", file=sys.stderr)
        return 1

    hits = [cid for cid, m in live.items() if needle in (m.get("url") or "")]
    if not hits:
        print(f"error: no live role whose URL contains {needle!r}. "
              f"Try add_jd.py --list", file=sys.stderr)
        return 1
    if len(hits) > 1:
        print(f"error: {needle!r} matches {len(hits)} roles — be more specific:",
              file=sys.stderr)
        for cid in hits:
            print(f"    {live[cid].get('company')} — {live[cid].get('role','')[:50]}\n"
                  f"      {live[cid].get('url')}", file=sys.stderr)
        return 1

    cid = hits[0]
    m = ps[cid]["machine"]
    had = len(m.get("full_jd") or "")
    m["full_jd"] = jd[:MAX_CHARS]
    m["jd_source"] = "manual-paste"
    m["jd_added"] = date.today().isoformat()
    for k in ("fit_score", "fit_why", "fit_disqualifier"):
        m.pop(k, None)          # force a re-score against the real text

    bak = STORE.with_suffix(f".json.bak-addjd-{date.today():%Y%m%d}")
    if not bak.exists():
        shutil.copy2(STORE, bak)
    STORE.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print(f"✅ {m.get('company')} — {m.get('role','')[:56]}")
    print(f"   {len(jd)} chars written (was {had}); fit cleared, will re-score next refresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
