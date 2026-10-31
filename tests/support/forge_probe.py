"""No test dials a forge or runs `git credential fill` through the server's token probe."""

import pytest

from kraft.adapters.forge import credentials, probe

#: The accounts the canned probe was asked about, for a test that counts probes.
CALLS: list[str] = []


@pytest.fixture(autouse=True)
def _canned_probe(request, monkeypatch):
    """Module state the app boot fills is reset, and `probe_account` answers `ok`; a test
    that drives the real probe says `@pytest.mark.real_probe`."""
    CALLS.clear()
    monkeypatch.setattr(credentials, "_TAKEN", {})
    monkeypatch.setattr(credentials, "_SECRET_FILES", ())
    if request.node.get_closest_marker("real_probe"):
        return

    async def canned(name, account, **kw):
        CALLS.append(name)
        return probe.Probe(name, "2026-01-01T00:00:00+00:00", "ok", "signed in as canned")

    monkeypatch.setattr(probe, "probe_account", canned)
