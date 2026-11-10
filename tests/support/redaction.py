"""Every test starts with no secret registered with `kraft.redact`, outside an agent's shell."""

import pytest

from kraft import redact


@pytest.fixture(autouse=True)
def _no_registered_secrets(monkeypatch):
    """One a test (or code it ran) registered would make every later log
    record in the worker drop its `exc_info`, which tests read."""
    monkeypatch.setattr(redact, "_secrets", ())


@pytest.fixture(autouse=True)
def _not_inside_an_agents_shell(monkeypatch):
    """`kraft` tags a call from a coding agent's shell (`CLAUDECODE` is set there), which would
    flip any test that asserts a bare call is a person's. A test that needs the variable sets it."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
