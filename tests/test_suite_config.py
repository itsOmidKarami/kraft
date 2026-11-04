"""The suite's own pytest configuration (pyproject `[tool.pytest.ini_options]`)
does what it claims, checked by running a throwaway suite under it."""

from __future__ import annotations

import ast
import errno
import importlib.util
import json
import os
import socket
import subprocess
import tomllib
import zlib
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


def _throwaway_suite(pytester, weights: dict[int, float] | None = None) -> list[str]:
    """Thirteen two-test files across both testpaths, under the repo-root conftest.
    `weights` (file number to seconds) writes a `tests/shard_durations.json` naming
    those files; the rest of the files are not in it."""
    pytester.makeconftest((_ROOT / "conftest.py").read_text())
    names = [f"tests/pkg{n % 3}/test_m{n}.py" for n in range(12)]
    names.append("plugins/kraft-lite/tests/test_b.py")
    for name in names:
        path = pytester.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_one():\n    pass\n\n\ndef test_two():\n    pass\n")
    if weights is not None:
        table = {names[n]: seconds for n, seconds in weights.items()}
        (pytester.path / "tests" / "shard_durations.json").write_text(json.dumps(table))
    return names


def test_testmon_keys_its_records_without_this_projects_own_version():
    """The project's version comes from git and moves with every commit, so a
    testmon record keyed by it matches no other commit and `just test-targeted`
    runs the whole suite (Kraft-4t45n.3). Naming any package replaces testmon's
    default ignore list, so pytest-testmon must be named too."""
    from testmon.common import get_system_packages

    project = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]["name"]
    keyed = get_system_packages(ignore=_INI["testmon_ignore_dependencies"]).split(", ")
    assert not [p for p in keyed if p.split(" ")[0] in {project, "pytest-testmon"}]


@pytest.mark.parametrize("count", [2, 3])
@pytest.mark.parametrize(
    "weights",
    [
        pytest.param(None, id="no-table"),
        pytest.param({n: 1 + (n * 7) % 11 for n in range(13)}, id="every-file-named"),
        pytest.param({0: 50, 1: 40, 2: 30, 3: 20, 4: 10}, id="some-files-named"),
    ],
)
def test_shards_split_the_suite_with_nothing_lost_and_nothing_run_twice(
    pytester, monkeypatch, count, weights
):
    """CI's unit shards (`KRAFT_TEST_SHARD=K/N`) together run every test
    exactly once, each shard runs something, and a file's tests stay in one
    shard. A test in no shard would be a test CI never runs, on a green run.
    So with a durations table that names every file, some, or none."""
    names = _throwaway_suite(pytester, weights)

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


@pytest.mark.parametrize(
    "spec", ["0/3", "4/3", "3", "a/b", "1/0", "-1/3", pytest.param("²/3", id="superscript-digit")]
)
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


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("not json", id="not-json"),
        pytest.param("[1, 2]", id="not-an-object"),
        pytest.param('{"tests/a.py": "slow"}', id="a-word-for-seconds"),
        pytest.param('{"tests/a.py": -1}', id="negative-seconds"),
    ],
)
def test_a_durations_table_that_cannot_be_read_stops_a_shard_instead_of_unbalancing_it(
    pytester, monkeypatch, text
):
    """Falling back to the hash on a bad table would leave three green jobs and
    an uneven split nobody sees."""
    _throwaway_suite(pytester)
    (pytester.path / "tests" / "shard_durations.json").write_text(text)
    monkeypatch.setenv("KRAFT_TEST_SHARD", "1/3")
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*shard_durations.json*"])


def _root_conftest():
    spec = importlib.util.spec_from_file_location("kraft_root_conftest", _ROOT / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_split_puts_the_longest_files_first_onto_the_lightest_shard():
    """Nine files of 1s to 9s over three shards: longest first gives 14, 15 and
    16 of 45. In name order, which here is shortest first, it is 12, 15 and 18."""
    table = {f"tests/test_{letter}.py": float(n) for n, letter in enumerate("abcdefghi", 1)}
    plan = _root_conftest()._split(table, 3)
    loads = [sum(s for f, s in table.items() if plan[f] == k) for k in range(3)]
    assert max(loads) - min(loads) <= 2


def test_a_file_the_table_does_not_name_goes_where_its_path_hashes():
    """A new test file runs without an entry in the table."""
    path = "tests/test_brand_new_and_unlisted.py"
    assert _root_conftest()._shard_of(path, 3) == zlib.crc32(path.encode()) % 3


def test_the_shard_durations_table_is_there_and_names_test_files():
    """Without it every file hashes and the shards are uneven again, with nothing
    failing to say so."""
    table = json.loads((_ROOT / "tests" / "shard_durations.json").read_text())
    assert table
    assert all(isinstance(seconds, int | float) and seconds >= 0 for seconds in table.values())
    assert all(
        path.startswith(("tests/", "plugins/kraft-lite/tests/")) and path.endswith(".py")
        for path in table
    )


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


def test_the_kraft_port_every_test_starts_with_is_held_and_answers_nothing():
    """`_isolated_kraft_home` pins KRAFT_PORT to a number no daemon is on. It is
    held for the whole test, so a listener another xdist worker starts cannot
    take it (CI once answered a "no server" check with another test's relay),
    and it never listens, so a call to it is refused."""
    port = int(os.environ["KRAFT_PORT"])
    with socket.socket() as taker, pytest.raises(OSError) as raised:
        taker.bind(("127.0.0.1", port))
    assert raised.value.errno == errno.EADDRINUSE
    with socket.socket() as caller:
        assert caller.connect_ex(("127.0.0.1", port)) == errno.ECONNREFUSED


def _child_spawns(monkeypatch) -> list[list[str]]:
    """Every probe child `subprocess.Popen` starts from here on."""
    seen = []
    real = subprocess.Popen.__init__

    def spy(self, args, *a, **k):
        if "kraft.detect" in " ".join(args):
            seen.append(list(args))
        return real(self, args, *a, **k)

    monkeypatch.setattr(subprocess.Popen, "__init__", spy)
    return seen


@pytest.mark.parametrize(
    "children",
    [0, pytest.param(1, marks=pytest.mark.real_repo_probe)],
    ids=["in-process", "child"],
)
def test_the_repository_probe_answers_alike_in_this_process_and_in_its_child(
    tmp_path, monkeypatch, children
):
    """`detect.probe` costs ~0.3 s of imports in its child interpreter, 600 of them 5% of a full
    run: a test gets the same proposal, and the same one-line failure, in this process unless it
    says `@pytest.mark.real_repo_probe`."""
    from support.harness import git
    from support.probe import propose, repo_with

    from kraft import config, detect

    repo = repo_with(tmp_path, {"go.mod": "module x\n", "x_test.go": ""})
    spawned = _child_spawns(monkeypatch)

    found = detect.probe(repo)

    assert found == propose(repo)
    assert found.test_command == "go test ./..."
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    (repo / ".git" / "objects" / tree[:2] / tree[2:]).unlink()
    with pytest.raises(config.ConfigError, match=r"^git ls-tree .* failed") as raised:
        detect.probe(repo)
    assert "\n" not in str(raised.value)
    assert len(spawned) == 2 * children


def test_an_in_process_probe_failure_keeps_the_last_line_the_childs_stderr_would_give(
    tmp_path, monkeypatch
):
    from kraft import config, detect

    def refuse(*args, **kwargs):
        raise config.ConfigError("git ls-tree failed:\nfatal: bad object")

    monkeypatch.setattr(detect, "propose", refuse)

    with pytest.raises(config.ConfigError) as raised:
        detect.probe(tmp_path)

    assert str(raised.value) == "fatal: bad object"
