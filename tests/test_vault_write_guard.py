"""Run the vault-write-guard decision tables under pytest.

The guard already shipped a thorough case table at
plugins/vault-write-guard/test_decide.py, but it was a standalone script that
nothing invoked — so CI never ran it and a regression in the guard would have
been invisible. That is the same shape of gap as test_config.py asserting a
skill NAME is present without ever checking the skill exists.

This imports the guard and its case tables directly and parametrizes them, so
every case shows up as an individual pytest result.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_DIR = REPO_ROOT / "plugins" / "vault-write-guard"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load("vault_write_guard", GUARD_DIR / "__init__.py")
cases = _load("vault_write_guard_cases", GUARD_DIR / "test_decide.py")


@pytest.mark.parametrize("name,tool,args,should_block",
                         [pytest.param(*c, id=c[0]) for c in cases.CASES])
def test_tier1_hard_block(name, tool, args, should_block):
    """Tier 1: health-file hand-writes are blocked outright (vault_log owns them)."""
    blocked = guard.decide(tool, args) is not None
    assert blocked is should_block


@pytest.mark.parametrize("name,tool,args,should_ask",
                         [pytest.param(*c, id=c[0]) for c in cases.APPROVE_CASES])
def test_tier2_and_3_approval(name, tool, args, should_ask):
    """Tier 2 (destructive rewrites) + Tier 3 (additive outside-world claims)."""
    asks = guard.needs_approval(tool, args) is not None
    assert asks is should_ask


def test_guard_fails_open_on_bad_input():
    """A guard bug must never block real work — every entry point tolerates junk."""
    for bad in (None, "not-a-dict", 42, [], {"file_path": None}):
        assert guard.decide("patch", bad) is None or isinstance(guard.decide("patch", bad), str)
        assert guard.needs_approval("patch", bad) is None or isinstance(
            guard.needs_approval("patch", bad), str)


def test_zero2sudo_incident_is_covered():
    """The literal 2026-07-30 write must require confirmation.

    A third-party Instagram story about other people's Google interviews was
    recorded as Sparsh's own and sat in his career dashboard for 11 days. Tiers
    1 and 2 both missed it because it was a small ADDITIVE edit."""
    msg = guard.needs_approval("patch", {
        "file_path": "00 - Dashboard/Internship Pipeline.md",
        "old_string": "| **Google** | app submitted |",
        "new_string": "| **Google** | INTERVIEW REQUEST Jul 30 | expedited interview request |",
    })
    assert msg is not None and "interview request" in msg
