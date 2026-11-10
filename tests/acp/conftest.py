import pytest

from kraft.acp.client import CLIENT_ENV


@pytest.fixture(autouse=True)
def _not_inside_an_acp_client(monkeypatch):
    """A test run by an ACP agent inherits the client's recursion marker; a client the test
    builds in this process must not read it as its own."""
    monkeypatch.delenv(CLIENT_ENV, raising=False)
