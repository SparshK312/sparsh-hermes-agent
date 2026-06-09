"""Shell + Python script validation tests.

Verifies all scripts in scripts/ parse cleanly before they hit the VPS.
Bash syntax errors in cron scripts would surface as silent failures (cron
fires, script errors, you get a Telegram error message and no nudge).
Python errors in setup/patch scripts would fail at deploy time.

Run: pytest tests/test_scripts.py -v
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _all_shell_scripts() -> list[Path]:
    """All .sh files in scripts/ (recursive)."""
    return sorted(SCRIPTS_DIR.rglob("*.sh"))


def _all_python_scripts() -> list[Path]:
    """All .py files in scripts/ (recursive)."""
    return sorted(SCRIPTS_DIR.rglob("*.py"))


@pytest.mark.parametrize("script", _all_shell_scripts(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_shell_syntax(script: Path):
    """`bash -n` parses the script without executing. Catches syntax errors,
    unclosed quotes, malformed heredocs, etc."""
    result = subprocess.run(
        ["bash", "-n", str(script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash syntax error in {script}:\n{result.stderr}"
    )


@pytest.mark.parametrize("script", _all_python_scripts(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_python_compiles(script: Path):
    """py_compile catches syntax errors without executing the script."""
    import py_compile
    try:
        py_compile.compile(str(script), doraise=True)
    except py_compile.PyCompileError as e:
        pytest.fail(f"Python compile error in {script}:\n{e}")


def test_deploy_script_is_executable():
    """scripts/deploy.sh must be executable for `./scripts/deploy.sh` to work."""
    deploy = SCRIPTS_DIR / "deploy.sh"
    assert deploy.exists(), "scripts/deploy.sh missing"
    assert deploy.stat().st_mode & 0o111, (
        "scripts/deploy.sh not executable. Run: chmod +x scripts/deploy.sh"
    )


def test_cron_scripts_have_shebang():
    """Each cron script needs #!/usr/bin/env bash so Hermes can exec it."""
    for script in (SCRIPTS_DIR / "cron").glob("*.sh"):
        first_line = script.read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith("#!"), (
            f"{script} missing shebang on first line: {first_line!r}"
        )


def test_deploy_sh_ships_soul_md():
    """deploy.sh must copy config/SOUL.md → ~/.hermes/SOUL.md on every push.
    Without this step, edits to the repo SOUL never reach the live agent and
    the SOUL would drift between repo + VPS silently."""
    deploy = (SCRIPTS_DIR / "deploy.sh").read_text(encoding="utf-8")
    # The cp invocation must be present
    assert "cp config/SOUL.md ~/.hermes/SOUL.md" in deploy, (
        "deploy.sh missing the SOUL.md deploy step. SOUL edits to the repo "
        "won't reach the live Hermes agent."
    )
    # And the rolling backup before overwrite
    assert "SOUL.md.bak" in deploy, (
        "deploy.sh missing the SOUL.md backup step. A bad SOUL deploy "
        "should be one-command rollback-able via ~/.hermes/SOUL.md.bak."
    )


def test_deploy_sh_sets_up_observations_dir():
    """deploy.sh must ensure the agent's self-observation directory exists
    with its README. Without this, the SOUL 'write to observations' rule
    points at a nonexistent path on first run, defeating the redirect."""
    deploy = (SCRIPTS_DIR / "deploy.sh").read_text(encoding="utf-8")
    assert "mkdir -p ~/.hermes/memories/observations" in deploy, (
        "deploy.sh missing the observations dir setup. SOUL redirects "
        "agent self-edits there; the dir must exist or the redirect fails."
    )
    assert "observations-README.md" in deploy, (
        "deploy.sh must ship the observations README so the agent reads "
        "the contract on every interaction with that directory."
    )


def test_voice_wrapper_patch_check_mode():
    """The voice-wrapper patcher's --check mode must not modify anything and
    should handle the no-Hermes-install case gracefully (for CI)."""
    patcher = SCRIPTS_DIR / "patch" / "voice_wrapper_patch.py"
    assert patcher.exists(), "voice_wrapper_patch.py missing"
    result = subprocess.run(
        ["python3", str(patcher), "--check"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "HERMES_GATEWAY_RUN": "/tmp/nonexistent-gateway-run.py"},
    )
    assert result.returncode in (0, 1, 2), (
        f"voice_wrapper_patch --check exited with unexpected code {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_image_routing_patch_check_mode():
    """Same contract for the image-routing patcher."""
    patcher = SCRIPTS_DIR / "patch" / "image_routing_patch.py"
    assert patcher.exists(), "image_routing_patch.py missing"
    result = subprocess.run(
        ["python3", str(patcher), "--check"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "HERMES_IMAGE_ROUTING": "/tmp/nonexistent-image-routing.py"},
    )
    assert result.returncode in (0, 1, 2), (
        f"image_routing_patch --check exited with unexpected code {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_image_routing_patch_roundtrip_on_fixture(tmp_path):
    """Patcher must round-trip cleanly on a synthesized fixture: unpatched
    → patch → patched → revert → unpatched. Catches any regression in the
    text-substitution logic."""
    import os
    patcher = SCRIPTS_DIR / "patch" / "image_routing_patch.py"
    fixture = tmp_path / "image_routing.py"
    fixture.write_text(
        '# minimal fixture mimicking the upstream block we patch\n'
        'def build_native_content_parts(image_paths, user_text):\n'
        '    text = (user_text or "").strip()\n'
        '    if image_paths:\n'
        '        base_text = text or "What do you see in this image?"\n'
        '        return base_text\n'
    )
    env = {**os.environ, "HERMES_IMAGE_ROUTING": str(fixture)}

    # initial: unpatched
    r = subprocess.run(["python3", str(patcher), "--check"], capture_output=True, text=True, env=env)
    assert "state:  unpatched" in r.stdout, r.stdout

    # apply
    r = subprocess.run(["python3", str(patcher)], capture_output=True, text=True, env=env)
    assert "PATCHED" in r.stdout, r.stdout

    # now: patched
    r = subprocess.run(["python3", str(patcher), "--check"], capture_output=True, text=True, env=env)
    assert "state:  patched" in r.stdout, r.stdout

    # second apply: no-op
    r = subprocess.run(["python3", str(patcher)], capture_output=True, text=True, env=env)
    assert "already patched" in r.stdout, r.stdout

    # revert
    r = subprocess.run(["python3", str(patcher), "--revert"], capture_output=True, text=True, env=env)
    assert "REVERTED" in r.stdout, r.stdout

    # back to unpatched
    r = subprocess.run(["python3", str(patcher), "--check"], capture_output=True, text=True, env=env)
    assert "state:  unpatched" in r.stdout, r.stdout


def test_voice_wrapper_patch_roundtrip_on_fixture(tmp_path):
    """Same round-trip test for voice-wrapper patcher (didn't exist before;
    adding now while we're here)."""
    import os
    patcher = SCRIPTS_DIR / "patch" / "voice_wrapper_patch.py"
    fixture = tmp_path / "run.py"
    fixture.write_text(
        '# minimal fixture mimicking the upstream wrapper block\n'
        'if True:\n'
        '    if True:\n'
        '        if True:\n'
        '            if True:\n'
        '                if True:\n'
        '                    enriched_parts.append(\n'
        '                        f\'[The user sent a voice message~ \'\n'
        '                        f\'Here\\\'s what they said: "{transcript}"]\'\n'
        '                    )\n'
    )
    env = {**os.environ, "HERMES_GATEWAY_RUN": str(fixture)}

    r = subprocess.run(["python3", str(patcher), "--check"], capture_output=True, text=True, env=env)
    assert "state:  unpatched" in r.stdout, r.stdout

    r = subprocess.run(["python3", str(patcher)], capture_output=True, text=True, env=env)
    assert "PATCHED" in r.stdout, r.stdout

    r = subprocess.run(["python3", str(patcher), "--check"], capture_output=True, text=True, env=env)
    assert "state:  patched" in r.stdout, r.stdout

    r = subprocess.run(["python3", str(patcher), "--revert"], capture_output=True, text=True, env=env)
    assert "REVERTED" in r.stdout, r.stdout

    r = subprocess.run(["python3", str(patcher), "--check"], capture_output=True, text=True, env=env)
    assert "state:  unpatched" in r.stdout, r.stdout
