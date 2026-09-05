#!/usr/bin/env python3
"""
board_facts.py — the live board, as facts, for whatever needs to talk about it.

WHY THIS EXISTS (2026-09-04). Two of Hermes's daily messages were confidently wrong
about the job search because neither of them read the board:

  • The 9 AM brief listed "Figma → Point72 → NVIDIA ×2 → SpaceX" on Sep 3 AND Sep 4.
    Figma and SpaceX were applied on Aug 29. It was reading a hardcoded list written
    into a daily note days earlier.
  • The 7 PM nudge said "top of the list: ⭐ Cohere". Cohere was applied 2026-06-23.
    It was parsing a markdown worklist file last modified Aug 26.

Both now call this instead. It reads the Google Sheet — the actual source of truth for
human fields — so "already applied" is a fact rather than a guess.

Read-only. Returns {} on any failure rather than raising, because a brief with no board
section is fine and a brief that fails to send is not.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SHEET_ID = "1Kkle7QoKsBMXihoslWjIoMDqKFznwxxA4Y_OgiqJpWI"
GOOGLE_API = Path.home() / ".hermes/skills/productivity/google-workspace/scripts/google_api.py"
VENV_PY = Path.home() / ".hermes/hermes-agent/venv/bin/python"
DONE = {"applied", "oa", "phone screen", "onsite", "offer", "rejected",
        "skip", "not a fit", "closed"}
LIVE_PIPELINE = {"oa", "phone screen", "onsite", "offer"}


def _sheet(rng: str) -> list[list]:
    py = str(VENV_PY) if VENV_PY.exists() else sys.executable
    try:
        out = subprocess.run([py, str(GOOGLE_API), "sheets", "get", SHEET_ID, rng],
                             capture_output=True, text=True, timeout=60, input="")
        if out.returncode != 0:
            return []
        return json.loads(out.stdout) or []
    except Exception:  # noqa: BLE001 - best effort by design
        return []


def _rows(rng: str) -> list[dict]:
    raw = _sheet(rng)
    if not raw or len(raw) < 2:
        return []
    hdr = [str(c).strip() for c in raw[0]]
    out = []
    for r in raw[1:]:
        d = {hdr[i]: (r[i] if i < len(r) else "") for i in range(len(hdr))}
        if any(str(v).strip() for v in d.values()):
            out.append(d)
    return out


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except Exception:  # noqa: BLE001
        return default


def board_facts(top_n: int = 8) -> dict:
    queue = _rows("Apply Now!A1:R400")
    apps = _rows("My Applications!A1:K80")
    if not queue and not apps:
        return {}

    # Roles worth surfacing: not already actioned, not disqualified, scored.
    open_roles = []
    for r in queue:
        status = (r.get("Status") or "").strip().lower()
        if status in DONE:
            continue
        fit = r.get("Fit")
        open_roles.append({
            "company": r.get("Company", ""),
            "role": (r.get("Role") or "")[:70],
            "fit": _int(fit, 0) if str(fit).strip().isdigit() else None,
            "hot": _int(r.get("Hot"), 0),
            "cycle": r.get("Cycle", ""),
            "age_days": _int(r.get("Age"), 999),
            "unreadable": str(fit).strip() in ("👀", ""),
        })
    scored = [x for x in open_roles if x["fit"] is not None]
    scored.sort(key=lambda x: (-x["hot"], -(x["fit"] or 0)))

    fresh = [x for x in open_roles if x["age_days"] <= 2]
    fresh.sort(key=lambda x: (-x["hot"], x["age_days"]))

    applied, pipeline = [], []
    for r in apps:
        st = (r.get("Status") or "").strip()
        row = {"company": r.get("Company", ""), "role": (r.get("Role") or "")[:60],
               "status": st, "applied": r.get("Applied", "")}
        if st.lower() in LIVE_PIPELINE:
            pipeline.append(row)
        if st.lower() == "applied":
            applied.append(row)

    def _d(s):
        try:
            return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
        except Exception:  # noqa: BLE001
            return date(1970, 1, 1)

    applied.sort(key=lambda x: _d(x["applied"]), reverse=True)
    cutoff = date.today() - timedelta(days=7)
    applied_7d = [a for a in applied if _d(a["applied"]) >= cutoff]

    return {
        "open_roles_total": len(open_roles),
        "unreadable_count": sum(1 for x in open_roles if x["unreadable"]),
        "top_targets": scored[:top_n],
        "new_last_2_days": fresh[:6],
        "applied_total": len(applied),
        "applied_last_7_days": len(applied_7d),
        "applied_recent": applied[:5],
        "live_pipeline": pipeline,
        "note": ("Already-actioned roles are excluded from top_targets — never suggest "
                 "applying to something in applied_recent or live_pipeline."),
    }


if __name__ == "__main__":
    print(json.dumps(board_facts(), indent=2, ensure_ascii=False))
