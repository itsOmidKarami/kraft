"""No test dials a forge, runs `git credential fill` through the server's token probe, or
starts the child interpreter the repository probe runs in."""

from pathlib import Path

import pytest

from kraft import detect
from kraft.adapters.forge import credentials, git_auth, probe
from kraft.config import ConfigError

#: The accounts the canned probe was asked about, for a test that counts probes.
CALLS: list[str] = []


@pytest.fixture(autouse=True)
def _canned_probe(request, monkeypatch):
    """Module state the app boot fills is reset, and `probe_account` answers `ok`; a test
    that drives the real probe says `@pytest.mark.real_probe`."""
    CALLS.clear()
    monkeypatch.setattr(credentials, "_TAKEN", {})
    monkeypatch.setattr(credentials, "_SECRET_FILES", ())
    monkeypatch.setattr(credentials, "_TOKENS", {})
    monkeypatch.setattr(credentials, "_LOCKS", {})
    monkeypatch.setattr(credentials, "_CONTEXT", None)
    monkeypatch.setattr(git_auth, "_READER", None)
    if request.node.get_closest_marker("real_probe"):
        return

    async def canned(name, account, **kw):
        CALLS.append(name)
        return probe.Probe(name, "2026-01-01T00:00:00+00:00", "ok", "signed in as canned")

    monkeypatch.setattr(probe, "probe_account", canned)


@pytest.fixture(autouse=True)
def _repository_probe_in_process(request, monkeypatch):
    """`detect.probe` runs `propose` in a child interpreter (`python -P -c`), which spends
    about 0.3 s importing kraft before it reads a file: 600 of them were 5% of a full run. Here
    the same call answers the same proposal in this process. A test about the child itself (its
    limits, its environment, its kills) says `@pytest.mark.real_repo_probe`."""
    if request.node.get_closest_marker("real_repo_probe"):
        return

    def in_process(root, templates_dir=None, *, test_command=None, timeout=None):
        try:
            return detect.propose(
                Path(root),
                detect.load(Path(templates_dir) if templates_dir else None),
                test_command=test_command,
            )
        except ConfigError as exc:
            # The child prints the message and the parent keeps its last line.
            lines = str(exc).strip().splitlines()
            raise ConfigError(lines[-1] if lines else "probe failed") from None

    monkeypatch.setattr(detect, "probe", in_process)
