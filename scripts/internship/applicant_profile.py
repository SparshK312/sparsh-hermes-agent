#!/usr/bin/env python3
"""applicant_profile.py — load the applicant's screening profile from a LOCAL,
gitignored file so personal details (name, residency/citizenship status, exact
targets) never live in this public repo.

The real profile lives at ~/.hermes/internship_profile.txt (gitignored, deployed
like SOUL.md — kept on the Mac and scp'd to the VPS, never committed). If the file
is absent, a generic fallback is returned so screening still runs (just less
tailored). Override the path with INTERNSHIP_PROFILE_FILE for testing.

Used by fit_pass.py (and intended for internship_triage.py) so the eligibility
language is sourced at runtime instead of hardcoded in code that gets pushed.
"""
from __future__ import annotations

import os
from pathlib import Path

_PROFILE_FILE = Path(
    os.environ.get("INTERNSHIP_PROFILE_FILE")
    or str(Path.home() / ".hermes" / "internship_profile.txt")
)

# Generic, non-identifying fallback — safe to be public.
_FALLBACK = (
    "a university student targeting SWE / AI-ML / Data intern or co-op roles in the "
    "US or Canada for the upcoming cycles, work-authorized in both countries, who "
    "cares about company brand."
)


def load_profile() -> str:
    """Return the applicant profile string (gitignored file, else generic fallback)."""
    try:
        txt = _PROFILE_FILE.read_text(encoding="utf-8").strip()
        if txt:
            return txt
    except Exception:  # noqa: BLE001 — missing/unreadable file → fallback
        pass
    return _FALLBACK


if __name__ == "__main__":
    print(load_profile())
