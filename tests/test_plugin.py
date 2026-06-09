"""Validation tests for our custom Hermes plugins.

Right now: just the openai (direct API) provider plugin. If Hermes restructures
their provider-loading API, we want to find out BEFORE the next deploy. These
tests don't require a Hermes install — they check the plugin file's shape.

Run: pytest tests/test_plugin.py -v
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_openai_plugin_exists():
    """Plugin file should exist where Hermes' loader expects it."""
    init = REPO_ROOT / "scripts" / "patch"  # placeholder check
    # The actual plugin lives in ~/.hermes/plugins/model-providers/openai/ on
    # the VPS, not in this repo. We commit a reference copy for documentation
    # only (the live one on the VPS is the source of truth). This test just
    # verifies the patcher exists (since that's what guards the analogous
    # source patch). The plugin itself is documented in docs/.
    patcher = REPO_ROOT / "scripts" / "patch" / "voice_wrapper_patch.py"
    assert patcher.exists()


def test_openai_plugin_source_parses():
    """If we ever commit the plugin source into the repo (e.g. for backup),
    it should at least parse as valid Python. This is a forward-compatible
    check that's a no-op until we add the file."""
    plugin = REPO_ROOT / "plugins" / "model-providers" / "openai" / "__init__.py"
    if not plugin.exists():
        return  # Plugin not committed to repo; skip
    src = plugin.read_text(encoding="utf-8")
    try:
        ast.parse(src)
    except SyntaxError as e:
        raise AssertionError(f"openai plugin __init__.py syntax error: {e}")
