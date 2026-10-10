"""The suite's own pytest configuration (pyproject `[tool.pytest.ini_options]`)
does what it claims, checked by running a throwaway suite under it."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

pytest_plugins = ("pytester",)

_ROOT = Path(__file__).resolve().parents[1]
_INI = tomllib.loads((_ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]


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


def test_an_async_test_in_either_tree_keeps_its_plain_id(pytester):
    """The repo-root conftest pins `anyio_backend` for both testpaths, so an
    `async def` test anywhere is collected once, under its own name -- not as
    `test_x[asyncio]`, which would break the intent pins that name it."""
    pytester.makeini(f"[pytest]\nanyio_mode = {_INI['anyio_mode']}\n")
    pytester.makeconftest((_ROOT / "conftest.py").read_text())
    for tree, name in (("tests", "test_a.py"), ("plugins/kraft-lite/tests", "test_b.py")):
        (pytester.path / tree).mkdir(parents=True)
        (pytester.path / tree / name).write_text("async def test_x():\n    pass\n")
    result = pytester.runpytest_inprocess("--collect-only", "-q", "-p", "no:cacheprovider")
    ids = sorted(line for line in result.outlines if "::" in line)
    assert ids == ["plugins/kraft-lite/tests/test_b.py::test_x", "tests/test_a.py::test_x"]


def _throwaway_suite(pytester) -> list[str]:
    """Thirteen two-test files across both testpaths, under the repo-root conftest."""
    pytester.makeconftest((_ROOT / "conftest.py").read_text())
    names = [f"tests/pkg{n % 3}/test_m{n}.py" for n in range(12)]
    names.append("plugins/kraft-lite/tests/test_b.py")
    for name in names:
        path = pytester.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_one():\n    pass\n\n\ndef test_two():\n    pass\n")
    return names


@pytest.mark.parametrize("count", [2, 3])
def test_shards_split_the_suite_with_nothing_lost_and_nothing_run_twice(
    pytester, monkeypatch, count
):
    """CI's unit shards (`KRAFT_TEST_SHARD=K/N`) together run every test
    exactly once, each shard runs something, and a file's tests stay in one
    shard. A test in no shard would be a test CI never runs, on a green run."""
    names = _throwaway_suite(pytester)

    def collected(shard: str | None) -> list[str]:
        monkeypatch.delenv("KRAFT_TEST_SHARD", raising=False)
        if shard:
            monkeypatch.setenv("KRAFT_TEST_SHARD", shard)
        result = pytester.runpytest_inprocess("--collect-only", "-q", "-p", "no:cacheprovider")
        return sorted(line for line in result.outlines if "::" in line)

    whole = collected(None)
    shards = [collected(f"{k}/{count}") for k in range(1, count + 1)]
    assert len(whole) == 2 * len(names)
    assert sorted(node for shard in shards for node in shard) == whole
    assert all(shards)
    for shard in shards:
        assert len(shard) == 2 * len({node.split("::")[0] for node in shard})


@pytest.mark.parametrize("spec", ["0/3", "4/3", "3", "a/b", "1/0", "-1/3"])
def test_a_malformed_shard_stops_the_run_instead_of_running_the_wrong_tests(
    pytester, monkeypatch, spec
):
    """A shard that cannot be read is a usage error, never a quiet full run
    (three jobs each running everything) or a quiet empty one."""
    _throwaway_suite(pytester)
    monkeypatch.setenv("KRAFT_TEST_SHARD", spec)
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*KRAFT_TEST_SHARD=*: want K/N with 1 <= K <= N*"])


def _spawns_kraft(call) -> bool:
    """`[..., "-m", "kraft", ...]` as the call's first argument."""
    if not call.args or not isinstance(call.args[0], ast.List):
        return False
    words = [e.value for e in call.args[0].elts if isinstance(e, ast.Constant)]
    return any(words[i : i + 2] == ["-m", "kraft"] for i in range(len(words)))


def _env_is_child_env(call) -> bool:
    env = next((k.value for k in call.keywords if k.arg == "env"), None)
    func = getattr(env, "func", None)
    return getattr(func, "id", getattr(func, "attr", None)) == "child_env"


def test_every_kraft_child_process_gets_its_env_from_child_env():
    """A `python -m kraft` child runs outside the in-process beads fake, so its
    environment has to come from `support.server.child_env`, which puts the
    loud `bd` stub first on PATH (Kraft-vrcw3). A spawn that builds its own env
    would reach the real bd from the unit tier."""
    offenders = []
    for path in sorted([*(_ROOT / "tests").rglob("*.py"), *(_ROOT / "plugins").rglob("test*.py")]):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and _spawns_kraft(node) and not _env_is_child_env(node):
                offenders.append(f"{path.relative_to(_ROOT)}:{node.lineno}")
    assert offenders == []
    # and the scan does find the spawns it is guarding
    assert any(
        _spawns_kraft(n)
        for n in ast.walk(ast.parse((_ROOT / "tests" / "cli" / "test_admin.py").read_text()))
        if isinstance(n, ast.Call)
    )
