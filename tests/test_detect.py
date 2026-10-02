"""`kraft.detect`: what `kraft repo connect` proposes, from what evidence.
CI and devcontainer files are `test_detect_ci.py`'s."""

from __future__ import annotations

import fnmatch
import shlex
import subprocess

import pytest
from support.harness import commit_all
from support.probe import JEST, PYTEST
from support.probe import chosen as _chosen
from support.probe import propose as _propose
from support.probe import repo_with as _repo

from kraft import config, detect

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
    ("pyproject-alone-stops", {"pyproject.toml": PYTEST}, None, None),
    ("poetry-without-a-lockfile-stops", {"pyproject.toml": "[tool.poetry]\n"}, None, None),
    ("pdm-without-a-lockfile-stops", {"pyproject.toml": "[project]\n[tool.pdm]\n"}, None, None),
    (
        "poe-task",
        {
            "pyproject.toml": "[project]\nname='x'\n[tool.poe.tasks]\ntest = 'pytest -x'\n",
            "uv.lock": "",
        },
        "uv run poe test",
        "uv sync",
    ),
    (
        "requirements-txt",
        {"requirements.txt": "pytest\n"},
        ".venv/bin/python -m pytest",
        "python3 -m venv .venv && echo '*' > .venv/.gitignore"
        " && .venv/bin/pip install -r requirements.txt",
    ),
    ("tox", {"tox.ini": "[tox]\n"}, "tox -e py", "tox -e py --notest"),
    (
        "tox-beside-a-lockless-pyproject",  # django, boto3
        {"tox.ini": "[tox]\n", "pyproject.toml": PYTEST, "requirements.txt": "pytest\n"},
        "tox -e py",
        "tox -e py --notest",
    ),
    ("hatch-toml", {"hatch.toml": "[envs.default]\n"}, "hatch test", "hatch env create"),
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
    (
        "maven-reactor",  # gson: test-compile does not build the modules' jars
        {"pom.xml": "<project><modules><module>a</module></modules></project>"},
        "mvn -B test",
        "mvn -B -DskipTests install",
    ),
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
        "rake-minitest",
        {"Gemfile": "", "Rakefile": "require 'minitest/test_task'\nMinitest::TestTask.create\n"},
        "bundle exec rake test",
        "bundle install",
    ),
    (
        "composer",
        {"composer.json": '{"scripts": {"test": "phpunit"}}', "composer.lock": "{}"},
        "composer test",
        "composer install",
    ),
    # `composer install` would write a composer.lock into every worktree.
    (
        "composer-without-a-lockfile",
        {"composer.json": '{"scripts": {"test": "x"}}'},
        "composer test",
        None,
    ),
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
        {"CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\nproject(x C)\n"},
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
        proposed |= {s["detector"] for s in p.stopped}
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
        ("test:\n    uv run pytest\n", "just test"),
        (
            "set shell := ['zsh']\n[no-cd]\n@test *ARGS: build\n    uv run pytest {{ARGS}}\n",
            "just test",
        ),
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
    p = _propose(_repo(tmp_path, {"Justfile": justfile, "pyproject.toml": PYTEST, "uv.lock": ""}))
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


_BOTH = {"pyproject.toml": PYTEST, "uv.lock": "", "package.json": JEST, "package-lock.json": ""}


def test_two_toolchains_at_one_root_are_both_tested_and_prepared(tmp_path):
    """tauri's pnpm and Cargo, phoenix's npm and mix: one family's tests
    passing says nothing of the other's, so the root runs both, in the
    table's order (Python first), and installs both."""
    p = _propose(_repo(tmp_path, _BOTH))
    assert p.test_command == "sh -c 'uv run pytest && npm test'"
    assert p.setup_command == "uv sync && npm ci"
    assert [c["command"] for c in p.candidates if not c["chosen"]] == []


def test_a_workspace_covers_only_the_members_its_test_runs(tmp_path):
    """A pnpm root's `pnpm test` ran no Cargo member's tests, yet the root
    claimed them as a Cargo workspace: now its test runs Cargo's too."""
    files = {
        "pnpm-lock.yaml": "",
        "package.json": '{"scripts": {"test": "vitest"}}',
        "Cargo.toml": "[workspace]\nmembers = ['crates/a']\n",
        "crates/a/Cargo.toml": "[package]\n",
    }
    p = _propose(_repo(tmp_path, files))
    assert p.test_scopes == [
        {"paths": ["**"], "command": "sh -c 'pnpm test && cargo test --workspace'"}
    ]
    assert p.setup_command == "pnpm install --frozen-lockfile && cargo fetch"


def test_a_workspace_root_whose_test_runs_another_family_leaves_its_members_a_scope(tmp_path):
    """Every packaged workspace has a test at its root, which the root runs;
    an operator's workspace detector need not. Its members then get scopes
    of their own: the root's `cargo test` cannot run them."""
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "detectors.yaml").write_text(
        "detectors:\n"
        "  - {id: zz-root, tier: toolchain, family: zz, files: [zz.ws],"
        " workspace: [{files: [zz.ws]}]}\n"
        "  - {id: zz, tier: toolchain, family: zz, files: [zz.pkg], test: [{run: zz test}]}\n"
    )
    files = {"Cargo.toml": "[package]\n", "zz.ws": "", "m/zz.pkg": ""}
    p = _propose(_repo(tmp_path / "repo", files), templates)
    assert [s["command"] for s in p.test_scopes] == ["cargo test", "sh -c 'cd m && zz test'"]


def test_a_runners_test_task_beside_two_toolchains_gets_both_installs(tmp_path):
    """Which toolchains `make test` needs is not known, so both install."""
    p = _propose(_repo(tmp_path, {**_BOTH, "Makefile": "test:\n\t./run-tests\n"}))
    assert (p.test_command, p.setup_command) == ("make test", "uv sync && npm ci")


#: A runner's test task, in a directory a uv.lock locks -> what is proposed.
_BARE = {
    "a-recipe-running-pytest": ({"justfile": "test:\n    pytest -x\n"}, "uv run just test"),
    "fastapis-script": (
        {"scripts/test.sh": "#!/usr/bin/env bash\nset -e\nexport X=1\npytest -n auto tests\n"},
        "uv run ./scripts/test.sh",
    ),
    "a-make-target-through-its-dependency": (
        {"Makefile": "test: cov\ncov:\n\t@python -m coverage run -m pytest\n"},
        "uv run make test",
    ),
    "a-taskfile-cmd": (
        {"Taskfile.yml": "version: '3'\ntasks:\n  test:\n    cmds:\n      - pytest\n"},
        "uv run task test",
    ),
    "a-recipe-already-through-uv": ({"justfile": "test:\n    uv run pytest\n"}, "just test"),
    "pytest-only-in-another-recipe": (
        {"justfile": "test:\n    ./run\nlint:\n    pytest --flake8\n"},
        "just test",
    ),
}


@pytest.mark.parametrize(("files", "expected"), _BARE.values(), ids=_BARE)
def test_a_runners_task_running_python_bare_runs_through_the_lockfile(tmp_path, files, expected):
    """fastapi's `./scripts/test.sh` runs `pytest`, which on a worker's PATH
    is whatever Python is installed, without the project's dependencies."""
    p = _propose(
        _repo(
            tmp_path,
            {**files, "pyproject.toml": PYTEST, "uv.lock": ""},
            ("scripts/test.sh",) if "scripts/test.sh" in files else (),
        )
    )
    assert p.test_command == expected


#: Files in a directory -> the (test, setup) a pyproject.toml with no lockfile
#: leaves there (#442): `uv sync` and `uv run` would each write a uv.lock.
_STOPS = {
    "a-pyproject-with-no-lockfile-gets-nothing": ({}, None, None),
    "a-lockfile-install-beside-it-still-stands": ({"package-lock.json": ""}, None, "npm ci"),
    "it-is-not-handed-go": ({"go.mod": "module x\n"}, None, None),
    "it-is-not-handed-npm": ({"package.json": JEST}, None, None),
    "a-runners-test-task-beside-it-is-kept": ({"Makefile": "test:\n\tpytest\n"}, "make test", None),
    "a-uv-lock-is-what-it-needs": ({"uv.lock": ""}, "uv run pytest", "uv sync"),
}


@pytest.mark.parametrize(("files", "test", "setup"), _STOPS.values(), ids=_STOPS)
def test_a_pyproject_with_no_lockfile_stops(tmp_path, files, test, setup):
    p = _propose(_repo(tmp_path, {"pyproject.toml": PYTEST, **files}))
    assert (p.test_command, p.setup_command) == (test, setup)
    assert bool(p.stopped) == (test is None)
    # R8a-04: what to do about it, not only why.
    assert all("commit a uv.lock (uv lock)" in s["reason"] for s in p.stopped)


@pytest.mark.parametrize(
    ("pyproject", "lock"),
    [
        ("[tool.poetry]\nname = 'x'\n", "Commit one (poetry lock)"),
        ("[project]\nname = 'x'\n[tool.pdm.dev-dependencies]\n", "Commit one (pdm lock)"),
        ("[project]\nname = 'x'\n[build-system]\n", "commit a uv.lock (uv lock)"),
    ],
    ids=["poetry", "pdm", "pip"],
)
def test_a_lockless_pyproject_is_told_to_commit_its_own_tools_lock(tmp_path, pyproject, lock):
    """A Poetry project told to `uv lock` would be read as a uv one from then
    on, which does not read Poetry's tables."""
    [stop] = _propose(_repo(tmp_path, {"pyproject.toml": pyproject})).stopped
    assert lock in stop["reason"]
    assert "--test-command" not in stop["reason"]  # the web shows it too


#: Layouts whose lockless pyproject.toml no root command can run (#442, #446):
#: a lone `web/` scope is what a change to the Python code would fail open to,
#: and a root `go test` or `npm test` is what it would select. Either passes
#: with the Python suite never run, so nothing is proposed.
_NOT_COVERED = {
    "at-the-root": {"pyproject.toml": PYTEST, "web/package.json": JEST},
    "one-level-down": {"backend/pyproject.toml": PYTEST, "web/package.json": JEST},
    "under-a-go-root": {"backend/pyproject.toml": PYTEST, "go.mod": "module x\n"},
    "under-an-npm-root": {"backend/pyproject.toml": PYTEST, "package.json": JEST},
}


@pytest.mark.parametrize("files", _NOT_COVERED.values(), ids=_NOT_COVERED)
def test_a_lockless_pyproject_no_root_command_can_cover_proposes_no_test_scope(tmp_path, files):
    p = _propose(_repo(tmp_path, {**files, "web/package-lock.json": ""}))
    assert (p.test_command, p.test_scopes) == (None, [])
    stopped = next(path for path in files if path.endswith("pyproject.toml"))
    assert p.stopped[0]["dir"] == (stopped.rpartition("/")[0] or ".")


#: Layout -> (the proposed test command, each scope's command, the directory
#: whose change must select the root scope by its paths rather than fail open).
_COVERED_BY_THE_ROOT = {
    "a-uv-workspace-member": (
        {"pyproject.toml": PYTEST, "uv.lock": "", "foo/pyproject.toml": PYTEST},
        ("uv run pytest", ["uv run pytest"], "foo"),
    ),
    "a-justfile-test-recipe-and-a-lockless-backend": (
        {"justfile": "test:\n    pytest\n", "backend/pyproject.toml": PYTEST},
        ("just test", ["just test"], "backend"),
    ),
    "a-justfile-test-recipe-a-lockless-backend-and-a-frontend": (
        {
            "justfile": "test:\n    pytest\n",
            "backend/pyproject.toml": PYTEST,
            "frontend/package.json": JEST,
        },
        ("just test", ["just test", "sh -c 'cd frontend && npm test'"], "backend"),
    ),
    "a-root-hatch-toml-and-a-lockless-backend": (  # pypa/hatch
        {"hatch.toml": "[envs.default]\n", "backend/pyproject.toml": PYTEST},
        ("hatch test", ["hatch test"], "backend"),
    ),
    "a-uv-lock-and-a-frontend": (
        {"pyproject.toml": PYTEST, "uv.lock": "", "src/app.py": "", "frontend/package.json": JEST},
        ("uv run pytest", ["uv run pytest", "sh -c 'cd frontend && npm test'"], "src"),
    ),
}


@pytest.mark.parametrize(
    ("files", "expected"), _COVERED_BY_THE_ROOT.values(), ids=_COVERED_BY_THE_ROOT
)
def test_a_lockless_pyproject_one_level_down_is_covered_by_the_root_command(
    tmp_path, files, expected
):
    """A uv workspace member never has a lock of its own, and `uv run` there
    writes none: the root's covers it. Any other lockless subdirectory is the
    root command's to test as well, when that command can run it (#446)."""
    from kraft.executor import dispatch

    command, scope_commands, covered = expected
    p = _propose(_repo(tmp_path, files))
    assert p.test_command == command
    assert [s["command"] for s in p.test_scopes] == scope_commands
    root_scope = p.test_scopes[0]
    assert any(fnmatch.fnmatchcase(f"{covered}/x.py", g) for g in root_scope["paths"])
    assert dispatch._matched_scopes(p.test_scopes, [f"{covered}/x.py"]) == [root_scope]


def test_a_runners_test_recipe_beside_a_root_pyproject_is_no_stop(tmp_path):
    """The stop is for a directory Kraft has no command for; a task runner's
    `test` recipe in that same directory is one."""
    files = {"justfile": "test:\n  pytest\n", "pyproject.toml": PYTEST, "x/go.mod": "module x\n"}
    p = _propose(_repo(tmp_path, files))
    assert (p.test_command, p.stopped) == ("just test", [])


def test_a_given_test_command_covers_a_lockless_pyproject_one_level_down(tmp_path):
    files = {"backend/pyproject.toml": PYTEST, "web/package.json": JEST}
    p = _propose(_repo(tmp_path, files), test_command="make test")
    root, nested = p.test_scopes
    assert ("backend/**" in root["paths"], root["command"]) == (True, "make test")
    assert nested == {"paths": ["web/**"], "command": "sh -c 'cd web && npm test'"}


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


def test_one_reader_serves_every_file_in_turn_past_a_capped_one(tmp_path, monkeypatch):
    """One `git cat-file --batch` reads them all: the rest of a file past
    the cap is skipped, never handed to the next read."""
    monkeypatch.setattr(detect, "_TEXT_LIMIT", 8)
    repo = _repo(tmp_path, {"big": "b" * 100, "small": "s\n"})
    index = detect._Index(repo)
    try:
        assert [index.text("big"), index.text("small")] == ["b" * 8, "s\n"]
    finally:
        index.close()


#: `python3 -m venv DIR` as Python 3.12 and older make one: no
#: `DIR/.gitignore`, which 3.13's `venv` writes and they do not. Its
#: `python` and `pytest` say they ran, in `DIR/ran`.
_PY312_VENV = """#!/bin/sh
[ "$1 $2" = "-m venv" ] || exit 2
mkdir -p "$3/bin" && echo "home = /usr/bin" > "$3/pyvenv.cfg"
for tool in pip python pytest; do
  printf '#!/bin/sh\\necho "$0 $*" >> "%s/ran"\\n' "$PWD/$3" > "$3/bin/$tool"
  chmod +x "$3/bin/$tool"
done
"""


def _py312(tmp_path) -> dict[str, str]:
    """An environment whose `python3` is `_PY312_VENV`, and with no `pytest`."""
    stub = tmp_path / "py312"
    stub.mkdir()
    (stub / "python3").write_text(_PY312_VENV)
    (stub / "python3").chmod(0o755)
    return {"PATH": f"{stub}:/usr/bin:/bin"}


@pytest.mark.parametrize("dev", ["requirements.txt", "requirements-dev.txt"])
def test_the_pip_setup_leaves_nothing_for_a_work_item_to_commit(tmp_path, dev):
    """The straggler sweep commits whatever git does not ignore: a `.venv/`
    the repo never ignored, made by a Python whose `venv` writes no
    `.gitignore`, rode into the merge request whole (1,023 files)."""
    repo = _repo(tmp_path, {dev: "pytest\n"})
    setup = _propose(repo).setup_command
    subprocess.run(["sh", "-c", setup], cwd=repo, env=_py312(tmp_path), check=True)
    assert (repo / ".venv" / "pyvenv.cfg").is_file()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True)
    assert status.stdout.decode() == ""


@pytest.mark.parametrize("d", ["", "api"], ids=["root", "nested"])
@pytest.mark.parametrize(
    ("files", "test"),
    [
        ({"script/test": "#!/bin/sh\npytest -q\n"}, None),
        (
            {
                ".github/workflows/ci.yml": "on: push\njobs:\n  t:\n    steps:\n"
                "      - run: python -m unittest discover -s tests\n"
            },
            ".venv/bin/python -m unittest discover -s tests",
        ),
    ],
    ids=["a-runners-task", "a-ci-line"],
)
def test_a_test_paired_with_the_pip_setup_runs_in_its_venv(tmp_path, d, files, test):
    """A runner's `script/test` or CI's `python -m unittest` beside a
    requirements.txt got the pip setup's `.venv`, and ran outside it, with
    none of what it installed. Run as dispatch runs a test command: split
    into argv, no shell, from the worktree's root."""
    if d and ".github/workflows/ci.yml" in files:
        files = {
            ".github/workflows/ci.yml": files[".github/workflows/ci.yml"].replace(
                "      - run:", f"      - working-directory: {d}\n        run:"
            )
        }
    else:
        files = {f"{d}/{k}" if d else k: v for k, v in files.items()}
    reqs = f"{d}/requirements.txt" if d else "requirements.txt"
    repo = _repo(
        tmp_path,
        {**files, reqs: "humanize\n"},
        executable=tuple(k for k in files if k.endswith("script/test")),
    )
    p = _propose(repo)
    if test is not None:
        assert detect.in_dir(d, test, shell=False) == p.test_command
    env = _py312(tmp_path)
    subprocess.run(["sh", "-c", p.setup_command], cwd=repo, env=env, check=True)
    subprocess.run(shlex.split(p.test_command), cwd=repo, env={"PATH": "/usr/bin:/bin"}, check=True)
    ran = (repo / d / ".venv" / "ran").read_text()
    assert ("pytest -q" if test is None else "python -m unittest") in ran


_VENV = "python3 -m venv .venv && echo '*' > .venv/.gitignore && .venv/bin/pip install"


@pytest.mark.parametrize(
    ("files", "installs", "source"),
    [
        (
            {"requirements.txt": "humanize\n", "requirements-dev.txt": "pytest\n"},
            "-r requirements.txt -r requirements-dev.txt",
            "requirements.txt + requirements-dev.txt",
        ),
        (
            {
                "requirements.txt": "humanize\n",
                "dev-requirements.txt": "-r requirements.txt\npytest\n",
            },
            "-r dev-requirements.txt",
            "dev-requirements.txt",
        ),
        ({"requirements_dev.txt": "pytest\n"}, "-r requirements_dev.txt", "requirements_dev.txt"),
        ({"requirements.txt": "pytest\n"}, "-r requirements.txt", "requirements.txt"),
    ],
    ids=["beside-requirements", "including-requirements", "dev-alone", "requirements-alone"],
)
def test_a_dev_requirements_file_is_installed_with_the_one_it_does_not_include(
    tmp_path, files, installs, source
):
    """requirements-dev.txt alone was installed, without the requirements.txt
    beside it, and the setup was said to come from requirements.txt."""
    p = _propose(_repo(tmp_path, files))
    assert p.setup_command == f"{_VENV} {installs}"
    assert _chosen(p, "setup")["source"] == source
