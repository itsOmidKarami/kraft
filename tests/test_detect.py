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


@pytest.mark.parametrize(
    ("files", "test", "setup"),
    [
        ({"justfile": "setup:\n  uv sync\ntest:\n  pytest\n"}, "just test", "just setup"),
        ({"Makefile": "deps:\n\tnpm ci\ntest: deps\n\tnpm test\n"}, "make test", "make deps"),
        ({"Makefile": ".PHONY: check\ncheck:\n\t./run\n"}, "make check", None),
        ({"Taskfile.yml": "version: '3'\ntasks:\n  test: {cmds: [go test]}\n"}, "task test", None),
        ({"mise.toml": '[tasks.test]\nrun = "pytest"\n'}, "mise run test", None),
        (
            {"pnpm-lock.yaml": "", "package.json": JEST},
            "pnpm test",
            "pnpm install --frozen-lockfile",
        ),
        (
            {"yarn.lock": "", ".yarnrc.yml": "nodeLinker: pnp\n", "package.json": JEST},
            "yarn test",
            "yarn install --immutable",
        ),  # noqa: E501
        ({"yarn.lock": "", "package.json": JEST}, "yarn test", "yarn install --frozen-lockfile"),
        ({"bun.lock": "", "package.json": JEST}, "bun run test", "bun install --frozen-lockfile"),
        ({"package-lock.json": "", "package.json": JEST}, "npm test", "npm ci"),
        ({"package.json": JEST}, "npm test", "npm install --no-package-lock"),
        ({"pyproject.toml": PYTEST, "uv.lock": ""}, "uv run pytest", "uv sync"),
        ({"pyproject.toml": PYTEST, "poetry.lock": ""}, "poetry run pytest", "poetry install"),
        ({"pyproject.toml": PYTEST, "pdm.lock": ""}, "pdm run pytest", "pdm install"),
        ({"pyproject.toml": PYTEST}, "uv run pytest", "uv sync"),
        (
            {"pyproject.toml": "[project]\nname='x'\n[tool.poe.tasks]\ntest = 'pytest -x'\n"},
            "uv run poe test",
            "uv sync",
        ),
        (
            {"requirements.txt": "pytest\n"},
            ".venv/bin/python -m pytest",
            "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
        ),
        ({"Cargo.toml": "[package]\n"}, "cargo test", "cargo fetch"),
        ({"go.mod": "module x\n"}, "go test ./...", "go mod download"),
        (
            {"gradlew": "", "build.gradle.kts": ""},
            "./gradlew --no-daemon test",
            "./gradlew --no-daemon testClasses",
        ),  # noqa: E501
        ({"pom.xml": "<project/>"}, "mvn -B test", "mvn -B test-compile"),
        ({"App.sln": "", "src/App/App.csproj": ""}, "dotnet test", "dotnet restore"),
        ({"Gemfile": "", ".rspec": ""}, "bundle exec rspec", "bundle install"),
        (
            {"Gemfile": "", "Rakefile": "Rake::TestTask.new\n"},
            "bundle exec rake test",
            "bundle install",
        ),
        (
            {"composer.json": '{"scripts": {"test": "phpunit"}}'},
            "composer test",
            "composer install",
        ),
        ({"mix.exs": ""}, "mix test", "mix deps.get"),
        ({"Package.swift": ""}, "swift test", "swift package resolve"),
        (
            {"deno.jsonc": '{\n  // tasks\n  "tasks": {"test": "deno test -A",},\n}'},
            "deno task test",
            "deno install",
        ),  # noqa: E501
        (
            {"CMakeLists.txt": ""},
            "sh -c 'cmake --build build && ctest --test-dir build --output-on-failure'",
            "cmake -S . -B build",
        ),  # noqa: E501
    ],
    ids=[
        "just",
        "make",
        "make-check",
        "taskfile",
        "mise",
        "pnpm",
        "yarn-berry",
        "yarn-classic",
        "bun",
        "npm",
        "npm-without-a-lockfile",
        "uv",
        "poetry",
        "pdm",
        "pyproject-alone",
        "poe-task",
        "requirements-txt",
        "cargo",
        "go",
        "gradle-wrapper",
        "maven",
        "dotnet-by-glob",
        "rspec",
        "rake",
        "composer",
        "mix",
        "swift",
        "deno-jsonc",
        "cmake",
    ],
)
def test_each_kind_of_repo_gets_its_own_commands(tmp_path, files, test, setup):
    p = _propose(_repo(tmp_path, files))
    assert (p.test_command, p.setup_command) == (test, setup)


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


def test_pytest_is_proposed_only_with_evidence_of_pytest(tmp_path):
    p = _propose(_repo(tmp_path, {"pyproject.toml": "[project]\nname = 'x'\n", "uv.lock": ""}))
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


def test_a_devcontainer_is_the_setup_of_last_resort(tmp_path):
    dc = '{\n  // set up\n  "postCreateCommand": ["./tools/bootstrap", "--dev"],\n}'
    p = _propose(_repo(tmp_path, {".devcontainer/devcontainer.json": dc, "Makefile": "test:\n"}))
    assert p.setup_command == "./tools/bootstrap --dev"
    with_lock = {".devcontainer/devcontainer.json": dc, "go.mod": "module x\n"}
    assert _propose(_repo(tmp_path / "go", with_lock)).setup_command == "go mod download"


def test_a_devcontainer_provisioning_the_container_is_not_proposed(tmp_path):
    dc = '{"postCreateCommand": "sudo apt-get install -y libpq-dev && npm ci"}'
    p = _propose(_repo(tmp_path, {".devcontainer.json": dc, "Makefile": "test:\n"}))
    assert p.setup_command is None


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
        ),  # noqa: E501
    ],
    ids=[
        "root-only",
        "a-directory-needing-quotes",
        "a-tested-scope-without-setup",
        "an-untested-root",
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
        ),  # noqa: E501
        (
            "detectors: [{id: x, tier: toolchain, files: [a], contains: {a: '('}}]\n",
            "not a valid regex",
        ),  # noqa: E501
        ("detectorz: []\n", "detectors.yaml"),
    ],
    ids=["an-unknown-reader", "tasks-without-a-reader", "a-broken-regex", "a-typo"],
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
    assert (p.test_command, p.ref) == ("go test ./...", "refs/remotes/origin/HEAD")


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
