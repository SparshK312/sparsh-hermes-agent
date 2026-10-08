"""role_exclusions.py — role TYPES he has ruled Not a Fit, for anything that SUGGESTS a role.

Dependency-free on purpose (like status_vocab.py): board_facts runs under /usr/bin/python3
from the coach crons, which cannot import hotness / internship_scraper.

Source: the role types he has ruled Not a Fit: data science / analyst lanes and
firmware / embedded. This is NOT hotness's lane
classifier (_DATA_KEYWORDS there includes data ENGINEERING, which he has not excluded).

WHY (2026-10-08): the 7 PM "apply to something" nudge and the morning brief both took
board_facts' newest role, which was a "Data Analyst Intern" with no fit score.
"""
from __future__ import annotations

import re

NOT_A_FIT_TITLE = re.compile(
    r"\bdata\s+scien|\bdata\s+analy(?!tics\s+engineer)|\bbusiness\s+(?:data\s+)?analy|\banalytics\s+analyst\b"
    r"|\bfirmware\b|\bembedded\b",
    re.I)


def excluded(title: str) -> bool:
    """True when a role title is a type he has ruled out (never suggest it)."""
    return bool(NOT_A_FIT_TITLE.search(title or ""))
