"""Repo-root pytest configuration: it reaches both testpaths (`tests/` and
`plugins/kraft-lite/tests/`), so what has to hold for every test lives here.
Everything else stays in tests/conftest.py."""

import pytest


@pytest.fixture
def anyio_backend():
    """Every `async def` test runs once, on asyncio.

    `anyio_mode = "auto"` (pyproject) hands every `async def test_*` to anyio's
    plugin, whose own `anyio_backend` is parametrized over each installed
    backend. That would rename every async test to `test_x[asyncio]` (breaking
    intent pins) and run it again on trio the day trio is installed. Kraft is
    asyncio-only, so pin it once, unparametrized, for both test trees."""
    return "asyncio"
