"""Cron wrappers must exit with their job's status (Nov 2026 audit: six of them logged
FAILED and exited 0, so the scheduler recorded every failed run as "ok")."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# wrapper, path (under a fake HOME) of the program it runs, is that program the interpreter itself?
CASES = [
    ("scripts/internship/run_curate_vps.sh", ".hermes/hermes-agent/venv/bin/python", True),
    ("scripts/internship/run_revive_dead.sh", ".hermes/hermes-agent/venv/bin/python", True),
    ("scripts/internship/run_coverage_digest.sh", ".hermes/hermes-agent/venv/bin/python", True),
    ("scripts/cron/fitness_report.sh", ".hermes/venvs/fitness/bin/python3", True),
    ("scripts/cron/cost_monitor.sh", ".hermes/scripts/monitor/cost_monitor.py", False),
    ("scripts/cron/hae_sync.sh", ".hermes/scripts/hae_process.py", False),
]


def _run(tmp_path, wrapper, target, is_interp, code):
    home = tmp_path / f"home{code}"
    t = home / target
    t.parent.mkdir(parents=True)
    (home / ".hermes" / "scripts" / "internship").mkdir(parents=True, exist_ok=True)
    if is_interp:
        t.write_text(f"#!/bin/sh\nexit {code}\n")
        t.chmod(0o755)
    else:
        t.write_text(f"import sys\nsys.exit({code})\n")
        if "hae_process" in target:                       # the second step of hae_sync succeeds
            (t.parent / "hae_daily_ingest.py").write_text("import sys\nsys.exit(0)\n")
    return subprocess.run(["bash", str(ROOT / wrapper)], capture_output=True, text=True,
                          env={"HOME": str(home), "PATH": os.environ["PATH"], "HERMES_VAULT": str(tmp_path / "v")})


@pytest.mark.parametrize("wrapper,target,is_interp", CASES)
def test_wrapper_passes_a_failure_through_and_succeeds_when_the_job_does(tmp_path, wrapper, target, is_interp):
    if wrapper.startswith("scripts/internship/") and not (ROOT / wrapper).exists():
        pytest.skip("the job-board engine is not part of this copy of the repository")
    bad = _run(tmp_path, wrapper, target, is_interp, 3)
    assert bad.returncode != 0, f"{wrapper} reported success for a job that exited 3"
    assert '{"wakeAgent": false}' not in bad.stdout          # a failed run must not also say "stay silent, all fine"
    good = _run(tmp_path, wrapper, target, is_interp, 0)
    assert good.returncode == 0, good.stderr
