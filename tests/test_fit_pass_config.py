"""Config-consistency guards for fit_pass.

These exist because of a real bug shipped on 2026-08-20. The OpenRouter migration
changed FIT_MODEL from "gpt-5.4-mini" to "openai/gpt-5.4-mini" and did NOT update
the two lookup collections keyed by the old spelling:

  * `_TEMPERATURE_OK` — a miss flips `is_reasoning` True, which DROPS
    `temperature: 0` and bumps max_tokens 1500 -> 8000. Determinism at temp 0 is
    the exact property the 2026-06-22 eval certified ("raw mini was flaky";
    7/7 stable pass, 14/14 recall), and every job description is scored through it.
  * `PRICE` — a miss silently falls back to a default and mis-states the cost log.

Neither failed loudly. Nothing in the suite covered them, so the full 185-test run
passed with the classifier's determinism guarantee quietly gone. These tests bind
FIT_MODEL to its own lookup tables so the next rename can't repeat it.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "internship"
sys.path.insert(0, str(SCRIPTS))

fit_pass = pytest.importorskip("fit_pass", reason="fit_pass imports are optional in CI")


def test_fit_model_accepts_temperature_zero():
    """FIT_MODEL must be in _TEMPERATURE_OK, or temperature=0 is silently dropped."""
    assert fit_pass.FIT_MODEL in fit_pass._TEMPERATURE_OK, (
        f"FIT_MODEL={fit_pass.FIT_MODEL!r} missing from _TEMPERATURE_OK. "
        "is_reasoning would flip True and drop `temperature: 0` — the property "
        "the fit-rubric eval certified."
    )


def test_fit_model_is_not_treated_as_reasoning():
    """Reproduce the exact branch in _call_model rather than trusting the set."""
    model = fit_pass.FIT_MODEL
    is_claude = model.startswith("claude")
    is_reasoning = (not is_claude) and (model not in fit_pass._TEMPERATURE_OK)
    assert is_reasoning is False, (
        f"{model!r} classifies as a reasoning model; temperature=0 will not be sent."
    )


def test_fit_model_has_a_price_entry():
    """A missing PRICE key falls back to a default and mis-states the cost log."""
    assert fit_pass.PRICE.get(fit_pass.FIT_MODEL) is not None, (
        f"No PRICE entry for {fit_pass.FIT_MODEL!r}; cost logging silently degrades."
    )


def test_openai_callers_go_through_openrouter():
    """The direct OpenAI key was revoked 2026-08-20 — nothing may call OpenAI directly."""
    assert "openrouter.ai" in fit_pass.OPENAI_URL, (
        f"OPENAI_URL={fit_pass.OPENAI_URL!r} is not OpenRouter; the direct "
        "OpenAI key is revoked and this path would 401."
    )


@pytest.mark.parametrize("name", ["fit_pass.py", "internship_triage.py"])
def test_no_direct_openai_endpoint_remains(name):
    """Repo-wide guard: the revoked key's endpoint must not reappear."""
    src = (SCRIPTS / name).read_text()
    assert "api.openai.com" not in src, f"{name} still references api.openai.com"
    assert "OPENAI_API_KEY" not in src, f"{name} still reads the revoked OPENAI_API_KEY"
