"""Validation tests for repo config files.

Catches structural breakage in config/mcp_servers.yaml + config/cron_additions.json
before it reaches the VPS. These files are snippets used in setup; a syntax
error here would make a fresh deploy fail mysteriously.

Run: pytest tests/test_config.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"


def test_mcp_servers_yaml_parses():
    """config/mcp_servers.yaml must be valid YAML."""
    path = CONFIG_DIR / "mcp_servers.yaml"
    assert path.exists(), f"Missing {path}"
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), "Top-level must be a dict"
    assert "mcp_servers" in data, "Must have mcp_servers key"


def test_mcp_servers_has_food_tracker():
    """The food-tracker MCP must be defined with required fields."""
    data = yaml.safe_load((CONFIG_DIR / "mcp_servers.yaml").read_text())
    ft = data["mcp_servers"].get("food-tracker")
    assert ft is not None, "food-tracker MCP missing from config"
    assert ft.get("command") == "node", "food-tracker should run via node"
    assert ft.get("enabled") is True, "food-tracker should be enabled"


def test_mcp_servers_tools_include_allowlist():
    """Architecture-critical: food-tracker must only expose search_food.
    Without this, the LLM cheerfully calls log_food/get_summary/etc. and
    bypasses the vault. We hit this bug — keep it bolted down."""
    data = yaml.safe_load((CONFIG_DIR / "mcp_servers.yaml").read_text())
    ft = data["mcp_servers"]["food-tracker"]
    tools = ft.get("tools", {})
    include = tools.get("include", [])
    assert include == ["search_food"], (
        f"tools.include must be exactly ['search_food'], got {include}. "
        "Allowing other tools breaks the vault-as-canonical architecture."
    )


def test_cron_additions_json_parses():
    """config/cron_additions.json must be valid JSON."""
    path = CONFIG_DIR / "cron_additions.json"
    assert path.exists(), f"Missing {path}"
    data = json.loads(path.read_text())
    assert "jobs_to_append" in data, "Must have jobs_to_append key"


def test_cron_jobs_have_required_fields():
    """Each cron job must have name + schedule + script for the script-mode jobs."""
    data = json.loads((CONFIG_DIR / "cron_additions.json").read_text())
    for job in data["jobs_to_append"]:
        assert "name" in job, f"Job missing name: {job}"
        assert "schedule" in job, f"Job {job['name']} missing schedule"
        assert "script" in job, f"Job {job['name']} missing script"


def test_cron_script_paths_point_to_allowed_root():
    """Hermes' cron dispatcher refuses scripts outside ~/.hermes/scripts/.
    We hit this bug on day 1 (script paths in /home/hermes/sparsh-hermes-agent/...
    got silently blocked). All script paths must now point at the mirrored
    copies under ~/.hermes/scripts/."""
    data = json.loads((CONFIG_DIR / "cron_additions.json").read_text())
    for job in data["jobs_to_append"]:
        script = job["script"]
        assert script.startswith("/home/hermes/.hermes/scripts/"), (
            f"Job {job['name']} script path '{script}' is outside the allowed "
            "root. Hermes' sandbox will block it. Mirror the script via deploy.sh."
        )


def test_no_secret_keys_in_repo_config():
    """Belt-and-suspenders: a real USDA/OpenAI key in the committed snippet
    would leak it to GitHub. Snippets must use placeholders only."""
    yaml_text = (CONFIG_DIR / "mcp_servers.yaml").read_text()
    # USDA keys are 40-char alphanumeric. Match the pattern explicitly.
    import re
    if re.search(r"USDA_API_KEY:\s*['\"]?[A-Za-z0-9]{40}['\"]?", yaml_text):
        # If a real-looking key is present, fail
        raise AssertionError(
            "A 40-char alphanumeric USDA_API_KEY appears in the committed "
            "config snippet. Use a placeholder like '<REPLACE_ON_VPS>' instead."
        )


# ===== SOUL.md (Hermes system prompt) =====
#
# SOUL.md is the highest-leverage piece of text in the system — it loads on
# every Telegram message, ahead of skill selection. A bad SOUL.md (missing
# routing rules, empty file, accidentally truncated) means the LLM falls back
# to its default behavior of "use the cheapest tool" — which is exactly the
# bug class that sent meal logs to ## Notes instead of log-food. These tests
# protect against silent SOUL regressions before deploy.sh ships them to the
# VPS.
#
# We test config/SOUL.public.md — the committed, genericized template that shares
# the real SOUL's structure. The personalized config/SOUL.md is gitignored (kept
# out of the public repo) and scp'd to the VPS by deploy.sh, so it isn't present
# in a clean checkout / CI; the public template is what guards the structure here.

SOUL_REQUIRED_SECTIONS = [
    "## Tone",
    "## Where current state lives",
    "## Hard routing rules",
    "## Persist before acknowledging",
    "## Write scope",
    "## When unsure",
]

SOUL_REQUIRED_ROUTING_TARGETS = [
    "log-food",       # food rule must name the skill
    "log-workout",    # workout rule must name the skill
    "web_search",     # branded/restaurant food rule
    "obsidian-vault-write",  # task/journal rule names the primitive
]


def test_soul_md_exists():
    """config/SOUL.md must exist and be non-empty. deploy.sh copies it to
    ~/.hermes/SOUL.md on every push; if the file is missing or empty, the
    Hermes agent loses its system prompt entirely."""
    path = CONFIG_DIR / "SOUL.public.md"
    assert path.exists(), f"Missing {path}"
    assert path.stat().st_size > 500, (
        f"{path} is suspiciously small ({path.stat().st_size} bytes). "
        "A working SOUL has tone + routing rules + state pointers ≈ 5KB."
    )


def test_soul_md_has_required_sections():
    """SOUL must have all the structural sections we depend on. Missing
    sections = silently weaker routing on every Telegram message."""
    text = (CONFIG_DIR / "SOUL.public.md").read_text(encoding="utf-8")
    missing = [s for s in SOUL_REQUIRED_SECTIONS if s not in text]
    assert not missing, (
        f"SOUL.md missing required sections: {missing}. "
        "These sections are load-bearing for routing behavior."
    )


def test_soul_md_names_specialized_skills_in_routing():
    """The hard routing rules MUST name the specialized skills by their
    actual skill names. Without this, the LLM falls back to obsidian-vault-
    write for everything (the exact bug we built v2 SOUL to fix)."""
    text = (CONFIG_DIR / "SOUL.public.md").read_text(encoding="utf-8")
    missing = [s for s in SOUL_REQUIRED_ROUTING_TARGETS if s not in text]
    assert not missing, (
        f"SOUL.md routing rules missing references to: {missing}. "
        "Hard routing only works if the skill names appear in SOUL."
    )


def test_soul_md_warns_against_notes_section_for_food():
    """A specific anti-pattern we hit multiple times: meal photos / snack
    text getting saved to the daily note's ## Notes section instead of
    going through log-food's MCP + frontmatter pipeline. SOUL must
    explicitly forbid this."""
    text = (CONFIG_DIR / "SOUL.public.md").read_text(encoding="utf-8")
    # Look for both an explicit prohibition AND a mention of ## Notes
    assert "## Notes" in text, "SOUL must reference the ## Notes anti-pattern explicitly"
    assert "NEVER" in text or "Never" in text, (
        "SOUL must have a hard NEVER prohibition on writing food to ## Notes. "
        "The advisory 'avoid X' phrasing doesn't enforce; the LLM bypasses it."
    )


def test_soul_md_forbids_repo_writes():
    """Observed 2026-05-27: Hermes' background curation turn was silently
    editing skills/log-{food,workout}/SKILL.md in the VPS git repo via
    obsidian-vault-write. That drift between repo + VPS broke deploy.sh.
    SOUL must explicitly forbid writes to the repo path."""
    text = (CONFIG_DIR / "SOUL.public.md").read_text(encoding="utf-8")
    assert "sparsh-hermes-agent" in text, (
        "SOUL must name the operational repo path explicitly so the agent "
        "knows what NOT to mutate."
    )
    assert "observations" in text, (
        "SOUL must redirect agent self-learnings to ~/.hermes/memories/observations/ "
        "instead of silently editing skill source."
    )


def test_observations_readme_exists():
    """config/observations-README.md is deployed to
    ~/.hermes/memories/observations/README.md by deploy.sh. It explains the
    contract to the agent on every read of that directory."""
    path = CONFIG_DIR / "observations-README.md"
    assert path.exists(), f"Missing {path}"
    text = path.read_text(encoding="utf-8")
    # Sanity: the README should mention what NOT to edit + where to write instead
    assert "Do NOT edit" in text or "do NOT" in text or "MUST NOT" in text, (
        "Observations README must clearly forbid editing SKILL.md files directly."
    )
