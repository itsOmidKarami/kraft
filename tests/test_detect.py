"""`kraft.detect`: what `kraft repo connect` proposes, from what evidence."""

from __future__ import annotations

import subprocess

import pytest
from support.harness import commit_all, make_repo

from kraft import config, detect

PYTEST = "[project]\nname = 'x'\n[tool.pytest.ini_options]\n"
JEST = '{"scripts": {"test": "jest"}}'


def _repo(tmp_path, files: dict[str, str], executable: tuple[str, ...] = ()):
    repo = make_repo(tmp_path)
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    for rel in executable:
        (repo / rel).chmod(0o755)
    commit_all(repo)
    return repo


def _propose(repo, templates_dir=None, **kw) -> detect.Proposal:
    return detect.propose(repo, detect.load(templates_dir), **kw)


def _chosen(p: detect.Proposal, role: str, d: str = "") -> dict:
    return next(c for c in p.candidates if c["chosen"] and c["role"] == role and c["dir"] == d)


DJANGO = "[project]\nname = 'x'\n"
EXTRAS = "[project]\nname = 'x'\n[project.optional-dependencies]\ntest = ['pytest']\n"
ANDROID = "plugins { id 'com.android.application' }\n"
WS = '{"private": true, "workspaces": ["packages/*"]}'

#: (case, files, the proposed test command, the proposed setup command): at
#: least one case per packaged detector, `test_every_packaged_detector_has_a_case`
#: checks.
KINDS = [
    ("just", {"justfile": "setup:\n  uv sync\ntest:\n  pytest\n"}, "just test", "just setup"),
    ("make", {"Makefile": "deps:\n\tnpm ci\ntest: deps\n\tnpm test\n"}, "make test", "make deps"),
    ("make-check", {"Makefile": ".PHONY: check\ncheck:\n\t./run\n"}, "make check", None),
    (
        "taskfile",
        {"Taskfile.yml": "version: '3'\ntasks:\n  test: {cmds: [go test]}\n"},
        "task test",
        None,
    ),  # noqa: E501
    ("mise", {"mise.toml": '[tasks.test]\nrun = "pytest"\n'}, "mise run test", None),
    (
        "pnpm",
        {"pnpm-lock.yaml": "", "package.json": JEST},
        "pnpm test",
        "pnpm install --frozen-lockfile",
    ),  # noqa: E501
    (
        "pnpm-workspace",
        {"pnpm-lock.yaml": "", "pnpm-workspace.yaml": "", "package.json": "{}"},
        "pnpm -r --if-present test",
        "pnpm install --frozen-lockfile",
    ),  # noqa: E501
    (
        "pnpm-nx",
        {"pnpm-lock.yaml": "", "nx.json": "{}", "package.json": "{}"},
        "pnpm exec nx run-many -t test",
        "pnpm install --frozen-lockfile",
    ),  # noqa: E501
    (
        "npm-turbo",
        {"package-lock.json": "", "turbo.json": "{}", "package.json": "{}"},
        "npx turbo run test",
        "npm ci",
    ),  # noqa: E501
    (
        "yarn-berry",
        {"yarn.lock": "", ".yarnrc.yml": "nodeLinker: pnp\n", "package.json": JEST},
        "yarn test",
        "yarn install --immutable",
    ),  # noqa: E501
    (
        "yarn-berry-workspaces",
        {"yarn.lock": "", ".yarnrc.yml": "x: 1\n", "package.json": WS},
        "yarn workspaces foreach -A run test",
        "yarn install --immutable",
    ),  # noqa: E501
    (
        "yarn-classic",
        {"yarn.lock": "", "package.json": JEST},
        "yarn test",
        "yarn install --frozen-lockfile",
    ),  # noqa: E501
    (
        "bun",
        {"bun.lock": "", "package.json": JEST},
        "bun run test",
        "bun install --frozen-lockfile",
    ),  # noqa: E501
    ("npm", {"package-lock.json": "", "package.json": JEST}, "npm test", "npm ci"),
    (
        "npm-workspaces",
        {"package-lock.json": "", "package.json": WS},
        "npm test --workspaces --if-present",
        "npm ci",
    ),  # noqa: E501
    ("npm-without-a-lockfile", {"package.json": JEST}, "npm test", "npm install --no-package-lock"),  # noqa: E501
    (
        "deno-jsonc",
        {"deno.jsonc": '{\n  // tasks\n  "tasks": {"test": "deno test -A",},\n}'},
        "deno task test",
        "deno install",
    ),  # noqa: E501
    ("uv", {"pyproject.toml": PYTEST, "uv.lock": ""}, "uv run pytest", "uv sync"),
    (
        "uv-pytest-in-an-extra",
        {"pyproject.toml": EXTRAS, "uv.lock": ""},
        "uv run pytest",
        "uv sync --all-extras",
    ),  # noqa: E501
    (
        "uv-django",
        {"pyproject.toml": DJANGO, "uv.lock": "", "manage.py": ""},
        "uv run python manage.py test",
        "uv sync",
    ),  # noqa: E501
    (
        "poetry",
        {"pyproject.toml": PYTEST, "poetry.lock": ""},
        "poetry run pytest",
        "poetry install",
    ),  # noqa: E501
    ("pdm", {"pyproject.toml": PYTEST, "pdm.lock": ""}, "pdm run pytest", "pdm install"),
    (
        "pipenv",
        {"Pipfile": "", "Pipfile.lock": "", "pytest.ini": ""},
        "pipenv run pytest",
        "pipenv sync --dev",
    ),  # noqa: E501
    (
        "hatch",
        {"pyproject.toml": "[project]\nname='x'\n[tool.hatch.envs.default]\n"},
        "hatch test",
        "hatch env create",
    ),  # noqa: E501
    ("pyproject-alone", {"pyproject.toml": PYTEST}, "uv run pytest", "uv sync"),
    (
        "poe-task",
        {"pyproject.toml": "[project]\nname='x'\n[tool.poe.tasks]\ntest = 'pytest -x'\n"},
        "uv run poe test",
        "uv sync",
    ),  # noqa: E501
    (
        "requirements-txt",
        {"requirements.txt": "pytest\n"},
        ".venv/bin/python -m pytest",
        "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
    ),  # noqa: E501
    ("tox", {"tox.ini": "[tox]\n"}, "tox", "tox --notest"),
    ("cargo", {"Cargo.toml": "[package]\n"}, "cargo test", "cargo fetch"),
    (
        "cargo-workspace",
        {"Cargo.toml": "[workspace]\nmembers = ['a']\n"},
        "cargo test --workspace",
        "cargo fetch",
    ),  # noqa: E501
    ("go", {"go.mod": "module x\n"}, "go test ./...", "go mod download"),
    (
        "gradle-wrapper",
        {"gradlew": "", "build.gradle.kts": ""},
        "./gradlew --no-daemon test",
        "./gradlew --no-daemon testClasses",
    ),  # noqa: E501
    (
        "android",
        {"gradlew": "", "settings.gradle": "include ':app'\n", "app/build.gradle": ANDROID},
        "./gradlew --no-daemon test",
        "./gradlew --no-daemon help",
    ),  # noqa: E501
    ("gradle", {"build.gradle": ""}, "gradle --no-daemon test", "gradle --no-daemon testClasses"),  # noqa: E501
    (
        "android-without-a-wrapper",
        {"settings.gradle": "include ':app'\n", "app/build.gradle": ANDROID},
        "gradle --no-daemon test",
        "gradle --no-daemon help",
    ),
    (
        "maven-wrapper",
        {"mvnw": "", "pom.xml": "<project/>"},
        "./mvnw -B test",
        "./mvnw -B test-compile",
    ),  # noqa: E501
    ("maven", {"pom.xml": "<project/>"}, "mvn -B test", "mvn -B test-compile"),
    ("sbt", {"build.sbt": ""}, "sbt test", "sbt update"),
    ("dotnet-by-glob", {"App.sln": "", "src/App/App.csproj": ""}, "dotnet test", "dotnet restore"),  # noqa: E501
    (
        "rails",
        {"bin/rails": "", "bin/setup": "", "Gemfile": ""},
        "bin/rails test",
        "bin/setup --skip-server",
    ),  # noqa: E501
    ("rspec", {"Gemfile": "", ".rspec": ""}, "bundle exec rspec", "bundle install"),
    (
        "rake",
        {"Gemfile": "", "Rakefile": "Rake::TestTask.new\n"},
        "bundle exec rake test",
        "bundle install",
    ),  # noqa: E501
    (
        "composer",
        {"composer.json": '{"scripts": {"test": "phpunit"}}'},
        "composer test",
        "composer install",
    ),  # noqa: E501
    ("mix", {"mix.exs": ""}, "mix test", "mix deps.get"),
    ("swift", {"Package.swift": ""}, "swift test", "swift package resolve"),
    (
        "flutter",
        {"pubspec.yaml": "dependencies:\n  flutter:\n    sdk: flutter\n"},
        "flutter test",
        "flutter pub get",
    ),  # noqa: E501
    ("dart", {"pubspec.yaml": "name: x\n"}, "dart test", "dart pub get"),
    ("zig", {"build.zig": ""}, "zig build test", "zig build --fetch"),
    ("stack", {"stack.yaml": ""}, "stack test", "stack build --only-dependencies --test"),
    (
        "cabal",
        {"x.cabal": ""},
        "cabal test",
        "cabal update && cabal build --only-dependencies --enable-tests",
    ),  # noqa: E501
    (
        "julia",
        {"Project.toml": 'name = "X"\nuuid = "1"\n'},
        "julia --project -e 'using Pkg; Pkg.test()'",
        "julia --project -e 'using Pkg; Pkg.instantiate()'",
    ),  # noqa: E501
    ("bazel", {"MODULE.bazel": ""}, "bazel test //...", "bazel fetch //..."),
    (
        "cmake",
        {"CMakeLists.txt": ""},
        "sh -c 'cmake --build build && ctest --test-dir build --output-on-failure'",
        "cmake -S . -B build",
    ),  # noqa: E501
    ("meson", {"meson.build": ""}, "meson test -C builddir", "meson setup builddir"),
]


@pytest.mark.parametrize(
    ("files", "test", "setup"), [k[1:] for k in KINDS], ids=[k[0] for k in KINDS]
)
def test_each_kind_of_repo_gets_its_own_commands(tmp_path, files, test, setup):
    p = _propose(_repo(tmp_path, files))
    assert (p.test_command, p.setup_command) == (test, setup)


def test_every_packaged_detector_has_a_case(tmp_path):
    """A detector added to the table without a case above is a proposal
    nothing has ever checked."""
    proposed = set()
    for n, (_, files, *_) in enumerate(KINDS):
        p = _propose(_repo(tmp_path / str(n), files))
        proposed |= {c["detector"] for c in p.candidates if c["chosen"]}
    for script in ("script/test", "bin/test"):  # runner scripts count only when executable
        p = _propose(_repo(tmp_path / script, {script: ""}, executable=(script,)))
        proposed |= {c["detector"] for c in p.candidates if c["chosen"]}
    assert {d.id for d in detect.load(None).detectors} - proposed == set()


def test_a_script_to_rule_them_all_counts_only_when_executable(tmp_path):
    files = {"script/test": "#!/bin/sh\n", "script/bootstrap": "#!/bin/sh\n"}
    p = _propose(_repo(tmp_path, files, executable=("script/test", "script/bootstrap")))
    assert (p.test_command, p.setup_command) == ("./script/test", "./script/bootstrap")
    assert _propose(_repo(tmp_path / "plain", files)).test_command is None


@pytest.mark.parametrize(
    ("justfile", "expected"),
    [
        ("test:\n    pytest\n", "just test"),
        ("set shell := ['zsh']\n[no-cd]\n@test *ARGS: build\n    pytest {{ARGS}}\n", "just test"),
        ("test-ui:\n    npm test\nlint:\n    ruff\n", "uv run pytest"),
        ("test := 'x'\n", "uv run pytest"),
    ],
    ids=[
        "a-test-recipe",
        "a-test-recipe-with-args-and-attributes",
        "no-test-recipe",
        "a-variable-named-test",
    ],
)
def test_a_runners_task_beats_the_toolchain_only_when_it_has_one(tmp_path, justfile, expected):
    """Kraft-reriq, Kraft-enc5z: a justfile wins only when `just test` would
    run something; one without a `test` recipe falls through to the manifest
    beside it."""
    p = _propose(_repo(tmp_path, {"Justfile": justfile, "pyproject.toml": PYTEST}))
    assert p.test_command == expected


def test_npms_placeholder_test_script_is_not_a_test_command(tmp_path):
    stub = '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}'
    p = _propose(_repo(tmp_path, {"package.json": stub, "package-lock.json": ""}))
    assert p.test_command is None
    assert p.setup_command == "npm ci"


def test_a_pyproject_holding_only_tool_settings_is_not_a_python_project(tmp_path):
    """Test and setup come from one reading of the repo: ruff's settings in a
    pyproject.toml beside a pnpm lockfile used to propose pytest with pnpm."""
    files = {"pyproject.toml": "[tool.ruff]\nline-length = 100\n", "pnpm-lock.yaml": ""}
    p = _propose(_repo(tmp_path, {**files, "package.json": JEST}))
    assert (p.test_command, p.setup_command) == ("pnpm test", "pnpm install --frozen-lockfile")


@pytest.mark.parametrize(
    "pyproject",
    [
        "[project]\nname = 'x'\n",
        "[project]\nname = 'x'\n[dependency-groups]\nlint = ['flake8-pytest-style']\n",
    ],
    ids=["nothing-names-it", "a-plugin-named-after-it"],
)
def test_pytest_is_proposed_only_with_evidence_of_pytest(tmp_path, pyproject):
    p = _propose(_repo(tmp_path, {"pyproject.toml": pyproject, "uv.lock": ""}))
    assert (p.test_command, p.setup_command) == (None, "uv sync")


def test_two_toolchains_at_one_root_are_both_prepared(tmp_path):
    """One test command (the table's order breaks the tie, the other is shown
    as a candidate) and both installs: each one's tests need its own."""
    files = {"pyproject.toml": PYTEST, "uv.lock": "", "package.json": JEST, "package-lock.json": ""}
    p = _propose(_repo(tmp_path, files))
    assert p.test_command == "npm test"
    assert [c["command"] for c in p.candidates if c["role"] == "test" and not c["chosen"]] == [
        "uv run pytest"
    ]
    assert p.setup_command == "npm ci && uv sync"


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


# ── scopes ──


def test_a_nested_project_is_a_scope_prepared_and_tested_from_its_directory(tmp_path):
    """Kraft-9wzy's layout. A scope's command runs from the worktree root
    without a shell, so a nested one is wrapped in `sh -c 'cd ...'`; the
    setup runs through a shell, so it gets a subshell."""
    files = {
        "pyproject.toml": PYTEST,
        "uv.lock": "",
        "frontend/package.json": JEST,
        "frontend/package-lock.json": "",
    }
    p = _propose(_repo(tmp_path, files))
    assert p.setup_command == "uv sync && (cd frontend && npm ci)"
    assert p.test_scopes[1] == {
        "paths": ["frontend/**"],
        "command": "sh -c 'cd frontend && npm test'",
    }
    assert "frontend/**" not in p.test_scopes[0]["paths"]
    assert {"pyproject.toml", "uv.lock", "calc.py"} <= set(p.test_scopes[0]["paths"])


def test_a_root_setup_recipe_prepares_the_whole_repo(tmp_path):
    files = {
        "justfile": "setup:\n  npm ci --prefix web\ntest:\n  pytest\n",
        "web/package.json": JEST,
        "web/package-lock.json": "",
    }
    p = _propose(_repo(tmp_path, files))
    assert p.setup_command == "just setup"
    assert p.test_scopes[1]["command"] == "sh -c 'cd web && npm test'"


def test_a_root_setup_recipe_prepares_only_the_directories_it_installs(tmp_path):
    """Kraft's own shape: `just setup` installs `frontend/`, and names
    `vscode` only in a comment and another recipe, so vscode's install is
    added beside it (spec A2)."""
    justfile = (
        "# setup installs the backend and the UI (not vscode)\n"
        "setup: deps\n"
        "    uv sync\n"
        "deps:\n"
        "    cd frontend && npm ci\n"
        "test:\n"
        "    pytest\n"
        "test-vscode:\n"
        "    cd vscode && npm ci && npm test\n"
    )
    files = {
        "justfile": justfile,
        "pyproject.toml": PYTEST,
        "uv.lock": "",
        "frontend/package.json": JEST,
        "frontend/package-lock.json": "",
        "vscode/package.json": JEST,
        "vscode/package-lock.json": "",
    }
    p = _propose(_repo(tmp_path, files))
    assert (p.test_command, p.setup_command) == ("just test", "just setup && (cd vscode && npm ci)")
    assert [s["command"] for s in p.test_scopes[1:]] == [
        "sh -c 'cd frontend && npm test'",
        "sh -c 'cd vscode && npm test'",
    ]


def test_a_workspace_member_with_its_own_lockfile_installs_on_its_own(tmp_path):
    files = {
        "pnpm-workspace.yaml": "packages: ['packages/*']\n",
        "pnpm-lock.yaml": "",
        "package.json": JEST,
        "packages/a/package.json": JEST,
        "docs/package.json": JEST,
        "docs/package-lock.json": "",
    }
    p = _propose(_repo(tmp_path, files))
    assert p.setup_command == "pnpm install --frozen-lockfile && (cd docs && npm ci)"
    assert [s["paths"] for s in p.test_scopes][1:] == [["docs/**"]]


def test_a_workspace_root_covers_its_members(tmp_path):
    files = {
        "pnpm-workspace.yaml": "packages: ['packages/*']\n",
        "pnpm-lock.yaml": "",
        "package.json": '{"private": true}',
        "packages/a/package.json": JEST,
        "apps/web/package.json": JEST,
    }
    p = _propose(_repo(tmp_path, files))
    assert p.test_scopes == [{"paths": ["**"], "command": "pnpm -r --if-present test"}]
    assert p.setup_command == "pnpm install --frozen-lockfile"


def test_a_project_two_levels_down_is_found_and_three_is_not(tmp_path):
    files = {"apps/web/go.mod": "module w\n", "a/b/c/go.mod": "module c\n"}
    p = _propose(_repo(tmp_path, files))
    assert [s["paths"] for s in p.test_scopes] == [["apps/web/**"]]


def test_ignored_untracked_and_conventional_non_project_directories_are_not_scopes(tmp_path):
    files = {
        ".gitignore": "vendored/\n",
        "vendored/go.mod": "module v\n",
        "examples/demo/go.mod": "module d\n",
        ".hidden/go.mod": "module h\n",
    }
    assert _propose(_repo(tmp_path, files)).test_scopes == []


def test_a_directory_with_tests_and_no_setup_leaves_setup_undecided(tmp_path):
    files = {"go.mod": "module x\n", "tools/Makefile": "test:\n\t./t\n"}
    p = _propose(_repo(tmp_path, files))
    assert p.setup_command is None
    assert p.missing_setup == ["tools"]


def test_a_given_test_command_replaces_the_roots_and_keeps_nested_scopes(tmp_path):
    """Kraft-k4mx: an override never suppresses nested probing. `""` says
    the root has no tests at all."""
    files = {"pyproject.toml": PYTEST, "web/package.json": JEST}
    p = _propose(_repo(tmp_path, files), test_command="just test")
    assert [s["command"] for s in p.test_scopes] == ["just test", "sh -c 'cd web && npm test'"]
    p = _propose(_repo(tmp_path / "none", files), test_command="")
    assert p.test_scopes == [{"paths": ["web/**"], "command": "sh -c 'cd web && npm test'"}]


@pytest.mark.parametrize(
    ("scopes", "expected"),
    [
        ([{"dir": "", "setup": "uv sync", "test": "t"}], "uv sync"),
        (
            [
                {"dir": "", "setup": "", "test": "t"},
                {"dir": "my app", "setup": "npm ci", "test": "t"},
            ],
            "(cd 'my app' && npm ci)",
        ),  # noqa: E501
        (
            [
                {"dir": "", "setup": "uv sync", "test": "t"},
                {"dir": "web", "setup": None, "test": "t"},
            ],
            None,
        ),  # noqa: E501
        (
            [
                {"dir": "", "setup": None, "test": None},
                {"dir": "web", "setup": "npm ci", "test": "t"},
            ],
            "(cd web && npm ci)",
        ),
        ([{"dir": "", "setup": "", "test": "t"}, {"dir": "web", "setup": "", "test": "t"}], ""),
        ([{"dir": "", "setup": None, "test": None}], None),
    ],
    ids=[
        "root-only",
        "a-directory-needing-quotes",
        "a-tested-scope-without-setup",
        "an-untested-root",
        "every-scope-declares-nothing",
        "nothing-declared",
    ],
)
def test_combine_setup(scopes, expected):
    assert detect.combine_setup(scopes) == expected


# ── the operator's detectors.yaml ──


def _own(tmp_path, text: str):
    templates = tmp_path / "templates"
    templates.mkdir(exist_ok=True)
    (templates / "detectors.yaml").write_text(text)
    return templates


def test_an_operators_detector_is_tried_before_the_packaged_ones(tmp_path):
    own = _own(
        tmp_path,
        "detectors:\n  - id: earthly\n    tier: runner\n    files: [Earthfile]\n"
        "    test: [{run: earthly +test}]\n",
    )
    repo = _repo(tmp_path, {"Earthfile": "", "Makefile": "test:\n"})
    assert _propose(repo, own).test_command == "earthly +test"
    assert _propose(repo).test_command == "make test"


def test_an_operators_detector_replaces_the_packaged_one_of_the_same_id(tmp_path):
    own = _own(
        tmp_path,
        "detectors:\n  - id: go\n    tier: toolchain\n    files: [go.mod]\n"
        "    test: [{run: gotestsum ./...}]\n",
    )
    repo = _repo(tmp_path, {"go.mod": "module x\n"})
    p = _propose(repo, own)
    assert (p.test_command, p.setup_command) == ("gotestsum ./...", None)


def test_disable_and_ignore_dirs_layer_onto_the_packaged_table(tmp_path):
    own = _own(tmp_path, "disable: [make]\nignore_dirs: [tools]\n")
    repo = _repo(tmp_path, {"Makefile": "test:\n", "tools/go.mod": "module t\n"})
    p = _propose(repo, own)
    assert (p.test_command, p.test_scopes) == (None, [])


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("detectors: [{id: x, tier: runner, files: [a], tasks: gradle}]\n", "unknown task reader"),
        (
            "detectors: [{id: x, tier: runner, files: [a], test: [{run: t, tasks: [t]}]}]\n",
            "no `tasks` reader",
        ),
        (
            "detectors: [{id: x, tier: toolchain, files: [a], contains: {a: '('}}]\n",
            "not a valid regex",
        ),
        ("detectorz: []\n", "detectors.yaml"),
        ("disable: [makefile]\n", "disable names no packaged detector: makefile"),
        (
            "detectors:\n  - {id: x, tier: runner, files: [a]}\n"
            "  - {id: x, tier: runner, files: [b]}\n",
            "detector id x is given twice",
        ),
    ],
    ids=[
        "an-unknown-reader",
        "tasks-without-a-reader",
        "a-broken-regex",
        "a-typo",
        "disabling-an-unknown-id",
        "a-duplicate-id",
    ],
)
def test_a_broken_operators_file_is_refused_naming_it(tmp_path, text, match):
    with pytest.raises(config.ConfigError, match=match):
        detect.load(_own(tmp_path, text))


# ── what is read ──


def test_an_uncommitted_file_is_not_evidence(tmp_path):
    """A work item's worktree holds the committed tree, so neither a file only
    in the working copy nor an uncommitted edit changes the proposal."""
    repo = _repo(tmp_path, {"package.json": JEST, "package-lock.json": ""})
    (repo / "Makefile").write_text("test:\n\tgo test ./...\n")
    (repo / "package.json").write_text("{}")
    p = _propose(repo)
    assert (p.test_command, p.ref) == ("npm test", "HEAD")


def test_origins_default_branch_is_read_before_a_local_commit(tmp_path):
    origin = _repo(tmp_path, {"go.mod": "module x\n"})
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    (clone / "Makefile").write_text("test:\n\tgo vet\n")
    commit_all(clone, "not pushed")
    p = _propose(clone)
    assert (p.test_command, p.ref) == ("go test ./...", "refs/remotes/origin/main")
    subprocess.run(["git", "-C", str(clone), "remote", "set-head", "origin", "-d"], check=True)
    assert _propose(clone).ref == "refs/remotes/origin/main", "no origin/HEAD: origin's main"


def test_a_repo_with_no_commit_is_read_from_its_working_copy(tmp_path):
    """Its own files only: nothing gitignored, and no symlink, which could
    point anywhere on the machine."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "justfile").write_text("test:\n  rm -rf /\n")
    repo = tmp_path / "fresh"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("ignored/\n")
    (repo / "go.mod").write_text("module x\n")
    (repo / "justfile").symlink_to(outside / "justfile")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "Cargo.toml").write_text("[package]\n")
    p = _propose(repo)
    assert (p.test_command, p.ref, p.test_scopes[0]["paths"]) == ("go test ./...", None, ["**"])


def test_a_listing_git_refuses_fails_the_probe_rather_than_reading_as_empty(tmp_path):
    """An empty proposal would save the repo disabled for "no test command
    found", which is not why."""
    repo = _repo(tmp_path, {"go.mod": "module x\n"})
    tree = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True
    ).stdout.strip()
    (repo / ".git" / "objects" / tree[:2] / tree[2:]).unlink()
    with pytest.raises(config.ConfigError, match="git ls-tree .* failed"):
        _propose(repo)


def test_a_listing_that_takes_too_long_fails_the_probe(tmp_path, monkeypatch):
    repo = _repo(tmp_path, {"go.mod": "module x\n"})
    monkeypatch.setattr(detect, "_LIST_TIMEOUT_S", 1e-6)
    with pytest.raises(config.ConfigError, match="took more than"):
        _propose(repo)


def test_a_file_is_read_up_to_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "_TEXT_LIMIT", 64)
    pad = "x" * 100
    repo = _repo(tmp_path, {"Makefile": f"# {pad}\ntest:\n\tgo test\n", "go.mod": "module x\n"})
    assert _propose(repo).test_command == "go test ./...", "the target past the cap is not read"


def test_a_rails_apps_bin_setup_is_run_without_starting_the_server(tmp_path):
    """Rails 7.1+'s bin/setup ends with `exec bin/dev`, which never exits."""
    files = {"bin/rails": "", "bin/setup": "", "Gemfile": ""}
    p = _propose(_repo(tmp_path, files, executable=("bin/rails", "bin/setup")))
    assert p.setup_command == "bin/setup --skip-server"
