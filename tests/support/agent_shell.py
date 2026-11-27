"""Every test starts outside a coding agent's shell."""

import pytest


@pytest.fixture(autouse=True)
def _not_inside_an_agents_shell(monkeypatch):
    """`kraft` tags a call from a coding agent's shell (`CLAUDE_CODE_CHILD_SESSION` is set
    there), which would flip any test that asserts a bare call is a person's. `CLAUDECODE` is
    cleared too: an IDE's terminal sets it, and nothing may read it as an agent's. A test that
    needs a variable sets it."""
    monkeypatch.delenv("CLAUDE_CODE_CHILD_SESSION", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
