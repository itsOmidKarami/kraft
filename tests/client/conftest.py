"""Shared setup for the client tests: the real app, reached in-process."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from support.harness import fake_templates_dir, isolated_bd

from kraft import client

_FAKE_CLAUDE = Path(__file__).resolve().parents[2] / "fixtures" / "fake-claude.sh"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """The app module, with `client.transport.http()` pointed at it over ASGI,
    hermetic under `tmp_path` (its own run dir, bd workspace and templates).

    `httpx.ASGITransport` does not run the app's lifespan, so a test runs its
    whole scenario in one coroutine under `support.api.run_with_app`, which
    enters it:

        def test_x(wired, tmp_path):
            async def scenario():
                return await client.list_work_items()

            assert run_with_app(wired, scenario) == []

    Unlike the suite's `app` fixture, which enters the lifespan per client for
    `cli.main()`'s own `asyncio.run`, the lifespan here stays open across the
    scenario, because the Database it opens is bound to that one loop.
    """
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    import kraft.api as api

    monkeypatch.setattr(
        client.transport,
        "http",
        lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://127.0.0.1"
        ),
    )
    return api
