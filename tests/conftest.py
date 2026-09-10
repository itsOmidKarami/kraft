from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path

import httpx
import pytest
from support.harness import fake_templates_dir, isolated_bd

from kraft import client

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def pytest_collection_modifyitems(config, items):
    if os.environ.get("KRAFT_E2E") == "1" and shutil.which("claude"):
        return
    skip = pytest.mark.skip(reason="e2e: set KRAFT_E2E=1 and install `claude` to run")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolated_kraft_home(tmp_path, monkeypatch):
    """No test may reach the operator's real `~/.kraft`.

    `kraft_home()` falls back to `~/.kraft` (paths.py:16). Only KRAFT_RUN_DIR and
    KRAFT_TEMPLATES_DIR were ever overridden, so `default_skills_dir()` — and any
    check that reads its env var lazily — resolved against the real home, which
    exists on a developer machine and not in a CI container. Autouse rather than
    part of `app`, so a test cannot reach the home by not opting in.
    """
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "kraft-home"))
    # Nor a real `kraft admin start` on the default port 8765: a test that
    # doesn't opt into the `app` fixture's ASGI transport falls through to a
    # real HTTP call in `client.base_url()`, and a developer machine running
    # `kraft` for real answers it — turning an expected ConnectError into a
    # live 401. Pin an ephemeral port nothing is listening on yet instead.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        monkeypatch.setenv("KRAFT_PORT", str(probe.getsockname()[1]))
    # No test may reach the release feed either. `doctor`'s version check and the
    # notice at boot both call out to gitlab.com, which would make this suite
    # depend on that host being up and cost every offline run a timeout. The
    # tests that exercise the check set their own stubs and unset this.
    monkeypatch.setenv("KRAFT_NO_UPDATE_CHECK", "1")
    # Nor the operator's real `~/.beads` (Kraft-t5g): bd's fallback when it finds
    # no `.beads/` walking up from cwd is a hardcoded `~/.beads`, not KRAFT_HOME.
    # Every throwaway repo the suite builds sets its own local git user config
    # (make_repo, _bd_template), so bd's `--actor` default never needs the real
    # `$HOME`'s global one.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def app(tmp_path, monkeypatch):
    """The app wired to client.http(), with its lifespan entered per call.

    cli.main() runs asyncio.run() itself, so — unlike test_client_read.py, where
    one coroutine owns the loop — the lifespan cannot stay open across the call.
    Each handler opens and closes its own loop, so each gets its own lifespan.
    """
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    import kraft.api as api

    class Lifespan(httpx.AsyncClient):
        """An AsyncClient that enters the app lifespan for the life of the client."""

        async def __aenter__(self):
            self._ctx = api.app.router.lifespan_context(api.app)
            await self._ctx.__aenter__()
            return await super().__aenter__()

        async def __aexit__(self, *exc):
            await super().__aexit__(*exc)
            await self._ctx.__aexit__(*exc)

    monkeypatch.setattr(
        client,
        "http",
        lambda: Lifespan(transport=httpx.ASGITransport(app=api.app), base_url="http://kraft"),
    )
    return api
