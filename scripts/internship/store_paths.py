#!/usr/bin/env python3
"""store_paths.py — the ONE definition of where the curated board's files live.

🔴 WHY THIS FILE EXISTS (added 2026-09-05, after it cost a day of blank rows).

Every script in this directory used to resolve the store path for itself, and they
disagreed with each other. The 2026-09-04 migration moved the LIVE store out of the
vault to ``~/.hermes/internship/`` — it has to live outside, because ``.json`` does
not sync through Obsidian, so a copy inside the vault diverges silently. add_jd.py
and jd_backfill.py were updated to match. curate.py, fit_pass.py, revive_dead.py and
worklist.py were NOT: they kept defaulting to the vault path, and run_curate_vps.sh
papered over curate.py by exporting ``CURATED_STORE`` before calling it.

That covered the cron and nothing else. A bare ``python curate.py`` on the VPS loaded
the stale 767-entry vault copy instead of the live 1,471-entry store. The refresh then
adopts every Sheet row it does not recognise as an "orphan" (curate.py step 1), so
~700 live rows were rewritten as entries holding a status and NOTHING else — no
company, no role, no URL. Those render as BLANK ROWS, and they were saved straight
back into the store, so they returned on every later run. It blanked real applications
too: the Shopify Offer row and three submitted Tesla roles kept their status and lost
their identity.

The lesson is not "remember to export the env var." It is that **a default which is
wrong on the machine the code actually runs on is a loaded gun**, and that five copies
of a path is five chances to migrate four of them. One definition, imported everywhere.

Resolution order (identical for every caller):
  1. ``$CURATED_STORE`` / ``$CURATED_XLSX`` — an explicit override always wins.
  2. On the VPS: ``~/.hermes/internship/``. Unconditional — NOT contingent on the file
     already existing, so a fresh VPS creates the store in the right place instead of
     silently falling through to the vault.
  3. On the Mac: the vault copy, with a loud warning. It has been historical since
     2026-09-04 and the refresh no longer reads it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["on_vps", "vault_root", "store_path", "xlsx_path", "describe"]

_STORE_NAME = "curated_postings.json"
_XLSX_NAME = "Curated Board.xlsx"

_warned = False


def on_vps() -> bool:
    """True on the Hermes VPS.

    Two independent markers, because either one alone has a failure mode: the vault
    directory can be absent mid-rebuild before Obsidian Sync has run, and
    ~/.hermes/internship/ does not exist until the first refresh writes it.
    """
    return Path("/home/hermes/vault").exists() or (Path.home() / ".hermes" / "internship").is_dir()


def vault_root() -> Path:
    return Path(os.environ.get("HERMES_VAULT")
                or ("/home/hermes/vault" if Path("/home/hermes/vault").exists()
                    else str(Path.home() / "Documents" / "School Vault - UofT")))


def _warn_mac_copy() -> None:
    global _warned
    if _warned:
        return
    _warned = True
    print("⚠️  WARNING: the live curated store is on the VPS "
          "(~/.hermes/internship/curated_postings.json).\n"
          "    This Mac vault copy has been HISTORICAL since 2026-09-04 and the "
          "refresh no longer reads it.\n"
          "    Run this on the VPS instead, or set CURATED_STORE explicitly.",
          file=sys.stderr)


def _resolve(env_var: str, filename: str, warn: bool) -> Path:
    env = os.environ.get(env_var)
    if env:
        return Path(env)
    if on_vps():
        return Path.home() / ".hermes" / "internship" / filename
    if warn:
        _warn_mac_copy()
    return vault_root() / "06 - Internships" / "Job Search" / filename


def store_path(warn: bool = True) -> Path:
    return _resolve("CURATED_STORE", _STORE_NAME, warn)


def xlsx_path(warn: bool = False) -> Path:
    # No warning for the xlsx: it owns nothing, and a stale one is an inconvenience
    # rather than the data-loss the store copy is.
    return _resolve("CURATED_XLSX", _XLSX_NAME, warn)


def describe() -> str:
    return (f"host={'vps' if on_vps() else 'mac'}  store={store_path(warn=False)}  "
            f"xlsx={xlsx_path()}")


if __name__ == "__main__":
    print(describe())
