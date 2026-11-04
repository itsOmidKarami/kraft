"""The `app` fixture holds one lifespan for a whole test (`tests/conftest.py`)."""

from __future__ import annotations

import asyncio
import contextlib

from kraft import client
from kraft.api import startup


def test_app_enters_the_lifespan_once_however_many_calls_follow(app, monkeypatch):
    entered = []
    real = startup._lifespan

    @contextlib.asynccontextmanager
    async def counted(a):
        entered.append(1)
        async with real(a):
            yield

    monkeypatch.setattr(startup, "_lifespan", counted)

    for _ in range(3):
        asyncio.run(client.repos())

    assert entered == [1]
