"""`kraft repo connect`: connecting a connected repo again, `--no-tests`, a
repo with no commit, and how connect names what it found."""

from __future__ import annotations

import asyncio
import subprocess

import pytest
from support.harness import commit_all

from kraft import cli, client

# `app` and `repo` fixtures: tests/conftest.py.


def test_no_tests_leaves_a_setup_found_undecided(app, capsys, repo):
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text(
        "on: push\njobs:\n  t:\n    steps:\n      - run: npm ci\n"
    )
    commit_all(repo)
    cli.main(["repo", "connect", str(repo), "--no-tests"])
    assert asyncio.run(client.repos())[0]["setup_command"] is None


def test_connecting_again_saves_what_a_lockfile_committed_since_proposes(app, capsys, repo):
    """ "Commit one (uv lock) and connect again" led nowhere: connecting
    again said "already connected" and saved nothing, so the repo stayed
    disabled with no commands until it was disconnected first."""
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n[tool.pytest.ini_options]\n")
    commit_all(repo)
    cli.main(["repo", "connect", str(repo)])
    assert "commit a uv.lock (uv lock) and connect again" in capsys.readouterr().out
    (repo / "uv.lock").write_text("version = 1\n")
    commit_all(repo)
    cli.main(["repo", "connect", str(repo)])
    out = capsys.readouterr().out
    assert "already connected" in out
    assert "test command: uv run pytest (from uv.lock)" in out
    assert "saved the test command, the setup command, enabled" in out
    [entry] = asyncio.run(client.repos())
    assert (entry["test_command"], entry["setup_command"], entry["enabled"]) == (
        "uv run pytest",
        "uv sync",
        True,
    )


def test_connecting_again_fills_only_what_is_undecided_and_asks_first(
    app, capsys, repo, monkeypatch
):
    """A test command given at the first connect stays; the setup command it
    left undeclared is proposed, and in a terminal saved only after a yes."""
    (repo / "Makefile").write_text("test:\n\tctest\n")
    commit_all(repo)
    cli.main(["repo", "connect", str(repo), "--test-command", "ctest -j4", "-y"])
    (repo / "Makefile").write_text("setup:\n\tcmake -B build\ntest:\n\tctest\n")
    commit_all(repo)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    answers = iter(["n", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    cli.main(["repo", "connect", str(repo)])
    assert "nothing saved" in capsys.readouterr().out
    assert asyncio.run(client.repos())[0]["setup_command"] is None
    cli.main(["repo", "connect", str(repo)])
    [entry] = asyncio.run(client.repos())
    assert (entry["test_command"], entry["setup_command"]) == ("ctest -j4", "make setup")


def test_no_tests_with_a_test_command_is_refused(app, capsys, repo):
    with pytest.raises(SystemExit) as caught:
        cli.main(["repo", "connect", str(repo), "--no-tests", "--test-command", "make test"])
    assert caught.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
    assert asyncio.run(client.repos()) == []


def test_the_prompt_says_when_there_is_nothing_to_pick(capsys, monkeypatch):
    """It offered "a number" with none listed, and asked about the root's
    tests on a monorepo whose scopes already have theirs."""
    from kraft.cli import repo as repo_cli

    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("asked"))
    assert repo_cli._pick("test", [], None, ["api", "web"]) is None
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "")
    assert repo_cli._pick("setup", [], None, ["web"]) is None
    assert "no setup command found for the root (web/ has its own)" in capsys.readouterr().out
    assert asked == ["  type one, - for none, or Enter leaves it unset: "]


def _cand(d, role, command, source, chosen=True):
    c = {"dir": d, "role": role, "command": command, "source": source, "chosen": chosen}
    return {**c, "tier": "toolchain"}


def test_connect_heads_a_monorepos_tests_as_scopes_each_with_its_source(capsys):
    """The first scope's `sh -c 'cd backend && …'` was headed "test command",
    as if it were the repo's."""
    from kraft.cli import repo as repo_cli

    repo_cli._say_connected(
        {
            "path": "/r",
            "test_command": "sh -c 'cd backend && uv run pytest'",
            "scopes": [
                {"dir": "backend", "test": "uv run pytest", "setup": "uv sync"},
                {"dir": "web", "test": "npm test", "setup": "npm ci"},
            ],
            "candidates": [
                _cand("backend", "test", "uv run pytest", "backend/uv.lock"),
                _cand("web", "test", "npm test", "web/package.json script `test`"),
            ],
        }
    )
    out = capsys.readouterr().out
    assert "test command:" not in out
    assert "test scopes:\n  backend/: uv run pytest (from backend/uv.lock)\n  web/: npm test" in out


@pytest.mark.parametrize(
    ("command", "candidates", "source"),
    [
        (
            "sh -c 'pnpm test && cargo test'",
            [
                _cand("", "test", "pnpm test", "pnpm-lock.yaml"),
                _cand("", "test", "cargo test", "Cargo.toml"),
            ],
            "(from pnpm-lock.yaml + Cargo.toml)",
        ),
        (
            "go test ./...",
            [
                _cand("", "test", "make test", "Makefile target `test`", chosen=False),
                _cand("", "test", "go test ./...", "go.mod", chosen=False),
            ],
            "(from go.mod)",
        ),
    ],
    ids=["combined", "picked-at-the-prompt"],
)
def test_connect_names_the_source_of_a_combined_or_picked_test(capsys, command, candidates, source):
    from kraft.cli import repo as repo_cli

    repo_cli._say_connected({"path": "/r", "test_command": command, "candidates": candidates})
    assert f"test command: {command} {source}" in capsys.readouterr().out


def test_a_setup_found_names_each_directory_it_runs_in(capsys):
    """`npm ci && npm ci found` lost the directories, and read like a
    command to copy that fails at the root."""
    from kraft.cli import repo as repo_cli

    repo_cli._say_connected(
        {
            "path": "/r",
            "setup_command": None,
            "missing_setup": ["."],
            "candidates": [
                _cand("web", "setup", "npm ci", "web/package-lock.json"),
                _cand("docs", "setup", "npm ci", "docs/package-lock.json"),
            ],
        }
    )
    out = capsys.readouterr().out
    assert "setup command: (cd web && npm ci) && (cd docs && npm ci) found, but the root" in out


def test_a_repo_with_no_commit_is_refused_until_it_has_one(app, tmp_path, capsys):
    """Connected enabled from its working copy, its items ran on an empty
    orphan branch, without the Makefile its `make test` was read from. MCP's
    `ensure_repo` and the web's first-run connect reach the same refusal."""
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=fresh, check=True)
    (fresh / "Makefile").write_text("test:\n\ttrue\n")
    with pytest.raises(SystemExit):
        cli.main(["repo", "connect", str(fresh)])
    assert "has no commit yet" in capsys.readouterr().err
    with pytest.raises(ValueError, match="commit its files, then connect it"):
        asyncio.run(client.ensure_repo(str(fresh)))
    assert asyncio.run(client.repos()) == []
    commit_all(fresh)
    cli.main(["repo", "connect", str(fresh)])
    assert "test command: make test" in capsys.readouterr().out
