"""Pytest config — discovery happens automatically from tests/test_*.py."""


import pytest


@pytest.fixture(autouse=True)
def _isolate_assistant_state(tmp_path_factory, monkeypatch):
    """Never let a test touch the real ~/.hermes/assistant (the send ledger and the spend
    record). Several modules record a send as a side effect of an otherwise stubbed path."""
    monkeypatch.setenv("HERMES_ASSISTANT_DIR", str(tmp_path_factory.mktemp("assistant")))
