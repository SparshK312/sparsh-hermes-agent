#!/usr/bin/env python3
"""
log_shard.py — ONE-TIME cutover: split the monolithic vault Log.md into day files.

    Log.md  ->  Log/YYYY/YYYY-MM/YYYY-MM-DD.md   (+ Log/README.md, + a MOVED stub)

Each entry goes to the day in its OWN header (`## [YYYY-MM-DD…]`; a range like
`[2026-10-02/03]` goes to its first date). Entries keep their exact bytes and, within a
day, their original order — including the ~17 that sit out of chronological order in the
monolith. Text before the first header (frontmatter + the old instructions) is preserved
verbatim inside Log/README.md.

VERIFICATION IS BY CONTENT, NOT COUNT (audit 2026-10-04): the multiset of per-entry
SHA-1s before must equal the multiset after, read back from disk. A count + byte-sum
check would pass a run that duplicated one entry and dropped another.

  log_shard.py --vault <path> --dry-run     # report only, write nothing
  log_shard.py --vault <path>               # write Log/, README, stub; verify; exit 1 on mismatch
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter, OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from log_append import HEADER_RE, SENTINEL_DATE, day_file, split_entries, stub_text  # noqa: E402

README = """---
type: readme
status: reference
created: 2026-10-05
---

# Vault Log — one file per day

**Where:** `Log/YYYY/YYYY-MM/YYYY-MM-DD.md`. The file is chosen by the date in the entry
header, so `grep` the headers rather than trusting file names for anything subtle.

**Why it moved (2026-10-05).** The single `Log.md` reached 3.2 MB. Obsidian Sync stores a
full copy of a note on every save, so ~14 saves a day re-uploaded ~45 MB/day and 462
versions filled the whole 1 GiB Sync quota. Sync jammed in both directions from Sep 14
and Hermes worked from a vault three days stale. A day file is a few KB, so the same
saves now cost ~40× less.

**Write** — never edit by hand, never append to `Log.md`:

    python3 Scripts/log_append.py <action> "<scope>" "<one-line summary>" --body "…"

`<action>` ∈ ingest · update · decision · archive · lint · schema. The script picks the
day file, takes a lock (parallel sessions are safe) and enforces the header format.

**Read**

    .venv/bin/python Scripts/catchup.py            # last 7 days of headers + prep state
    .venv/bin/python Scripts/recall.py "kaisa"     # find anything, ranked, compact
    grep -rh '^## \\[' Log/ | sort -s -k2,2 | tail -20       # latest headers, all days
    grep -rl 'Grade Tracker' Log/                   # which days mention something

`Log.md` is a stub containing only a MOVED sentinel header; anything appended to it is
swept into the right day file by `log_append.py --sweep` (catchup.py runs it).

---

## The original Log.md preamble (verbatim, kept for the record)

"""


def sha(s: str) -> str:
    return hashlib.sha1(s.rstrip("\n").encode()).hexdigest()


def group(entries: list[str]) -> "OrderedDict[str, list[str]]":
    days: "OrderedDict[str, list[str]]" = OrderedDict()
    for e in entries:
        date = HEADER_RE.match(e).group(1)
        days.setdefault(date, []).append(e)
    return days


def render_day(date: str, entries: list[str]) -> str:
    body = "".join(e if e.endswith("\n") else e + "\n" for e in entries)
    return f"---\ntype: log\ndate: {date}\n---\n\n" + body


def read_back(vault: Path) -> list[str]:
    out = []
    for p in sorted((vault / "Log").rglob("????-??-??.md")):
        _, entries = split_entries(p.read_text(encoding="utf-8"))
        out += entries
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    vault = Path(a.vault)
    src = vault / "Log.md"
    text = src.read_text(encoding="utf-8")
    pre, entries = split_entries(text)
    entries = [e for e in entries if not e.startswith(f"## [{SENTINEL_DATE}]")]
    if not entries:
        print("log_shard: Log.md holds no entries (already sharded?) — nothing to do")
        return 1
    days = group(entries)
    before = Counter(sha(e) for e in entries)
    print(f"log_shard: {len(entries)} entries, {len(days)} days, "
          f"{min(days)} → {max(days)}, {len(text):,} bytes; preamble {len(pre):,} bytes")
    if (vault / "Log").exists() and any((vault / "Log").rglob("????-??-??.md")):
        print("log_shard: Log/ already has day files — refusing to merge into them")
        return 1
    if a.dry_run:
        big = sorted(((sum(len(e) for e in v), d) for d, v in days.items()), reverse=True)[:5]
        print("  largest days:", ", ".join(f"{d} {n/1024:.0f} KB" for n, d in big))
        return 0
    for date, es in days.items():
        p = day_file(vault, date)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_day(date, es), encoding="utf-8")
    (vault / "Log" / "README.md").write_text(README + pre, encoding="utf-8")
    after_entries = read_back(vault)
    after = Counter(sha(e) for e in after_entries)
    if after != before:
        missing = before - after
        extra = after - before
        print(f"log_shard: ❌ VERIFY FAILED — missing {sum(missing.values())}, "
              f"extra {sum(extra.values())}. Log.md left untouched.", file=sys.stderr)
        return 1
    src.write_text(stub_text(), encoding="utf-8")
    print(f"log_shard: ✅ {len(after_entries)} entries verified by content across "
          f"{len(days)} day files; Log.md replaced by the MOVED stub")
    return 0


if __name__ == "__main__":
    sys.exit(main())
