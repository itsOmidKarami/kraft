"""Repo-root pytest configuration: it reaches both testpaths (`tests/` and
`plugins/kraft-lite/tests/`), so what has to hold for every test lives here.
Everything else stays in tests/conftest.py."""

import functools
import json
import os
import sys
import zlib
from fnmatch import fnmatch
from pathlib import Path

import pytest

#: Seconds per test file, written by `dev/shard_durations.py`. Absent, every
#: file is placed by a hash of its path.
_DURATIONS = Path(__file__).parent / "tests" / "shard_durations.json"


def _shard() -> tuple[int, int] | None:
    """`KRAFT_TEST_SHARD=2/3` as (1, 3), or None when it is unset."""
    spec = os.environ.get("KRAFT_TEST_SHARD")
    if not spec:
        return None
    index, _, count = spec.partition("/")
    if not (index.isdecimal() and count.isdecimal() and 1 <= int(index) <= int(count)):
        raise pytest.UsageError(f"KRAFT_TEST_SHARD={spec!r}: want K/N with 1 <= K <= N")
    return int(index) - 1, int(count)


@functools.cache
def _durations() -> dict[str, float]:
    """The table, or `{}` when there is none. One that cannot be read stops the
    run: a quiet fall back to the hash would unbalance the shards unseen."""
    try:
        table = json.loads(_DURATIONS.read_text())
    except FileNotFoundError:
        return {}
    except ValueError as err:
        raise pytest.UsageError(f"{_DURATIONS.name}: {err}") from err
    if not isinstance(table, dict) or not all(
        isinstance(s, int | float) and s >= 0 for s in table.values()
    ):
        raise pytest.UsageError(f"{_DURATIONS.name}: want a {{path: seconds}} object")
    return table


def _split(table: dict[str, float], count: int) -> dict[str, int]:
    """Test file to shard (from 0): the longest first, each onto the lightest.
    A function of the table and the count alone, so every xdist worker and every
    job computes the same split."""
    loads = [0.0] * count
    plan = {}
    for path, seconds in sorted(table.items(), key=lambda item: (-item[1], item[0])):
        shard = loads.index(min(loads))
        loads[shard] += seconds
        plan[path] = shard
    return plan


@functools.cache
def _plan(count: int) -> dict[str, int]:
    return _split(_durations(), count)


def _shard_of(path: str, count: int) -> int:
    """A file the table does not name goes where its path hashes, as every file
    did before the table, so a new test file needs no entry to run."""
    return _plan(count).get(path, zlib.crc32(path.encode()) % count)


def pytest_configure(config):
    """A coroutine that is never awaited fails its test (pyproject
    `filterwarnings`), but the warning can land on whichever later test is
    running when the garbage collector reaches it. Recording where each
    coroutine was created puts the leaker's own file and line in the message."""
    sys.set_coroutine_origin_tracking_depth(8)
    if _shard():
        _durations()  # a table that cannot be read is a usage error now, not a collection error


def pytest_ignore_collect(collection_path, config):
    """Under a shard, do not import another shard's test files. Collecting the
    whole suite to drop two thirds of it was most of the seconds before the first
    test. Only a prune: `pytest_collection_modifyitems` still decides what runs."""
    try:
        shard = _shard()
    except pytest.UsageError:
        return None  # modifyitems reports it
    if shard is None or not any(
        fnmatch(collection_path.name, pattern) for pattern in config.getini("python_files")
    ):
        return None
    try:
        path = collection_path.relative_to(config.rootpath).as_posix()
    except ValueError:
        return None
    index, count = shard
    return True if _shard_of(path, count) != index else None


def pytest_collection_modifyitems(config, items):
    """CI runs the unit tier in shards: `KRAFT_TEST_SHARD=2/3` keeps the test
    files `_shard_of` puts in the second of three, and unset keeps them all.
    By file, so a module's fixtures are built in one shard. By crc32 where the
    table is silent, which every xdist worker computes alike; `hash()` is
    salted per process. `tests/conftest.py` scrubs the name before each test, so
    a pytest that a test starts (`kraft.intent`'s collection) still sees the
    whole suite."""
    shard = _shard()
    if shard is None:
        return
    index, count = shard
    mine, others = [], []
    for item in items:
        path = item.nodeid.partition("::")[0]
        (mine if _shard_of(path, count) == index else others).append(item)
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
