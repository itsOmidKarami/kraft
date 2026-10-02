"""`kraft.detect`: which directories are scopes of their own, how each is
prepared, and when a repo's proposal stops rather than test only part of it.
Split from `test_detect.py` for its line budget."""

from __future__ import annotations

import shlex

import pytest
from support.probe import JEST, PYTEST
from support.probe import propose as _propose
from support.probe import repo_with as _repo

from kraft import detect


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
        "    uv run pytest\n"
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
        "site/package.json": JEST,
        "site/package-lock.json": "",
    }
    p = _propose(_repo(tmp_path, files))
    assert p.setup_command == "pnpm install --frozen-lockfile && (cd site && npm ci)"
    assert [s["paths"] for s in p.test_scopes][1:] == [["site/**"]]


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
        "deps/hiredis/Makefile": "test:\n\t./run\n",  # redis vendors its dependencies here
    }
    assert _propose(_repo(tmp_path, files)).test_scopes == []


def test_a_non_project_directory_is_skipped_whatever_its_case(tmp_path):
    """hono's benchmarks/routers-deno joined every item's setup (and failed
    on a machine with no deno), fmt's support/ an Android build, and
    swift-argument-parser's Sources/ and Tests/ CMake fragments became
    scopes of their own."""
    files = {
        "go.mod": "module x\n",
        "benchmarks/routers-deno/deno.json": "{}",
        "Benchmarks/x/go.mod": "module b\n",
        "support/build.gradle": "",
        "Tests/go.mod": "module t\n",
        "Sources/CMakeLists.txt": "add_library(x x.c)\n",
        "docs/Makefile": "check:\n\tsphinx-build -W . _build\n",  # django's docs
    }
    p = _propose(_repo(tmp_path, files))
    assert (p.test_scopes, p.setup_command) == (
        [{"paths": ["**"], "command": "go test ./..."}],
        "go mod download",
    )


def test_a_ci_working_directory_is_held_to_the_scope_rules(tmp_path):
    """vscode's CI ran in src/vs/sessions/test/e2e, four levels down, under
    a `test` directory: it became a scope."""
    step = "      - run: npm test\n        working-directory: src/a/test/e2e\n"
    files = {
        "go.mod": "module x\n",
        "src/a/test/e2e/x.js": "",
        ".github/workflows/ci.yml": f"jobs:\n  t:\n    steps:\n{step}",
    }
    p = _propose(_repo(tmp_path, files))
    assert [s["command"] for s in p.test_scopes] == ["go test ./..."]


def test_a_deno_workspace_root_tests_its_members(tmp_path):
    """deno's std library: 43 member scopes, before its workspace was read."""
    files = {
        "deno.json": '{"workspace": ["./bytes", "./path"]}',
        "bytes/deno.json": '{"name": "@std/bytes"}',
        "path/deno.json": '{"name": "@std/path"}',
    }
    p = _propose(_repo(tmp_path, files))
    assert p.test_scopes == [{"paths": ["**"], "command": "deno test"}]


#: Sinatra's shape: its core suite at the root, two gems beside it.
_SINATRA = {
    "Gemfile": "",
    "lib/sinatra.rb": "",
    "sinatra-contrib/Gemfile": "",
    "sinatra-contrib/.rspec": "",
    "rack-protection/Gemfile": "",
    "rack-protection/.rspec": "",
}


def test_a_root_project_with_no_test_found_stops_rather_than_test_only_below_it(tmp_path):
    """A change to `lib/` would select the two gems' scopes and pass on
    them, the root's own suite never run."""
    p = _propose(_repo(tmp_path, {**_SINATRA, "Rakefile": "task :release\n"}))
    assert (p.test_command, p.test_scopes) == (None, [])
    [stop] = p.stopped
    assert stop["dir"] == "." and "Gemfile" in stop["reason"]
    assert "rack-protection/, sinatra-contrib/" in stop["reason"]
    given = _propose(_repo(tmp_path / "given", {**_SINATRA}), test_command="")
    assert given.stopped == []
    assert [s["command"] for s in given.test_scopes] == [
        "sh -c 'cd rack-protection && bundle exec rspec'",
        "sh -c 'cd sinatra-contrib && bundle exec rspec'",
    ]


def test_a_root_project_with_its_test_found_keeps_the_scopes_below_it(tmp_path):
    rakefile = "require 'minitest/test_task'\nMinitest::TestTask.create\n"
    p = _propose(_repo(tmp_path, {**_SINATRA, "Rakefile": rakefile}))
    assert [s["command"] for s in p.test_scopes] == [
        "bundle exec rake test",
        "sh -c 'cd rack-protection && bundle exec rspec'",
        "sh -c 'cd sinatra-contrib && bundle exec rspec'",
    ]
    assert p.stopped == []


def test_a_root_with_no_project_of_its_own_is_tested_by_its_scopes(tmp_path):
    """A monorepo of projects side by side: nothing at the root to test."""
    files = {"README.md": "", "api/go.mod": "module a\n", "web/package.json": JEST}
    p = _propose(_repo(tmp_path, files))
    assert ([s["paths"] for s in p.test_scopes], p.stopped) == ([["api/**"], ["web/**"]], [])


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


#: A project one level down, with nothing at the root -> the argv its scope's
#: command splits into (#453): it runs from the repository root, so it moves
#: into its own directory first.
_NESTED_PROJECTS = {
    "npm": ({"web app/package.json": JEST}, "cd 'web app' && npm test"),
    "uv": (
        {"backend/pyproject.toml": PYTEST, "backend/uv.lock": ""},
        "cd backend && uv run pytest",
    ),
    "cargo": ({"crate/Cargo.toml": ""}, "cd crate && cargo test"),
    "go": ({"svc/go.mod": "module svc\n"}, "cd svc && go test ./..."),
    "just": ({"tools/Justfile": "test:\n    true\n"}, "cd tools && just test"),
}


@pytest.mark.parametrize(("files", "script"), _NESTED_PROJECTS.values(), ids=_NESTED_PROJECTS)
def test_a_nested_scopes_command_runs_in_its_own_directory(tmp_path, files, script):
    """Verify runs every scope's command from the worktree root, without a
    shell, so a bare `npm test` there looks for its project at the root. A
    name with a space stays one argument to `cd`."""
    p = _propose(_repo(tmp_path, files))
    (directory,) = {path.rpartition("/")[0] for path in files}
    [scope] = p.test_scopes
    assert (scope["paths"], shlex.split(scope["command"])) == (
        [f"{directory}/**"],
        ["sh", "-c", script],
    )


def test_a_program_this_machine_lacks_is_named_before_an_item_fails_on_it(tmp_path):
    """hono's runtime-tests/deno joined every item's setup on a machine with
    no deno: verify failed on `deno: not found`. The scope stays (its tests
    are real), and connect says so."""
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "detectors.yaml").write_text(
        "detectors:\n  - id: zz\n    tier: toolchain\n    family: zz\n    files: [zz.pkg]\n"
        "    test: [{run: \"sh -c 'A=1 zz-not-installed test && go vet'\"}]\n"
        "    setup: [{run: ./zz-setup}]\n"
    )
    files = {"go.mod": "module x\n", "rt/zz.pkg": ""}
    p = _propose(_repo(tmp_path / "repo", files), templates)
    assert p.missing_tools == [{"dir": "rt", "tool": "zz-not-installed"}]
