"""Fixtures shared by the template tests."""

from __future__ import annotations

import pytest

from kraft import policy as _policy


@pytest.fixture
def warnings_afresh(monkeypatch):
    """A deprecation is said once per process; these tests hear it anew."""
    monkeypatch.setattr(_policy, "_DEPRECATIONS_SAID", set())
