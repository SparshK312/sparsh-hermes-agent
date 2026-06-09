"""Validation tests for every SKILL.md in the repo.

Catches the bug class we hit on day 1: YAML colons in unquoted descriptions
made 3 skills silently fail to load on Hermes (no error, just missing from
the skill registry). These tests would have flagged that pre-deploy.

Run: pytest tests/test_skills.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

# Fields Hermes' skill loader expects in the YAML frontmatter.
REQUIRED_FIELDS = {"name", "description", "version", "platforms"}

# Optional but conventional fields we use across our skills.
OPTIONAL_FIELDS = {"metadata"}


def _all_skills() -> list[Path]:
    if not SKILLS_DIR.exists():
        return []
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def _parse_frontmatter(skill_path: Path) -> dict:
    """Extract the YAML frontmatter from a SKILL.md file."""
    text = skill_path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"No YAML frontmatter found in {skill_path}")
    return yaml.safe_load(parts[1])


@pytest.mark.parametrize("skill_path", _all_skills(), ids=lambda p: p.parent.name)
class TestSkillFrontmatter:
    """Each SKILL.md must have valid, well-formed YAML frontmatter."""

    def test_yaml_parses(self, skill_path: Path):
        """Frontmatter must be valid YAML. This catches the colon-in-unquoted-
        description bug that broke 3 of our skills on initial deploy."""
        try:
            fm = _parse_frontmatter(skill_path)
        except yaml.YAMLError as e:
            pytest.fail(f"YAML parse error in {skill_path}: {e}")
        assert isinstance(fm, dict), f"Frontmatter is not a dict in {skill_path}"

    def test_required_fields_present(self, skill_path: Path):
        """Hermes' skill loader requires name/description/version/platforms."""
        fm = _parse_frontmatter(skill_path)
        missing = REQUIRED_FIELDS - set(fm.keys())
        assert not missing, f"Missing required fields in {skill_path}: {missing}"

    def test_name_matches_directory(self, skill_path: Path):
        """The frontmatter `name` should match the parent dir name."""
        fm = _parse_frontmatter(skill_path)
        assert fm["name"] == skill_path.parent.name, (
            f"name '{fm['name']}' != directory '{skill_path.parent.name}'"
        )

    def test_platforms_includes_linux(self, skill_path: Path):
        """We deploy on Linux (Hetzner VPS). Skills missing 'linux' in
        platforms get silently excluded from the registry on the VPS.
        We hit this bug — obsidian-vault-write originally listed only [macos]."""
        fm = _parse_frontmatter(skill_path)
        platforms = fm.get("platforms", [])
        assert "linux" in platforms, (
            f"{skill_path.parent.name} platforms={platforms} missing 'linux'"
        )

    def test_description_not_empty(self, skill_path: Path):
        """Empty descriptions make NL routing useless — Hermes' agent uses
        the description to decide which skill to invoke."""
        fm = _parse_frontmatter(skill_path)
        desc = fm.get("description", "").strip()
        assert len(desc) >= 50, (
            f"Description too short ({len(desc)} chars) in {skill_path}. "
            "Routing needs enough signal to disambiguate."
        )

    def test_version_is_semver_ish(self, skill_path: Path):
        """Loose semver check (X.Y.Z). Hermes doesn't enforce but it's good
        hygiene for tracking skill iterations."""
        fm = _parse_frontmatter(skill_path)
        version = str(fm.get("version", ""))
        assert re.match(r"^\d+\.\d+\.\d+$", version), (
            f"Version '{version}' in {skill_path} isn't semver (X.Y.Z)"
        )


def test_at_least_some_skills_exist():
    """Sanity: we have skills in the repo at all."""
    skills = _all_skills()
    assert len(skills) >= 5, f"Found only {len(skills)} skills; expected ≥5."
