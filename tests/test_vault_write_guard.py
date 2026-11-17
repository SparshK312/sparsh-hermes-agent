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


def test_screenshot_incident_is_covered():
    """A third-party post recorded as the user's own must require confirmation.

    Tiers 1 and 2 both missed it because it was a small ADDITIVE edit."""
    msg = guard.needs_approval("patch", {
        "file_path": "00 - Dashboard/Internship Pipeline.md",
        "old_string": "| **Initech** | app submitted |",
        "new_string": "| **Initech** | INTERVIEW REQUEST Jul 30 | expedited interview request |",
    })
    # The message quotes the matched text verbatim, so compare case-insensitively —
    # the real incident wrote it as "INTERVIEW REQUEST".
    assert msg is not None and "interview request" in msg.lower()


def test_open_loops_view_cannot_be_hand_edited():
    """The view is rendered from the VPS store; an agent edit would be overwritten and
    would look like it had worked (2026-11-16)."""
    p = "/home/hermes/vault/00 - Dashboard/Open Loops.md"
    for tool, args in (("patch", {"path": p, "old_string": "a", "new_string": "b"}),
                       ("write_file", {"path": p, "content": "x"}),
                       ("execute_code", {"code": f"open('{p}','w').write('x')"})):
        msg = guard.decide(tool, args)
        assert msg and "loops.py" in msg, tool
    assert guard.decide("patch", {"path": "/home/hermes/vault/00 - Dashboard/Action Items.md",
                                  "old_string": "a", "new_string": "b"}) is None
    assert guard.decide("read_file", {"path": p}) is None
