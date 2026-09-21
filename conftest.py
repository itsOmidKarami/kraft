"""Repo-root pytest configuration: it reaches both testpaths (`tests/` and
`plugins/kraft-lite/tests/`), so what has to hold for every test lives here.
Everything else stays in tests/conftest.py."""

import sys

import pytest


def pytest_configure(config):
    """A coroutine that is never awaited fails its test (pyproject
    `filterwarnings`), but the warning can land on whichever later test is
    running when the garbage collector reaches it. Recording where each
    coroutine was created puts the leaker's own file and line in the message."""
    sys.set_coroutine_origin_tracking_depth(8)


@pytest.fixture
def anyio_backend():
    """Every `async def` test runs once, on asyncio.

    `anyio_mode = "auto"` (pyproject) hands every `async def test_*` to anyio's
    plugin, whose own `anyio_backend` is parametrized over each installed
    backend. That would rename every async test to `test_x[asyncio]` (breaking
    intent pins) and run it again on trio the day trio is installed. Kraft is
    asyncio-only, so pin it once, unparametrized, for both test trees."""
    return "asyncio"
