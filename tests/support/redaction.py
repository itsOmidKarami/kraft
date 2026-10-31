"""Every test starts with no secret registered with `kraft.redact`."""

import pytest

from kraft import redact


@pytest.fixture(autouse=True)
def _no_registered_secrets(monkeypatch):
    """One a test (or code it ran) registered would make every later log
    record in the worker drop its `exc_info`, which tests read."""
    monkeypatch.setattr(redact, "_secrets", ())
