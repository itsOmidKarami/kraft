"""`kraft repo connect`: connecting a connected repo again, `--no-tests`, a
repo with no commit, and how connect names what it found."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from support.addons import a_terminal, library_outcome, repo_with_library
from support.harness import commit_all, git

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


def test_connect_help_says_connecting_again_saves(capsys):
    """It said "(idempotent)", from 1.4, when connecting again did nothing;
    it now saves what the entry left undecided, and can enable the repo."""
    with pytest.raises(SystemExit):
        cli.main(["repo", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "connect a repo; again, to fill what it left undecided" in text
    assert "idempotent" not in text


def test_connecting_again_with_no_tests_says_what_it_saved(app, capsys, repo):
    """The probe answers `test_command: None` for `""`, so connecting a
    disabled repo again with `--no-tests` printed no test command line,
    while it saved `""` and enabled the repo."""
    cli.main(["repo", "connect", str(repo), "--setup-command", ""])
    assert asyncio.run(client.repos())[0]["enabled"] is False
    capsys.readouterr()
    cli.main(["repo", "connect", str(repo), "--no-tests"])
    out = capsys.readouterr().out
    assert 'test command: "" (no tests)' in out
    assert "saved enabled: its work items pass verification without running a test" in out
    [entry] = asyncio.run(client.repos())
    assert (entry["test_command"], entry["enabled"]) == ("", True)


def test_connect_says_the_commands_it_was_given_not_the_probes_advice(app, capsys, repo):
    """Connecting again with `--setup-command` printed the probe's "pass
    --setup-command" advice above "saved the setup command", and a first
    connect with `--test-command` still said no test command was proposed
    and to set one yourself."""
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    commit_all(repo)
    cli.main(["repo", "connect", str(repo), "--test-command", "make check", "-y"])
    first = capsys.readouterr().out
    assert "test command: make check" in first
    assert "no test command proposed: the root" not in first

    cli.main(["repo", "connect", str(repo), "--setup-command", "make deps", "-y"])
    again = capsys.readouterr().out
    assert "setup command: make deps (given)" in again
    assert "pass --setup-command" not in again
    assert "no test command proposed: the root" not in again
    assert asyncio.run(client.repos())[0]["setup_command"] == "make deps"


@pytest.mark.parametrize(
    "before",
    [pytest.param([], id="a-first-connect"), pytest.param(["--no-tests"], id="connected-already")],
)
def test_in_place_saves_worktrees_off(app, capsys, repo, before):
    """A reconnect fills only what is undecided (`_gains`), so `--in-place`
    on a connected repo is its own save."""
    if before:
        cli.main(["repo", "connect", str(repo), *before, "-y"])
    cli.main(["repo", "connect", str(repo), "--in-place", "--no-tests", "-y"])
    assert asyncio.run(client.repos())[0]["worktrees"] is False


@pytest.mark.parametrize(
    "flags, terminal, typed, outcome, says",
    [
        pytest.param(
            [], True, ["y", "n"], "current", "repo library app taken", id="a-terminal-is-asked"
        ),
        pytest.param(
            [],
            True,
            ["y", "y"],
            "current",
            "later text changes will be taken automatically",
            id="a-terminal-may-opt-in",
        ),
        pytest.param(
            ["-y"],
            True,
            [],
            "waiting",
            "run `kraft repo library` to review it",
            id="yes-never-accepts",
        ),
        pytest.param(
            [],
            False,
            [],
            "waiting",
            "run `kraft repo library` to review it",
            id="no-terminal-never-accepts",
        ),
        pytest.param(["--json"], True, [], "waiting", None, id="json-never-accepts"),
    ],
)
def test_connect_offers_the_repo_library(
    app, tmp_path, capsys, monkeypatch, flags, terminal, typed, outcome, says
):
    repo = repo_with_library(tmp_path)
    if terminal:
        a_terminal(monkeypatch, typed)

    cli.main(["repo", "connect", str(repo), "--no-tests", *flags])

    out = capsys.readouterr().out
    assert library_outcome(repo) == outcome
    if says:
        assert says in out


def test_a_library_that_cannot_be_checked_does_not_fail_the_connect(
    app, tmp_path, capsys, monkeypatch
):
    repo = repo_with_library(tmp_path)

    async def down(_path=None):
        raise ValueError("kraft 502: origin did not answer")

    monkeypatch.setattr(client, "repo_library", down)

    cli.main(["repo", "connect", str(repo), "--no-tests"])

    out = capsys.readouterr().out
    assert "connected:" in out
    assert "repo library not checked: kraft 502: origin did not answer" in out


def _origin(repo, url):
    """An origin (none for None), and a test command found, so the only
    questions are the forge's."""
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n[tool.pytest.ini_options]\n")
    (repo / "uv.lock").write_text("version = 1\n")
    commit_all(repo)
    if url:
        git(repo, "remote", "add", "origin", url)


def _forges_yaml():
    return Path(os.environ["KRAFT_CONFIG_DIR"]) / "forges.yaml"


def _accounts():
    return yaml.safe_load(_forges_yaml().read_text())["accounts"]


def _prompts(monkeypatch, answers):
    """A terminal; every prompt recorded, answered from `answers`."""
    seen, left = [], iter(answers)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": seen.append(prompt) or next(left))
    return seen


def _posted(monkeypatch):
    """Every body `POST /repos` was sent."""
    posted, post = [], client.transport._post

    async def spy(path, payload, **kw):
        if path == "/repos":
            posted.append(payload)
        return await post(path, payload, **kw)

    monkeypatch.setattr(client.transport, "_post", spy)
    return posted


@pytest.mark.parametrize(
    "remote, forge, project",
    [
        pytest.param("https://github.com/acme/app.git", "github", "acme/app", id="github-https"),
        pytest.param("git@github.com:acme/app.git", "github", "acme/app", id="github-ssh"),
        pytest.param(None, None, None, id="no-origin"),
    ],
)
def test_an_existing_user_sees_no_change(app, capsys, repo, monkeypatch, remote, forge, project):
    """The spec's anti-goal: no forges.yaml, and connect asks what it asked
    before the forge questions existed -- nothing here -- posts the body it
    posted, and writes no forges.yaml."""
    _origin(repo, remote)
    seen, posted = _prompts(monkeypatch, []), _posted(monkeypatch)
    cli.main(["repo", "connect", str(repo)])
    [entry] = asyncio.run(client.repos())
    assert (seen, posted) == ([], [{"path": str(repo)}])
    assert (entry["forge"], entry["project"], entry.get("forge_account")) == (forge, project, None)
    assert not _forges_yaml().exists()


_SELF_HOSTED = "git@code.corp.example:gitlab/team/app.git"


@pytest.mark.parametrize(
    "remote, web_url, project",
    [
        # An SSH path carries no web prefix: `gitlab` is offered, and stays a group.
        pytest.param(_SELF_HOSTED, "https://code.corp.example/gitlab", "gitlab/team/app", id="ssh"),
        pytest.param(
            "https://code.corp.example/gitlab/team/app.git",
            "https://code.corp.example/gitlab",
            "team/app",
            id="https-prefix-stripped",
        ),
        pytest.param(
            "https://code.corp.example:8443/gitlab/team/app.git",
            "https://code.corp.example:8443/gitlab",
            "team/app",
            id="https-port-kept",
        ),
    ],
)
def test_a_self_hosted_host_asks_forge_then_web_address(
    app, capsys, repo, monkeypatch, remote, web_url, project
):
    _origin(repo, remote)
    seen = _prompts(monkeypatch, ["gitlab", "2"])
    cli.main(["repo", "connect", str(repo)])
    assert seen == ["  which forge is it? [github/gitlab] ", "  number, or type an address [1] "]
    assert _accounts() == {
        "code.corp.example": {"forge": "gitlab", "web_url": web_url, "default_for_host": True}
    }
    [entry] = asyncio.run(client.repos())
    assert (entry["forge"], entry["project"], entry.get("forge_account")) == (
        "gitlab",
        project,
        None,
    )


_GH_DEFAULT = {"forge": "github", "web_url": "https://github.com", "default_for_host": True}
_CORP = {"forge": "gitlab", "web_url": "https://code.corp.example/gitlab"}


@pytest.mark.parametrize(
    "remote, flags, before, accounts, named",
    [
        pytest.param(
            _SELF_HOSTED,
            ["--forge", "gitlab", "--web-url", "https://code.corp.example/gitlab"],
            {},
            {"code.corp.example": {**_CORP, "default_for_host": True}},
            None,
            id="named-for-the-host",
        ),
        pytest.param(
            _SELF_HOSTED,
            ["--forge", "gitlab", "--web-url", "https://code.corp.example/gitlab"]
            + ["--forge-account", "corp"],
            {},
            {"corp": {**_CORP, "default_for_host": True}},
            "corp",
            id="named-by-flag",
        ),
        # An SSH alias's address is on another host: the account is no host's
        # default, and the repo names it.
        pytest.param(
            "git@github-work:acme/app.git",
            ["--forge", "github", "--web-url", "https://github.com"],
            {},
            {"github-work": {"forge": "github", "web_url": "https://github.com"}},
            "github-work",
            id="alias-names-a-non-default-account",
        ),
        pytest.param(
            _SELF_HOSTED,
            ["--forge", "gitlab", "--web-url", "https://git.corp.example"],
            {},
            {"code.corp.example": {"forge": "gitlab", "web_url": "https://git.corp.example"}},
            "code.corp.example",
            id="typed-url-on-another-host",
        ),
        pytest.param(
            "git@github-work:acme/app.git",
            ["--forge", "github", "--web-url", "https://github.com"],
            {"gh": _GH_DEFAULT},
            {
                "gh": _GH_DEFAULT,
                "github-work": {"forge": "github", "web_url": "https://github.com"},
            },
            "github-work",
            id="existing-github.com-default-untouched",
        ),
        # The alias's account from an earlier connect: reused, not a 409.
        pytest.param(
            "git@github-work:acme/app.git",
            ["--forge", "github"],
            {"github-work": {"forge": "github", "web_url": "https://github.com"}},
            {"github-work": {"forge": "github", "web_url": "https://github.com"}},
            "github-work",
            id="alias-account-reused",
        ),
        # An account that exists: its forge, and nothing asked or written.
        pytest.param(
            _SELF_HOSTED,
            ["--forge-account", "corp"],
            {"corp": _CORP},
            {"corp": _CORP},
            "corp",
            id="existing-account-named",
        ),
    ],
)
def test_flags_answer_the_questions(
    app, capsys, repo, monkeypatch, remote, flags, before, accounts, named
):
    _origin(repo, remote)
    for name, acct in before.items():
        asyncio.run(client.add_forge(name, **acct))
    seen = _prompts(monkeypatch, [])
    cli.main(["repo", "connect", str(repo), "-y", *flags])
    assert seen == [] and _accounts() == accounts
    [entry] = asyncio.run(client.repos())
    assert (entry["forge"], entry.get("forge_account")) == (
        accounts[named or "code.corp.example"]["forge"],
        named,
    )


@pytest.mark.parametrize(
    "worker",
    [
        pytest.param(False, id="terminal"),
        # A worker may not read forge accounts: the server takes the account's forge.
        pytest.param(True, id="worker"),
    ],
)
def test_an_existing_account_named_asks_nothing(app, capsys, repo, monkeypatch, worker):
    """...and the project loses the account's web prefix, as for a host's default."""
    _origin(repo, "https://code.corp.example/gitlab/team/app.git")
    asyncio.run(client.add_forge("corp", **_CORP))
    seen = _prompts(monkeypatch, [])
    if worker:
        monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "wi-1")

        async def refused():
            raise client.status_error(403, "a Kraft session cannot manage forge accounts")

        monkeypatch.setattr(client, "list_forges", refused)
    cli.main(["repo", "connect", str(repo), "--forge-account", "corp", *(["-y"] if worker else [])])
    [entry] = asyncio.run(client.repos())
    assert (seen, entry["forge"], entry["forge_account"], entry["project"]) == (
        [],
        "gitlab",
        "corp",
        "team/app",
    )


def test_a_host_with_an_account_asks_nothing(app, capsys, repo, monkeypatch):
    _origin(repo, _SELF_HOSTED)
    asyncio.run(client.add_forge("corp", "gitlab", "https://code.corp.example", None, True))
    seen = _prompts(monkeypatch, [])
    cli.main(["repo", "connect", str(repo)])
    assert seen == [] and asyncio.run(client.repos())[0]["forge"] == "gitlab"


@pytest.mark.parametrize(
    "flags, before, refusal",
    [
        pytest.param(
            ["--forge", "gitlab", "--web-url", "https://code.corp.example"],
            {"code.corp.example": {"forge": "gitlab", "web_url": "https://other.example"}},
            "already exists (gitlab, https://other.example); name this one with --forge-account",
            id="name-taken",
        ),
        pytest.param(
            ["--web-url", "https://code.corp.example"],
            {},
            "--web-url needs --forge",
            id="web-url-alone",
        ),
        pytest.param(
            ["--forge", "gitlab", "--api-url", "https://code.corp.example/api/v4"],
            {},
            "--api-url needs --web-url",
            id="api-url-without-web-url",
        ),
        pytest.param(
            ["--forge", "github", "--forge-account", "corp"],
            {"corp": _CORP},
            "forge account 'corp' is gitlab, not github",
            id="forge-differs-from-named-account",
        ),
    ],
)
def test_flags_that_cannot_hold_are_refused_before_connecting(
    app, capsys, repo, monkeypatch, flags, before, refusal
):
    _origin(repo, _SELF_HOSTED)
    for name, acct in before.items():
        asyncio.run(client.add_forge(name, **acct))
    seen = _prompts(monkeypatch, [])
    with pytest.raises(SystemExit) as caught:
        cli.main(["repo", "connect", str(repo), *flags])
    assert refusal in str(caught.value) and seen == []
    assert asyncio.run(client.repos()) == []
    assert (_accounts() if _forges_yaml().exists() else {}) == before


def test_an_unreadable_forges_yaml_is_said_before_asking(app, capsys, repo, monkeypatch):
    _origin(repo, _SELF_HOSTED)
    _forges_yaml().write_text("accounts: [\n")
    seen = _prompts(monkeypatch, [])
    with pytest.raises(SystemExit):
        cli.main(["repo", "connect", str(repo)])
    assert seen == [] and "forges.yaml" in capsys.readouterr().err


def test_a_failed_connect_keeps_the_account_it_wrote(app, capsys, repo, monkeypatch):
    _origin(repo, _SELF_HOSTED)

    async def refused(*a, **kw):
        raise client.status_error(400, "no")

    monkeypatch.setattr(client, "ensure_repo", refused)
    with pytest.raises(SystemExit):
        cli.main(
            ["repo", "connect", str(repo), "-y", "--forge", "gitlab"]
            + ["--web-url", "https://code.corp.example/gitlab"]
        )
    assert "the forge account stays in forges.yaml" in capsys.readouterr().out
    assert list(_accounts()) == ["code.corp.example"]


@pytest.mark.parametrize(
    "remote, again, flags, said",
    [
        pytest.param(
            _SELF_HOSTED, True, ["--forge", "gitlab"], "--forge ignored: ", id="connected"
        ),
        pytest.param(
            "git@github.com:acme/app.git",
            False,
            ["--forge", "gitlab"],
            "--forge ignored: github.com is github",
            id="host-says",
        ),
        pytest.param(
            "git@github.com:acme/app.git",
            False,
            ["--forge", "github", "--web-url", "https://github.com"],
            "--web-url ignored: github.com has its forge already",
            id="web-url-on-a-host-that-says",
        ),
        pytest.param(
            None,
            False,
            ["--forge", "github", "--web-url", "https://github.com"],
            "--web-url ignored: the repo has no origin",
            id="web-url-with-no-origin",
        ),
    ],
)
def test_an_ignored_forge_says_so(app, capsys, repo, remote, again, flags, said):
    _origin(repo, remote)
    if again:
        cli.main(["repo", "connect", str(repo), "-y", "--forge", "github"])
        capsys.readouterr()
    cli.main(["repo", "connect", str(repo), "-y", *flags])
    assert said in capsys.readouterr().out and not _forges_yaml().exists()
    assert asyncio.run(client.repos())[0]["forge"] == "github"


def test_forge_alone_beside_a_disagreeing_account_connects(app, capsys, repo):
    """--forge alone writes no account, so an account of the host's name is no conflict."""
    _origin(repo, "git@github-work:acme/app.git")
    theirs = {"forge": "github", "web_url": "https://github.com"}
    asyncio.run(client.add_forge("github-work", **theirs))
    cli.main(["repo", "connect", str(repo), "-y", "--forge", "gitlab"])
    [entry] = asyncio.run(client.repos())
    assert (entry["forge"], entry.get("forge_account")) == ("gitlab", None)
    assert _accounts() == {"github-work": theirs}


def test_yes_without_forge_asks_nothing_and_writes_no_account(app, capsys, repo):
    _origin(repo, _SELF_HOSTED)
    cli.main(["repo", "connect", str(repo), "-y"])
    assert asyncio.run(client.repos())[0]["forge"] is None and not _forges_yaml().exists()
