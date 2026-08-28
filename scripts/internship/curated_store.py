#!/usr/bin/env python3
"""
curated_store.py — the canonical store behind the living board.

curated_postings.json is the source of truth for MACHINE fields; the xlsx is the
source of truth for HUMAN fields between refreshes. They're merged by canonical_id
on every refresh (see build_curated_xlsx.py round-trip). This module owns the JSON:
load, upsert-machine (never touches human), set-human (from the xlsx read-back),
and atomic save.

Schema:
{
  "version": 1,
  "generated_at": "ISO",
  "postings": {
     "<canonical_id>": {
        "machine": { company, role, location, url, ats_type, source, cycle, lane,
                     posted_date, age_days, full_jd, tier, brand, role_score,
                     recency, hotness, fresh, dead, fail_count, first_seen, last_seen },
        "human":   { status, applied_date, notes, priority_override }
     }, ...
  }
}
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1

HUMAN_FIELDS = ("status", "applied_date", "notes", "priority_override")
_EMPTY_HUMAN = {k: "" for k in HUMAN_FIELDS}


class CuratedStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"version": SCHEMA_VERSION, "generated_at": "", "postings": {}}

    # ── load / save ──────────────────────────────────────────────────────────
    def load(self) -> "CuratedStore":
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        self.data.setdefault("postings", {})
        self.data.setdefault("version", SCHEMA_VERSION)
        return self

    def save(self, generated_at: str) -> None:
        """Atomic write: temp file in the same dir + os.replace (crash-safe)."""
        self.data["generated_at"] = generated_at
        self.data["version"] = SCHEMA_VERSION
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # ── access ───────────────────────────────────────────────────────────────
    @property
    def postings(self) -> dict:
        return self.data["postings"]

    def get(self, cid: str) -> dict | None:
        return self.postings.get(cid)

    def entry(self, cid: str) -> dict:
        """Get-or-create the {machine, human} envelope for an id."""
        e = self.postings.get(cid)
        if e is None:
            e = {"machine": {}, "human": dict(_EMPTY_HUMAN)}
            self.postings[cid] = e
        e.setdefault("machine", {})
        h = e.setdefault("human", {})
        for k in HUMAN_FIELDS:
            h.setdefault(k, "")
        return e

    # ── mutation ─────────────────────────────────────────────────────────────
    # Fields where an empty harvest result must never clobber something we already
    # hold. A blank here means "this fetch did not get it", not "this value is gone".
    _NEVER_BLANK = ("full_jd", "role", "title", "location", "url", "posted_date",
                    "cycle", "company")

    def upsert_machine(self, cid: str, fields: dict) -> None:
        """Merge machine fields. NEVER touches human fields.

        🔴 A blind dict.update() here was silently destroying data (found 2026-08-28).
        Tesla answers automation with 403, so every refresh called fetch_jd_record,
        got an empty record back, and wrote `full_jd: ""` straight over a job
        description that had been pasted in by hand minutes earlier. The same bug
        means ANY transient fetch failure erases a good JD — which is a large part of
        why rows sat unscoreable in the first place.

        Rule: a blank never overwrites a non-blank, and a hand-pasted JD outranks any
        scraper result outright.
        """
        m = self.entry(cid)["machine"]
        manual_jd = m.get("jd_source") == "manual-paste"
        for k, v in fields.items():
            blank = v is None or (isinstance(v, str) and not v.strip())
            if blank and k in self._NEVER_BLANK and m.get(k):
                continue
            if k == "full_jd" and manual_jd:
                # only replace a hand-pasted JD with something at least as substantial
                if len(str(v or "")) < len(str(m.get("full_jd") or "")):
                    continue
            m[k] = v

    def set_human(self, cid: str, fields: dict) -> None:
        """Set human fields (from xlsx read-back). Only known keys; blanks ignored
        so a cleared cell doesn't wipe a value unless explicitly empty-string."""
        h = self.entry(cid)["human"]
        for k in HUMAN_FIELDS:
            if k in fields and fields[k] is not None:
                h[k] = str(fields[k]).strip()

    def add_orphan(self, cid: str, human: dict, label: str = "manual-add") -> None:
        """A row the user hand-added to the xlsx (unknown id) — preserve it."""
        e = self.entry(cid)
        e["machine"].setdefault("source", label)
        e["machine"].setdefault("orphan", True)
        self.set_human(cid, human)

    def items(self):
        return self.postings.items()

    def __len__(self):
        return len(self.postings)


# ── helpers ───────────────────────────────────────────────────────────────────
def is_actioned(entry: dict) -> bool:
    """True if the user has moved this posting out of the 'To Apply' churn zone."""
    status = (entry.get("human", {}).get("status") or "").strip().lower()
    return status not in ("", "to apply")
