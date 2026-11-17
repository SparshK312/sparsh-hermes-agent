"""Well-formedness check for every SKILL.md in the repo.

Catches the real failure: a malformed SKILL.md (bad YAML, missing field, no
'linux' platform, bad semver) silently drops the skill from Hermes' registry on
the VPS — no error, just a missing skill. We hit exactly this on day 1 (a colon
in an unquoted description broke 3 skills; obsidian-vault-write once listed only
[macos]). This is metadata hygiene, not behavior — ONE consolidated check per
skill (it used to be 6 separate parametrized assertions per skill, which inflated
the test count without adding coverage).

Run: pytest tests/test_skills.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
REQUIRED_FIELDS = {"name", "description", "version", "platforms"}


def _all_skills() -> list[Path]:
    if not SKILLS_DIR.exists():
        return []
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def _parse_frontmatter(skill_path: Path) -> dict:
    text = skill_path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("no YAML frontmatter")
    return yaml.safe_load(parts[1])


@pytest.mark.parametrize("skill_path", _all_skills(), ids=lambda p: p.parent.name)
def test_skill_frontmatter_valid(skill_path: Path):
    """One thorough check per skill; collects ALL problems so the failure message
    pinpoints them. A failure here = the skill would silently fail to load."""
    name = skill_path.parent.name
    try:
        fm = _parse_frontmatter(skill_path)
    except (yaml.YAMLError, ValueError) as e:
        pytest.fail(f"{name}: frontmatter won't parse ({e}) — would break skill loading")
    if not isinstance(fm, dict):
        pytest.fail(f"{name}: frontmatter is not a mapping")

    problems = []
    missing = REQUIRED_FIELDS - set(fm.keys())
    if missing:
        problems.append(f"missing required fields {sorted(missing)}")
    if fm.get("name") != name:
        problems.append(f"name '{fm.get('name')}' != directory '{name}'")
    if "linux" not in (fm.get("platforms") or []):
        problems.append(f"platforms {fm.get('platforms')} missing 'linux' (excluded on the VPS)")
    if len((fm.get("description") or "").strip()) < 50:
        problems.append("description <50 chars (too weak for NL routing)")
    if not re.match(r"^\d+\.\d+\.\d+$", str(fm.get("version", ""))):
        problems.append(f"version '{fm.get('version')}' not semver X.Y.Z")
    assert not problems, f"{name} SKILL.md: " + "; ".join(problems)


def test_at_least_some_skills_exist():
    """Sanity: skills are present at all."""
    assert len(_all_skills()) >= 5
