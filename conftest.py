"""Repo-root pytest configuration: it reaches both testpaths (`tests/` and
`plugins/kraft-lite/tests/`), so what has to hold for every test lives here.
Everything else stays in tests/conftest.py."""

import os
import sys
import zlib

import pytest


def pytest_configure(config):
    """A coroutine that is never awaited fails its test (pyproject
    `filterwarnings`), but the warning can land on whichever later test is
    running when the garbage collector reaches it. Recording where each
    coroutine was created puts the leaker's own file and line in the message."""
    sys.set_coroutine_origin_tracking_depth(8)


def pytest_collection_modifyitems(config, items):
    """CI runs the unit tier in shards: `KRAFT_TEST_SHARD=2/3` keeps the test
    files whose path falls in the second of three, and unset keeps them all.
    By file, so a module's fixtures are built in one shard. By crc32, which
    every xdist worker computes alike; `hash()` is salted per process.
    `tests/conftest.py` scrubs the name before each test, so a pytest that a
    test starts (`kraft.intent`'s collection) still sees the whole suite."""
    spec = os.environ.get("KRAFT_TEST_SHARD")
    if not spec:
        return
    index, _, count = spec.partition("/")
    if not (index.isdecimal() and count.isdecimal() and 1 <= int(index) <= int(count)):
        raise pytest.UsageError(f"KRAFT_TEST_SHARD={spec!r}: want K/N with 1 <= K <= N")
    mine, others = [], []
    for item in items:
        path = item.nodeid.partition("::")[0]
        ours = zlib.crc32(path.encode()) % int(count) == int(index) - 1
        (mine if ours else others).append(item)
    items[:] = mine
    config.hook.pytest_deselected(items=others)


@pytest.fixture
def anyio_backend():
    """Every `async def` test runs once, on asyncio.

    `anyio_mode = "auto"` (pyproject) hands every `async def test_*` to anyio's
    plugin, whose own `anyio_backend` is parametrized over each installed
    backend. That would rename every async test to `test_x[asyncio]` (breaking
    intent pins) and run it again on trio the day trio is installed. Kraft is
    asyncio-only, so pin it once, unparametrized, for both test trees."""
    return "asyncio"
