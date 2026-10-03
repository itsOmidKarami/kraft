"""`dev/mutation_pass.py` measures which tests are the sole killer of a mutant.
These pin the three things its number rests on, on a tiny module under
`tmp_path`, never on a real one: the mutants it generates, that the file under
mutation always gets its original bytes back, and the sole-killer arithmetic.

No real pytest is launched here: the script's one subprocess seam,
`subprocess.run` inside `run_test`, is patched.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "mutation_pass.py"


@pytest.fixture(scope="module")
def mp():
    spec = importlib.util.spec_from_file_location("_dev_mutation_pass", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # its dataclass resolves string annotations through sys.modules
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        del sys.modules[spec.name]


TINY = '''"""A docstring is never a string site."""


def f(x):
    if x < 1:
        return True
    return x


def g():
    return ("é", 1 == 2)
'''


def test_generates_one_mutant_per_site_of_each_kind(mp):
    """Sites from the four kinds the plan names, located by line, each spliced
    into the real source at its byte offsets -- the `é` before `1 == 2` is two
    bytes that a character offset would get wrong."""
    mutants = mp.generate_mutants(TINY, strings=False)

    assert sorted((m.kind, m.line, m.before, m.after) for m in mutants) == [
        ("bool", 6, "True", "False"),
        ("compare", 5, "x < 1", "x >= 1"),
        ("compare", 11, "1 == 2", "1 != 2"),
        ("negate_if", 5, "x < 1", "not (x < 1)"),
        ("return_none", 6, "True", "None"),
        ("return_none", 7, "x", "None"),
        ("return_none", 11, '("é", 1 == 2)', "None"),
    ]
    by_site = {(m.kind, m.line): m.source for m in mutants}
    assert 'return ("é", 1 != 2)' in by_site[("compare", 11)]
    assert "    if not (x < 1):\n" in by_site[("negate_if", 5)]
    assert len({m.id for m in mutants}) == len(mutants)

    with_strings = mp.generate_mutants(TINY, strings=True)
    strings = [(m.line, m.before) for m in with_strings if m.kind == "string"]
    assert strings == [(11, '"é"')]


@pytest.mark.parametrize(
    "raised",
    [RuntimeError("boom"), KeyboardInterrupt()],
    ids=["error", "keyboard-interrupt"],
)
def test_the_original_bytes_come_back_when_a_run_raises(mp, tmp_path, monkeypatch, raised):
    target = tmp_path / "subject.py"
    original = TINY.encode()
    target.write_bytes(original)
    mutants = mp.generate_mutants(TINY, strings=False)
    seen: list[bytes] = []
    between: list[bytes] = []

    def fake_run(cmd, **kwargs):
        seen.append(target.read_bytes())
        if len(seen) == 3:
            raise raised
        return subprocess.CompletedProcess(cmd, 0, stdout="1 passed in 0.01s\n", stderr="")

    monkeypatch.setattr(mp.subprocess, "run", fake_run)
    runner = mp.PytestRunner(python="python", cwd=tmp_path, scratch=tmp_path / "runs")

    with pytest.raises(type(raised)):
        mp.run_grid(
            target,
            mutants,
            ["t.py::a", "t.py::b"],
            runner,
            jobs=1,
            on_mutant=lambda m, row: between.append(target.read_bytes()),
        )

    assert target.read_bytes() == original
    # it really was mutated while the tests ran: each mutant's own bytes
    assert seen[0] == mutants[0].source.encode() != original
    assert seen[2] == mutants[1].source.encode()
    # and the original went back after the first mutant, not only at the end
    assert between == [original]


@pytest.mark.parametrize(
    ("returncode", "stdout", "outcome"),
    [
        (0, "1 passed in 0.1s", "pass"),
        (0, "1 skipped in 0.1s", "skip"),
        (1, "1 failed in 0.1s", "fail"),
        (2, "1 error during collection", "error"),
        (4, "ERROR: file or directory not found", "error"),
        (5, "no tests ran in 0.1s", "error"),
        (None, "", "timeout"),
    ],
    ids=["pass", "skip", "fail", "collection-error", "usage-error", "nothing-ran", "timeout"],
)
def test_a_run_outcome_says_killed_or_not(mp, tmp_path, monkeypatch, returncode, stdout, outcome):
    """A failure, a collection error and a timeout all kill; a skip does not,
    so a test that skips itself here can never be anyone's sole killer."""

    def fake_run(cmd, **kwargs):
        if returncode is None:
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(mp.subprocess, "run", fake_run)
    runner = mp.PytestRunner(python="python", cwd=tmp_path, scratch=tmp_path / "runs")

    assert runner("t.py::a") == outcome
    assert mp.killed(outcome) is (outcome in {"fail", "error", "timeout"})


def test_sole_killers_on_a_hand_built_grid(mp):
    p, f, e = "pass", "fail", "error"
    tests = ["t.py::a", "t.py::b", "t.py::c[x]", "t.py::c[y]"]

    def row(*outcomes):
        return dict(zip(tests, outcomes, strict=True))

    grid = {
        "m1": row(f, p, p, p),  # a alone
        "m2": row(f, e, p, p),  # two killers (an error kills too): nobody's alone
        "m3": row(p, p, p, "skip"),  # survived
        "m4": row(p, p, f, f),  # two cases, one function
        "m5": row(e, e, e, e),  # the module would not import
    }

    s = mp.summarize(grid, tests)

    assert s["sole_killers"] == {"t.py::a": ["m1"]}
    assert s["sole_killers_by_function"] == {"t.py::a": ["m1"], "t.py::c": ["m4"]}
    assert s["never_sole"] == ["t.py::b", "t.py::c[x]", "t.py::c[y]"]
    assert s["never_sole_by_function"] == ["t.py::b"]
    assert s["survived"] == ["m3"]
    assert s["import_failures"] == ["m5"]
    assert s["kills"] == {"t.py::a": 3, "t.py::b": 2, "t.py::c[x]": 2, "t.py::c[y]": 2}
    assert s["headline"] == "1 of 4 tests are the sole killer of at least one mutant"
    assert s["headline_by_function"] == (
        "2 of 3 test functions are the sole killer of at least one mutant"
    )


def test_a_sole_kill_that_does_not_fail_again_is_marked_flaky(mp, tmp_path):
    """A sole kill is the claim the headline rests on, so each one is run again
    with its mutant applied; one that passes the second time was load or luck,
    not the mutation, and stops counting as a kill."""
    target = tmp_path / "subject.py"
    target.write_bytes(TINY.encode())
    m1, m2, m3 = mp.generate_mutants(TINY, strings=False)[:3]
    a, b = "t.py::a", "t.py::b"
    grid = {
        m1.id: {a: "fail", b: "pass"},  # alone, and fails again
        m2.id: {a: "pass", b: "fail"},  # alone, but passes the second time
        m3.id: {a: "fail", b: "fail"},  # not alone: not re-run
    }
    calls: list[tuple[str, bytes]] = []

    def rerun(nodeid):
        calls.append((nodeid, target.read_bytes()))
        return "fail" if nodeid == a else "pass"

    flaky = mp.confirm_sole_kills(target, [m1, m2, m3], grid, rerun)

    assert flaky == {m2.id: b}
    assert grid[m2.id] == {a: "pass", b: "flaky"}
    assert grid[m1.id] == {a: "fail", b: "pass"}
    assert not mp.killed("flaky")
    assert calls == [(a, m1.source.encode()), (b, m2.source.encode())]
    assert target.read_bytes() == TINY.encode()
