"""The suite's own pytest configuration (pyproject `[tool.pytest.ini_options]`)
does what it claims, checked by running a throwaway suite under it."""

from __future__ import annotations

import tomllib
from pathlib import Path

pytest_plugins = ("pytester",)

_INI = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())["tool"][
    "pytest"
]["ini_options"]


def test_a_forgotten_await_fails_the_test(pytester):
    """A coroutine that is never awaited did its work nowhere, and the test
    asserting on it can pass on nothing. Python only warns, and CPython
    raises that warning inside the coroutine's finalizer, where it becomes an
    unraisable exception -- so both halves of the filter are needed for the
    test to fail rather than print a warning."""
    pytester.makeini(
        "[pytest]\nanyio_mode = auto\nfilterwarnings =\n"
        + "".join(f"    {f}\n" for f in _INI["filterwarnings"])
    )
    pytester.makepyfile(
        """
        async def write():
            return 1

        def test_sync_caller_forgets():
            write()

        async def test_async_caller_forgets(anyio_backend):
            write()

        def test_a_caller_that_awaits_is_fine():
            import asyncio
            assert asyncio.run(write()) == 1
        """
    )
    pytester.makeconftest(
        "import pytest\n\n@pytest.fixture\ndef anyio_backend():\n    return 'asyncio'\n"
    )
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, failed=2)
