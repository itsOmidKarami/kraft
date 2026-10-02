"""`kraft.detect` reading a repository whose files are built to hurt it: a
committed file must neither stall the server nor crash the probe. Its
regexes, parsers and CI walk get adversarial input sized like a real
attack, and a bound on the CPU it may spend."""

from __future__ import annotations

import signal
import time

import pytest
from support.probe import PYTEST
from support.probe import propose as _propose
from support.probe import repo_with as _repo

from kraft import config, detect

#: Seconds of CPU: comfortably above the probe of a real repo (well under a
#: second) and the costliest case below (about 1.2s), and far below the tens
#: of seconds each case took before its fix.
_BUDGET_S = 3.0

_WORKFLOW = ".github/workflows/ci.yml"
#: A file at the size the probe reads one to.
_BIG = detect._TEXT_LIMIT - 64


def _step(command: str) -> str:
    return f"jobs:\n  t:\n    steps:\n      - run: '{command}'\n"


class _OverBudget(BaseException):
    """Raised by the CPU timer. Not an `Exception`, so no handler inside the
    probe mistakes it for a parse error and reads on."""


def _within_budget(repo, **kw) -> detect.Proposal:
    """The proposal for `repo`, failing once this process has spent
    `_BUDGET_S` of CPU on it.

    CPU rather than wall time: a loaded machine makes the probe wait for a
    core, never compute more, so a busy CI runner cannot fail this. And the
    timer interrupts a regex mid-match (`re` checks for signals while it
    backtracks), so a regex that regressed fails here with this message
    instead of running into the suite's per-test timeout."""
    fired = []

    def over(signum, frame):
        fired.append(signum)
        raise _OverBudget

    previous = signal.signal(signal.SIGPROF, over)
    signal.setitimer(signal.ITIMER_PROF, _BUDGET_S)
    try:
        try:
            p = _propose(repo, **kw)
        finally:
            signal.setitimer(signal.ITIMER_PROF, 0)
    except _OverBudget:
        pass
    finally:
        signal.signal(signal.SIGPROF, previous)
    assert not fired, f"the probe spent more than {_BUDGET_S}s of CPU"
    return p


#: A file shaped to make one regex backtrack -> what still holds beside it.
_BACKTRACKING = {
    # `_MAKE_RULE`'s rule name once crossed lines: every line start rescanned
    # to the end of the file looking for a colon.
    "a-makefile-of-colon-free-lines": {"Makefile": "abcdefghij\n" * 30_000},
    "a-makefile-line-of-blanks": {"Makefile": "a" + " " * 200_000 + "\n"},
    "a-justfile-of-colon-free-lines": {"justfile": "abcdefghij x\n" * 30_000},
    # pubspec's `^\s+sdk` matched across lines from every line start.
    "a-pubspec-of-whitespace": {"pubspec.yaml": " \n" * 60_000},
    # The optional-dependencies table, once a regex from its header to the
    # end of the file, now read by its key.
    "a-pyproject-of-repeated-extras-headers": {
        "pyproject.toml": "[project.optional-dependencies]\n" * 25_000,
        "uv.lock": "",
    },
    # `_BARE_PYTHON`'s env assignment ran to the end of the line from each `;`.
    "a-test-recipe-of-separators": {
        "justfile": "test:\n    " + ";a=" * 60_000 + "\n",
        "pyproject.toml": PYTEST,
        "uv.lock": "",
    },
    # The rest at the full 4 MB a file is read to (a third review's cases).
    # `_RAKE_TASK`'s two whitespace runs either side of an optional `(`.
    "a-rakefile-task-of-blanks": {"Gemfile": "", "Rakefile": "task" + " " * _BIG + "!"},
    # `_BARE_PYTHON`'s blanks before and after `["']?[@-]*`.
    "a-test-recipe-of-blanks": {
        "justfile": "test:\n\t" + " " * _BIG + "x\n",
        "pyproject.toml": PYTEST,
        "uv.lock": "",
    },
    # `_PREFIX`'s blanks after `npm`, then a lazy run (within the CI budget).
    "a-ci-npm-line-of-blanks": {_WORKFLOW: _step("npm" + " " * 2_000_000 + "x")},
    # `_strip_env` popped the first of 200,000 assignments 200,000 times.
    "a-ci-line-of-assignments": {_WORKFLOW: _step("A=1 " * 200_000 + "npm test")},
    # `_corroborate` scanned every candidate for each of 20,000 CI lines.
    # (One script, within the CI budget a step per line would pass.)
    "a-workflow-of-distinct-test-lines": {
        _WORKFLOW: "jobs:\n  t:\n    steps:\n      - run: |\n"
        + "".join(f"          pytest -k t{n}\n" for n in range(20_000))
    },
}


@pytest.mark.parametrize("files", _BACKTRACKING.values(), ids=_BACKTRACKING)
def test_a_file_built_to_backtrack_is_read_in_bounded_time(tmp_path, files):
    """A regex that backtracks holds the GIL: the whole server, not only the
    probe's thread, froze for as long as it ran (38 s on a 110 KB Makefile)."""
    _within_budget(_repo(tmp_path, {**files, "go.mod": "module x\n"}))


def test_extras_naming_pytest_are_still_read_by_their_key(tmp_path):
    extras = "[project]\nname = 'x'\n[project.optional-dependencies]\ntest = [\n  'pytest>=8',\n]\n"
    p = _propose(_repo(tmp_path, {"pyproject.toml": extras, "uv.lock": ""}))
    assert p.setup_command == "uv sync --all-extras"
    elsewhere = "[project]\nname = 'x'\n[project.optional-dependencies]\ndocs = ['x']\n"
    other = _propose(
        _repo(tmp_path / "o", {"pyproject.toml": elsewhere + "[tool.pytest]\n", "uv.lock": ""})
    )
    assert other.setup_command == "uv sync", "pytest outside the extras table is not an extra"


def _alias_bomb(depth: int) -> str:
    """`depth` levels of nine aliases to the level below: 9**depth leaves
    from a few hundred bytes, all shared once loaded."""
    lines = ['a0: &a0 ["make test"]']
    for i in range(1, depth + 1):
        lines.append(f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 9)}]")
    lines.append(f"jobs:\n  t:\n    steps:\n      - run: *a{depth}")
    return "\n".join(lines) + "\n"


def test_a_ci_file_of_nested_aliases_is_skipped_not_walked(tmp_path):
    """A 418-byte workflow walked 9**6 nodes and ran past 90 s."""
    files = {
        ".github/workflows/bomb.yml": _alias_bomb(6),
        ".github/workflows/unit.yml": "jobs:\n  u:\n    steps:\n      - run: go test -race ./...\n",
    }
    p = _within_budget(_repo(tmp_path, files))
    assert [c["marker"] for c in p.candidates] == [".github/workflows/unit.yml"]


def test_a_ci_file_with_an_ordinary_anchor_is_still_read(tmp_path):
    """GitLab CI is written with anchors: only a walk past the budget is skipped."""
    gitlab = (
        ".tests: &tests\n  script:\n    - make test\nunit:\n  <<: *tests\nlint:\n  <<: *tests\n"
    )
    p = _propose(_repo(tmp_path, {".gitlab-ci.yml": gitlab}))
    assert p.test_command == "make test"


_DEEP = 100_000

#: A file nested deeper than its parser recurses -> what is proposed: that
#: file is no evidence (its tasks unread), the rest of the repo still is.
_NESTED = {
    "package-json": ({"package.json": "[" * _DEEP, "package-lock.json": ""}, "go test ./..."),
    "deno-jsonc": ({"deno.jsonc": "[" * _DEEP}, "sh -c 'deno test && go test ./...'"),
    "devcontainer": (
        {".devcontainer/devcontainer.json": '{"postCreateCommand": ' + "[" * _DEEP},
        "go test ./...",
    ),
    "workflow": ({_WORKFLOW: "a: " + "[" * _DEEP}, "go test ./..."),
    "taskfile": ({"Taskfile.yml": "a: " + "[" * _DEEP}, "go test ./..."),
    "pyproject": ({"pyproject.toml": "a = " + "[" * _DEEP, "uv.lock": ""}, "go test ./..."),
}


@pytest.mark.parametrize(("files", "expected"), _NESTED.values(), ids=_NESTED)
def test_a_file_nested_past_its_parser_is_no_evidence_not_a_crash(tmp_path, files, expected):
    """A RecursionError nothing caught was a bare 500 from connect, failed a
    parent's connect from inside one submodule, and crashed `kraft admin
    doctor`."""
    p = _propose(_repo(tmp_path, {**files, "go.mod": "module x\n"}))
    assert p.test_command == expected


def test_a_probe_past_its_time_is_killed_and_said(tmp_path, monkeypatch):
    """The last line of defence, for whatever no fix above foresaw: an
    operator's own regex here. The probe runs in a child process, so the
    server keeps the GIL, and the child is killed at the limit."""
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "detectors.yaml").write_text(
        "detectors:\n  - id: slow\n    tier: runner\n    files: [x]\n"
        "    contains: {x: '^(a+)+$'}\n    test:\n      - run: slow\n"
    )
    repo = _repo(tmp_path, {"x": "a" * 40 + "b"})
    monkeypatch.setattr(detect, "_PROBE_TIMEOUT_S", 2)
    started = time.monotonic()
    with pytest.raises(config.ConfigError, match="took more than 2s"):
        config.probe_repo(repo, templates_dir=templates)
    assert time.monotonic() - started < 10


def test_a_probe_that_fails_says_why(tmp_path):
    """The child's `ConfigError` comes back as one, word for word."""
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "detectors.yaml").write_text("disable: [nope]\n")
    with pytest.raises(config.ConfigError, match="disable names no packaged detector: nope"):
        config.probe_repo(_repo(tmp_path, {}), templates_dir=templates)


def test_a_probe_answers_as_it_did_in_process(tmp_path):
    repo = _repo(
        tmp_path, {"go.mod": "module x\n", "web/package.json": '{"scripts": {"test": "jest"}}'}
    )
    assert detect.probe(repo) == _propose(repo)


def _merge_bomb(depth: int, top: str = "") -> str:
    """`depth` levels of mappings, each merging nine aliases to the level
    below: 435 bytes at depth 8 took 5.6 s and 92 MB to build."""
    lines = ["a0: &a0 {x: 1}"]
    for i in range(1, depth + 1):
        lines.append(f"a{i}: &a{i} {{<<: [{', '.join([f'*a{i - 1}'] * 9)}]}}")
    return "\n".join(lines) + "\n" + top


@pytest.mark.parametrize("where", [_WORKFLOW, "Taskfile.yml"])
def test_a_yaml_merge_bomb_is_refused_before_it_is_built(tmp_path, where):
    top = (
        "jobs: {t: {steps: [{run: make test}]}}\n" if where == _WORKFLOW else "tasks: {test: {}}\n"
    )
    p = _within_budget(_repo(tmp_path, {where: _merge_bomb(9, top), "go.mod": "module x\n"}))
    assert p.test_command == "go test ./..."


def test_one_budget_covers_every_ci_file(tmp_path, monkeypatch):
    """A hundred large workflows each within a file's budget timed the probe
    out: the budget is the probe's, and a file is skipped once it is spent."""

    def text(f: int) -> str:
        lines = "".join(f"      - run: go test -run T{n}w{f}\n" for n in range(120))
        return f"jobs:\n  t:\n    steps:\n{lines}"

    one = detect._Index(_repo(tmp_path / "one", {".github/workflows/w0.yml": text(0)}))
    detect._ci_candidates(one)
    cost = detect._YAML_BUDGET - one.yaml_left[0]  # what one such file spends
    one.close()
    monkeypatch.setattr(detect, "_YAML_BUDGET", cost * 5 // 2)  # two and a half files
    monkeypatch.setattr(detect, "_CI_FILE_BUDGET", cost + 1)
    files = {f".github/workflows/w{n}.yml": text(n) for n in range(5)}
    read = {c["marker"] for c in _propose(_repo(tmp_path / "five", files)).candidates}
    assert read == {".github/workflows/w0.yml", ".github/workflows/w1.yml"}


def test_one_budget_covers_every_byte_read(tmp_path, monkeypatch):
    """A 4 MB file in each of thousands of directories was read and kept
    whole: 988 MB, then MemoryError."""
    monkeypatch.setattr(detect, "_READ_BUDGET", 1000)
    repo = _repo(tmp_path, {"a": "x" * 800, "b": "y" * 800, "c": "z"})
    index = detect._Index(repo)
    try:
        assert [len(index.text(r)) for r in ("a", "b", "c")] == [800, 200, 0]
    finally:
        index.close()


@pytest.mark.parametrize(
    "files",
    [
        # YAML takes a control character only escaped, in double quotes.
        {_WORKFLOW: 'jobs: {t: {steps: [{run: "npm test\\e[2K\\e[1Anpm test"}]}}\n'},
        {_WORKFLOW: 'jobs: {t: {steps: [{run: "npm test \\u202etset mpn"}]}}\n'},
        {".devcontainer.json": '{"postCreateCommand": "npm ci\\u001b[1A"}'},
    ],
    ids=["an-ansi-escape-in-ci", "a-bidi-override-in-ci", "an-escape-in-a-devcontainer"],
)
def test_a_command_with_a_control_character_is_no_candidate(tmp_path, files):
    """What a terminal would act on could show one command while argv runs
    another: such a line is no evidence at all."""
    assert _propose(_repo(tmp_path, files)).candidates == []


def test_the_probes_process_gets_no_secret_and_no_import_from_where_the_server_runs(
    tmp_path, monkeypatch
):
    """With `-c`, the server's working directory came first on the child's
    import path: a `fnmatch.py` there ran, holding ANTHROPIC_API_KEY."""
    evil = tmp_path / "evil"
    evil.mkdir()
    marker = tmp_path / "ran"
    # shlex: imported by the probe, and not already by Python's own startup.
    (evil / "shlex.py").write_text(f"open({str(marker)!r}, 'w').close()\nraise ImportError\n")
    monkeypatch.chdir(evil)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    seen = {}
    real = detect.subprocess.run

    def spy(argv, **kw):
        seen.update(kw)
        return real(argv, **kw)

    monkeypatch.setattr(detect.subprocess, "run", spy)
    repo = _repo(tmp_path, {"go.mod": "module x\n"})
    assert detect.probe(repo).test_command == "go test ./..."
    assert not marker.exists(), "a module from the server's working directory ran"
    assert "ANTHROPIC_API_KEY" not in seen["env"]


def test_a_probe_that_answers_too_much_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "_PROBE_OUTPUT", 16)
    with pytest.raises(config.ConfigError, match="answered more than 16 bytes"):
        detect.probe(_repo(tmp_path, {"go.mod": "module x\n"}))


def test_a_repos_own_fsmonitor_never_runs(tmp_path):
    """A repo with no commit is listed with `ls-files --others`, which runs
    the `core.fsmonitor` its own .git/config names."""
    import subprocess

    repo = tmp_path / "fresh"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    marker = tmp_path / "ran"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "core.fsmonitor", str(hook)], check=True)
    (repo / "go.mod").write_text("module x\n")
    assert _propose(repo).test_command == "go test ./..."
    assert not marker.exists()
