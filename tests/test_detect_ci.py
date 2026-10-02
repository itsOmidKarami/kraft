"""`kraft.detect` reading CI and devcontainer files: which lines are
evidence, which directory each belongs to, and how they rank."""

from __future__ import annotations

import pytest
from support.probe import JEST, PYTEST
from support.probe import chosen as _chosen
from support.probe import propose as _propose
from support.probe import repo_with as _repo

# ── CI ──

_WORKFLOW = ".github/workflows/ci.yml"


def _workflow(steps: str) -> dict[str, str]:
    return {_WORKFLOW: f"on: push\njobs:\n  test:\n    runs-on: x\n    steps:\n{steps}"}


def test_what_ci_runs_beats_a_toolchain_default(tmp_path):
    files = {"go.mod": "module x\n", **_workflow("      - run: go test -race ./...\n")}
    p = _propose(_repo(tmp_path, files))
    assert p.test_command == "go test -race ./..."
    assert _chosen(p, "test")["source"] == _WORKFLOW


def test_a_runners_task_beats_what_ci_runs(tmp_path):
    files = {"Makefile": "test:\n\tgo test ./...\n", **_workflow("      - run: go test ./...\n")}
    assert _propose(_repo(tmp_path, files)).test_command == "make test"


def test_ci_running_the_proposal_corroborates_it_and_outranks_a_ci_only_line(tmp_path):
    steps = "      - run: npm test\n      - run: xvfb-run -a npm run test:e2e\n"
    files = {"package.json": JEST, "package-lock.json": "", **_workflow(steps)}
    p = _propose(_repo(tmp_path, files))
    assert p.test_command == "npm test"
    assert "CI runs it too (.github/workflows/ci.yml)" in _chosen(p, "test")["source"]
    assert [c["command"] for c in p.candidates if c["tier"] == "ci"] == [
        "xvfb-run -a npm run test:e2e"
    ]


@pytest.mark.parametrize(
    "line",
    [
        "pytest -n $WORKERS",
        'uv pip install "$WHEEL"',
        "npm test | tee out.txt",
        "test -f build/out",
        "echo running tests",
        "ruff check .",
        "x) npm test ;;",
    ],
    ids=[
        "a-variable",
        "a-variable-in-double-quotes",
        "a-pipe",
        "shell-test",
        "echo",
        "a-linter",
        "a-case-arm",
    ],  # noqa: E501
)
def test_a_ci_line_that_only_means_something_in_its_script_is_ignored(tmp_path, line):
    files = _workflow(f"      - run: '{line}'\n")
    assert _propose(_repo(tmp_path, files)).candidates == []


def test_a_github_script_input_is_not_a_command(tmp_path):
    steps = (
        "      - uses: actions/github-script@v7\n"
        "        with:\n"
        "          script: return 'all tests'\n"
    )
    assert _propose(_repo(tmp_path, _workflow(steps))).candidates == []


def test_a_ci_working_directory_makes_its_own_scope(tmp_path):
    steps = "      - run: zig build test\n        working-directory: ./web\n"
    files = {"web/main.zig": "", **_workflow(steps)}
    p = _propose(_repo(tmp_path, files))
    assert p.test_scopes == [{"paths": ["web/**"], "command": "sh -c 'cd web && zig build test'"}]


def test_a_cd_before_a_ci_command_is_its_directory(tmp_path):
    files = {"app/x.go": "", **_workflow("      - run: cd app && go test ./...\n")}
    p = _propose(_repo(tmp_path, files))
    assert [(c["dir"], c["command"]) for c in p.candidates] == [("app", "go test ./...")]


def test_a_ci_line_running_a_bare_tool_ranks_below_the_toolchain(tmp_path):
    """CI installed `pytest` on its own PATH; a worktree has `uv run`."""
    files = {"pyproject.toml": PYTEST, "uv.lock": "", **_workflow("      - run: pytest -x\n")}
    p = _propose(_repo(tmp_path, files))
    assert p.test_command == "uv run pytest"
    assert [(c["command"], c["chosen"]) for c in p.candidates if c["tier"] == "ci"] == [
        ("pytest -x", False)
    ]
    alone = _propose(_repo(tmp_path / "alone", _workflow("      - run: pytest -x\n")))
    assert alone.test_command == "pytest -x"


def test_the_repos_test_workflow_is_read_before_its_e2e_one(tmp_path):
    files = {
        ".github/workflows/e2e.yml": "jobs:\n  e:\n    steps:\n      - run: npm run test:e2e\n",
        ".github/workflows/unit.yml": "jobs:\n  u:\n    steps:\n      - run: go test -race ./...\n",
    }
    assert _propose(_repo(tmp_path, files)).test_command == "go test -race ./..."


_CIRCLE = (
    "jobs:\n  t:\n    steps:\n      - run:\n"
    "          command: make test\n          working_directory: svc\n"
)
_AZURE = "steps:\n  - bash: go test ./...\n    workingDirectory: api\n"


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({".circleci/config.yml": _CIRCLE, "svc/x.c": ""}, ("svc", "make test")),
        ({"azure-pipelines.yml": _AZURE, "api/x.go": ""}, ("api", "go test ./...")),
        (
            {**_workflow("      - run: npm --prefix web test\n"), "web/x.js": ""},
            ("web", "npm test"),
        ),
        ({**_workflow("      - run: yarn --cwd=web test\n"), "web/x.js": ""}, ("web", "yarn test")),
    ],
    ids=["circleci-long-form", "azure-bash-step", "npm-prefix", "yarn-cwd"],
)
def test_each_ci_form_names_its_directory(tmp_path, files, expected):
    p = _propose(_repo(tmp_path, files))
    assert [(c["dir"], c["command"]) for c in p.candidates if c["role"] == "test"] == [expected]


def test_ci_setup_is_shown_and_never_chosen(tmp_path):
    steps = "      - run: pip install poetry\n      - run: npm ci\n      - run: npm test\n"
    p = _propose(_repo(tmp_path, _workflow(steps)))
    setups = [c for c in p.candidates if c["role"] == "setup"]
    assert [(c["command"], c["chosen"]) for c in setups] == [("npm ci", False)]
    assert p.setup_command is None
    assert p.missing_setup == ["."]


def test_gitlab_ci_scripts_are_read(tmp_path):
    files = {
        ".gitlab-ci.yml": (
            "test:\n  before_script: [bundle install]\n  script:\n    - bundle exec rspec\n"
        )
    }
    p = _propose(_repo(tmp_path, files))
    assert [(c["role"], c["command"]) for c in p.candidates] == [
        ("setup", "bundle install"),
        ("test", "bundle exec rspec"),
    ]


# ── devcontainer ──


def test_a_devcontainers_commands_are_shown_and_never_chosen(tmp_path):
    """They are written for a container: commonly a global `pip install`."""
    dc = '{\n  // set up\n  "postCreateCommand": ["./tools/bootstrap", "--dev"],\n}'
    p = _propose(_repo(tmp_path, {".devcontainer/devcontainer.json": dc, "Makefile": "test:\n"}))
    assert p.setup_command is None
    assert [
        (c["command"], c["tier"], c["chosen"]) for c in p.candidates if c["role"] == "setup"
    ] == [("./tools/bootstrap --dev", "devenv", False)]


def test_a_devcontainer_provisioning_the_container_is_not_a_candidate(tmp_path):
    dc = '{"postCreateCommand": "sudo apt-get install -y libpq-dev && npm ci"}'
    p = _propose(_repo(tmp_path, {".devcontainer.json": dc, "Makefile": "test:\n"}))
    assert [c for c in p.candidates if c["tier"] == "devenv"] == []
