"""`kraft.detect` reading a repository whose files are built to hurt it: a
committed file must neither stall the server nor crash the probe. Its
regexes, parsers and CI walk get adversarial input sized like a real
attack, and a time bound."""

from __future__ import annotations

import time

import pytest
from support.probe import PYTEST
from support.probe import propose as _propose
from support.probe import repo_with as _repo

from kraft import config, detect

#: Comfortably above the probe of a real repo (well under a second), and far
#: below the tens of seconds each case took before its fix.
_BOUND_S = 3.0

_WORKFLOW = ".github/workflows/ci.yml"


def _timed(repo, **kw) -> tuple[detect.Proposal, float]:
    started = time.monotonic()
    p = _propose(repo, **kw)
    return p, time.monotonic() - started


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
}


@pytest.mark.parametrize("files", _BACKTRACKING.values(), ids=_BACKTRACKING)
def test_a_file_built_to_backtrack_is_read_in_bounded_time(tmp_path, files):
    """A regex that backtracks holds the GIL: the whole server, not only the
    probe's thread, froze for as long as it ran (38 s on a 110 KB Makefile)."""
    _, took = _timed(_repo(tmp_path, {**files, "go.mod": "module x\n"}))
    assert took < _BOUND_S


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
    p, took = _timed(_repo(tmp_path, files))
    assert took < _BOUND_S
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
    "deno-jsonc": ({"deno.jsonc": "[" * _DEEP}, "deno test"),
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
