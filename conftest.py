"""Repo-root pytest configuration: it reaches both testpaths (`tests/` and
`plugins/kraft-lite/tests/`), so what has to hold for every test lives here.
Everything else stays in tests/conftest.py."""

import os
import sys
import zlib
from fnmatch import fnmatch

import pytest


def _shard() -> tuple[int, int] | None:
    """`KRAFT_TEST_SHARD=2/3` as (1, 3), or None when it is unset."""
    spec = os.environ.get("KRAFT_TEST_SHARD")
    if not spec:
        return None
    index, _, count = spec.partition("/")
    if not (index.isdecimal() and count.isdecimal() and 1 <= int(index) <= int(count)):
        raise pytest.UsageError(f"KRAFT_TEST_SHARD={spec!r}: want K/N with 1 <= K <= N")
    return int(index) - 1, int(count)


def _shard_of(path: str, count: int) -> int:
    """The shard (from 0) a test file belongs to. A hash of the path, which every
    xdist worker computes alike (`hash()` is salted per process), so a new test
    file needs no entry anywhere."""
    return zlib.crc32(path.encode()) % count


def pytest_configure(config):
    """A coroutine that is never awaited fails its test (pyproject
    `filterwarnings`), but the warning can land on whichever later test is
    running when the garbage collector reaches it. Recording where each
    coroutine was created puts the leaker's own file and line in the message."""
    sys.set_coroutine_origin_tracking_depth(8)


def pytest_ignore_collect(collection_path, config):
    """Under a shard, do not import another shard's test files. Collecting the
    whole suite to drop most of it was the bulk of the seconds before the first
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
    files whose path hashes into the second of three, and unset keeps them all.
    By file, so a module's fixtures are built in one shard. `tests/conftest.py`
    scrubs the name before each test, so a pytest that a test starts
    (`kraft.intent`'s collection) still sees the whole suite."""
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
