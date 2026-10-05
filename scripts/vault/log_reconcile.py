#!/usr/bin/env python3
"""
log_reconcile.py — ONE-TIME: merge the VPS's diverged Log.md into the Mac's before sharding.

Obsidian Sync was jammed (quota) Sep 14 → Oct 5 2026, so each side appended entries the
other never received: the Mac's sessions (~94%) and the VPS's `hermes:` prefill entries.
Union by FULL entry text: every Mac entry stays exactly where it is; each VPS entry whose
exact text the Mac lacks is appended (log_shard then files it under its header date).

NEAR-DUPLICATES are flagged, never auto-resolved (audit 2026-10-04): an entry amended in
place on one side shares its opening with the other side's version. Same header line +
different body, or same first 200 chars + different text → printed for a human.

  log_reconcile.py --mac <vault>/Log.md --vps /tmp/vps_Log.md [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from log_append import split_entries  # noqa: E402


def norm(e: str) -> str:
    return e.rstrip("\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mac", required=True)
    ap.add_argument("--vps", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    mac_p, vps_p = Path(a.mac), Path(a.vps)
    mac_text = mac_p.read_text(encoding="utf-8")
    _, mac = split_entries(mac_text)
    _, vps = split_entries(vps_p.read_text(encoding="utf-8"))
    have = {norm(e) for e in mac}
    by_head = {norm(e).splitlines()[0]: norm(e) for e in mac}
    by_200 = {norm(e)[:200]: norm(e) for e in mac}
    new, near = [], []
    for e in vps:
        n = norm(e)
        if n in have:
            continue
        head = n.splitlines()[0]
        if head in by_head or n[:200] in by_200:
            near.append((head, by_head.get(head) or by_200.get(n[:200]), n))
            continue
        new.append(e if e.endswith("\n") else e + "\n")
    print(f"log_reconcile: Mac {len(mac)} entries, VPS {len(vps)}; "
          f"{len(new)} VPS-only to add, {len(near)} near-duplicate(s) for review")
    for e in new:
        print("  + " + norm(e).splitlines()[0][:150])
    for head, m, v in near:
        print(f"  ? NEAR-DUP (NOT merged): {head[:130]}\n"
              f"      mac {len(m)} chars vs vps {len(v)} chars")
    if a.dry_run or not new:
        return 0 if not near else 2
    bak = mac_p.with_suffix(".md.pre-reconcile")
    shutil.copy2(mac_p, bak)
    with open(mac_p, "a", encoding="utf-8") as f:
        if not mac_text.endswith("\n"):
            f.write("\n")
        for e in new:
            f.write("\n" + e)
    after = split_entries(mac_p.read_text(encoding="utf-8"))[1]
    ok = len(after) == len(mac) + len(new)
    print(f"log_reconcile: {'✅' if ok else '❌'} Mac now {len(after)} entries "
          f"(backup {bak.name})")
    return 0 if ok and not near else (2 if ok else 1)


if __name__ == "__main__":
    sys.exit(main())
