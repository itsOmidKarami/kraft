"""repos, connect, path, open — the repo and worktree verbs."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from support.harness import commit_all, make_repo, make_repo_with_submodule

from kraft import cli, client

# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.transport.http()
# to the ASGI app with the lifespan entered per client.


def test_repos_is_empty_until_something_is_connected(app):
    assert asyncio.run(client.repos()) == []


def test_repos_lists_a_connected_repo(app, repo):
    asyncio.run(client.ensure_repo(str(repo)))
    listed = asyncio.run(client.repos())
    assert [entry["path"] for entry in listed] == [str(repo)]


def test_repo_list_reads_a_missing_enabled_key_as_enabled(app, capsys):
    """Ruling 212: an absent `enabled` means enabled. Written by hand, since
    `POST /repos` always writes an explicit value -- this is the shape of an
    entry that predates the field."""
    repos_yaml = Path(os.environ["KRAFT_TEMPLATES_DIR"]) / "repos.yaml"
    repos_yaml.write_text(yaml.safe_dump({"repos": [{"path": "/r", "name": "r"}]}))
    cli.main(["repo", "list"])
    row = next(line for line in capsys.readouterr().out.splitlines() if "/r" in line)
    assert "disabled" not in row
    assert "enabled" in row


def test_open_worktree_without_a_worktree_is_a_readable_404(app, make_item, repo):
    wid = make_item(repo)  # paused, never run: no worktree yet
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.open_worktree(wid))


def test_repos_marks_the_repo_you_are_standing_in(app, tmp_path, monkeypatch, capsys):
    # a wide terminal so the last column does not truncate the tmp path away
    monkeypatch.setenv("COLUMNS", "300")
    here = make_repo(tmp_path, name="here")
    there = make_repo(tmp_path, name="there")
    asyncio.run(client.ensure_repo(str(here)))
    asyncio.run(client.ensure_repo(str(there)))
    monkeypatch.chdir(here)
    cli.main(["repo", "list"])
    lines = capsys.readouterr().out.splitlines()
    marked = [line for line in lines if line.startswith("*")]
    assert len(marked) == 1
    assert str(here) in marked[0]


def test_repos_json_is_the_raw_list(app, capsys, repo):
    asyncio.run(client.ensure_repo(str(repo)))
    cli.main(["repo", "list", "--json"])
    assert json.loads(capsys.readouterr().out) == asyncio.run(client.repos())


def test_connect_defaults_to_the_cwd(app, monkeypatch, capsys, repo):
    monkeypatch.chdir(repo)
    cli.main(["repo", "connect"])
    assert str(repo) in capsys.readouterr().out
    assert [entry["path"] for entry in asyncio.run(client.repos())] == [str(repo)]


def test_connect_twice_is_fine_and_says_so(app, capsys, repo):
    cli.main(["repo", "connect", str(repo)])
    capsys.readouterr()
    cli.main(["repo", "connect", str(repo)])  # must not raise SystemExit
    assert "already connected" in capsys.readouterr().out


def test_connect_names_the_test_command_and_the_marker_it_came_from(app, capsys, repo):
    """Kraft-enc5z: the proposal is a guess a human should check, so connect
    says what it proposed and which file it read that from."""
    (repo / "justfile").write_text("test:\n    pytest\n")
    commit_all(repo)
    cli.main(["repo", "connect", str(repo)])
    assert "test command: just test (from justfile recipe `test`)" in capsys.readouterr().out


def test_connect_says_when_it_saved_the_repo_disabled(app, capsys, repo):
    """No marker, no test command: the repo lands disabled, and connect says
    why rather than leaving the first work item to find out."""
    cli.main(["repo", "connect", str(repo)])
    out = capsys.readouterr().out
    assert "saved disabled: no test command found" in out
    assert "setup command: none found" in out


def test_connect_says_when_it_found_no_setup_command(app, capsys, repo):
    """A Makefile with a `test` target and nothing else has a test command and
    no setup one: connect says so, naming the directory, since an undeclared
    setup_command stops the first item."""
    (repo / "Makefile").write_text("test:\n\tctest\n")
    commit_all(repo)
    cli.main(["repo", "connect", str(repo)])
    out = capsys.readouterr().out
    assert "test command: make test (from Makefile target `test`)" in out
    assert (
        "setup command: none found for the root" in out and '`""` if it needs no preparation' in out
    )
    assert "tick No setup needed under Templates › Repos" in out


def test_connect_names_the_setup_command_it_proposed(app, capsys, repo):
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n[tool.pytest.ini_options]\n")
    (repo / "uv.lock").write_text("version = 1\n")
    commit_all(repo)
    cli.main(["repo", "connect", str(repo)])
    out = capsys.readouterr().out
    assert "setup command: uv sync (from uv.lock)" in out
    assert "none found" not in out
    assert "saved disabled" not in out


def test_connect_takes_the_commands_given_on_the_command_line(app, capsys, repo):
    (repo / "Makefile").write_text("test:\n\tctest\n")
    cli.main(["repo", "connect", str(repo), "--test-command", "ctest -j4", "--setup-command", ""])
    out = capsys.readouterr().out
    assert "test command: ctest -j4" in out
    assert 'setup command: "" (nothing to prepare)' in out
    [entry] = asyncio.run(client.repos())
    assert (entry["test_command"], entry["setup_command"], entry["enabled"]) == (
        "ctest -j4",
        "",
        True,
    )


def test_connect_no_tests_declares_a_repo_with_none(app, capsys, repo):
    """Saved enabled, which connect says, and with nothing found to prepare
    a repo with no tests needs no preparation either: `--no-tests` alone left
    its setup undeclared, so its first item stopped before it started."""
    cli.main(["repo", "connect", str(repo), "--no-tests"])
    out = capsys.readouterr().out
    assert 'test command: "" (no tests)' in out
    assert "saved enabled: its work items pass verification without running a test" in out
    assert 'setup command: "" (nothing to prepare)' in out
    [entry] = asyncio.run(client.repos())
    assert (entry["test_command"], entry["setup_command"], entry["enabled"]) == ("", "", True)


def test_connect_flags_on_a_connected_repo_change_nothing_and_say_so(app, capsys, repo):
    cli.main(["repo", "connect", str(repo), "--test-command", "make test", "--setup-command", ""])
    cli.main(["repo", "connect", str(repo), "--test-command", "make check"])
    assert "its commands are unchanged" in capsys.readouterr().out
    [entry] = asyncio.run(client.repos())
    assert entry["test_command"] == "make test"


def test_connect_lists_the_commands_it_did_not_propose(app, capsys, repo):
    (repo / "Makefile").write_text("test:\n\tgo test ./...\n")
    (repo / "go.mod").write_text("module x\n")
    (repo / "x_test.go").write_text("")
    commit_all(repo)
    cli.main(["repo", "connect", str(repo)])
    out = capsys.readouterr().out
    assert "test command: make test (from Makefile target `test`)" in out
    assert "also found for test: go test ./... (go.mod)" in out


def test_connect_in_a_terminal_asks_which_command_to_use(app, capsys, repo, monkeypatch):
    (repo / "Makefile").write_text("test:\n\tgo test ./...\n")
    (repo / "go.mod").write_text("module x\n")
    (repo / "x_test.go").write_text("")
    commit_all(repo)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    answers = iter(["2", ""])  # the test command: go's; the setup: keep the proposal
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    cli.main(["repo", "connect", str(repo)])
    out = capsys.readouterr().out
    assert "1) make test    from Makefile target `test`  [proposed]" in out
    [entry] = asyncio.run(client.repos())
    assert (entry["test_command"], entry["setup_command"]) == ("go test ./...", "go mod download")


def test_a_number_with_no_option_is_asked_again_not_saved(app, capsys, repo, monkeypatch):
    (repo / "Makefile").write_text("test:\n\tgo test ./...\n")
    (repo / "go.mod").write_text("module x\n")
    (repo / "x_test.go").write_text("")
    commit_all(repo)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    answers = iter(["7", "2", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    cli.main(["repo", "connect", str(repo)])
    assert "7 is not one of the 2 listed" in capsys.readouterr().out
    [entry] = asyncio.run(client.repos())
    assert entry["test_command"] == "go test ./..."


def test_a_yes_keeps_the_proposal_and_a_no_asks_again(app, capsys, repo, monkeypatch):
    """A yes or no answers the prompt; neither is saved as the command."""
    (repo / "Makefile").write_text("test:\n\tgo test ./...\n")
    (repo / "go.mod").write_text("module x\n")
    (repo / "x_test.go").write_text("")
    commit_all(repo)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    answers = iter(["n", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    cli.main(["repo", "connect", str(repo)])
    assert "pick one by its number" in capsys.readouterr().out
    [entry] = asyncio.run(client.repos())
    assert entry["test_command"] == "make test"


def test_no_tests_at_the_prompt_is_confirmed_before_it_is_saved(app, capsys, repo, monkeypatch):
    """`-` saves `test_command: ""` and enables the repo with no tests run:
    a no at the confirmation asks again rather than saving it."""
    (repo / "Makefile").write_text("test:\n\tgo test ./...\n")
    (repo / "go.mod").write_text("module x\n")
    (repo / "x_test.go").write_text("")
    commit_all(repo)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    answers, asked = iter(["-", "", "-", "y"]), []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or next(answers))
    cli.main(["repo", "connect", str(repo)])
    assert ["Save it that way?" in a for a in asked] == [False, True, False, True]
    [entry] = asyncio.run(client.repos())
    assert entry["test_command"] == ""


def test_what_connect_prints_from_a_repo_is_shown_never_obeyed(capsys):
    """An ANSI sequence in a path or command could show a person one thing
    while another is saved."""
    from kraft.cli import repo as repo_cli

    repo_cli._say_connected(
        {"path": "/r\x1b[2K", "test_command": "npm test", "probe_failed": "boom\u202e"}
    )
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\u202e" not in out
    assert "connected: /r\\x1b[2K" in out
    assert "the probe failed: boom\\u202e" in out


def test_connect_says_a_setup_found_but_left_undecided(capsys):
    """Redis: `make bootstrap` was found, then dropped because src/ has
    nothing to prepare it, and connect said "none found"."""
    from kraft.cli import repo as repo_cli

    bootstrap = {"dir": "", "role": "setup", "command": "make bootstrap", "chosen": True}
    bootstrap |= {"tier": "runner", "source": "Makefile target `bootstrap`"}
    repo_cli._say_connected(
        {"path": "/r", "setup_command": None, "missing_setup": ["src"], "candidates": [bootstrap]}
    )
    out = capsys.readouterr().out
    assert "setup command: make bootstrap found, but src has nothing to prepare it" in out


def test_connect_does_not_list_what_it_saved_as_found_besides(capsys):
    """jest's chosen `yarn test` came back under "also found" from CI."""
    from kraft.cli import repo as repo_cli

    ci = {"dir": "", "role": "test", "command": "yarn test", "chosen": False, "tier": "ci"}
    web = {"dir": "web", "role": "setup", "command": "npm ci", "chosen": False, "tier": "toolchain"}
    other = {"dir": "", "role": "test", "command": "make test", "chosen": False, "tier": "runner"}
    for c in (ci, web, other):
        c["source"] = "x"
    repo_cli._say_connected(
        {
            "path": "/r",
            "test_command": "yarn test",
            "setup_command": "yarn install && (cd web && npm ci)",
            "candidates": [ci, web, other],
        }
    )
    out = capsys.readouterr().out
    assert "also found for test: make test (x)" in out
    assert "yarn test (x)" not in out and "npm ci (x)" not in out


def test_a_proposed_combination_is_offered_whole(capsys, monkeypatch):
    """Picking `mix deps.get` from a proposed `npm ci && mix deps.get`
    dropped `npm ci`: the combination is an option of its own."""
    from kraft.cli import repo as repo_cli

    cands = [
        {"dir": "", "role": "setup", "command": c, "chosen": True, "source": src}
        for c, src in (("npm ci", "package-lock.json"), ("mix deps.get", "mix.exs"))
    ]
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert repo_cli._pick("setup", cands, "npm ci && mix deps.get") == "npm ci && mix deps.get"
    out = capsys.readouterr().out
    assert "1) npm ci && mix deps.get    from package-lock.json + mix.exs  [proposed]" in out


def test_connect_names_a_program_this_machine_lacks(capsys):
    from kraft.cli import repo as repo_cli

    repo_cli._say_connected({"path": "/r", "missing_tools": [{"dir": "rt/deno", "tool": "deno"}]})
    assert "not installed here: deno, which rt/deno/'s commands run" in capsys.readouterr().out


def test_connect_names_origins_branch_whole(capsys):
    from kraft.cli import repo as repo_cli

    repo_cli._say_connected({"path": "/r", "read_from": "refs/remotes/origin/release/main"})
    assert "read from origin/release/main, where work items start" in capsys.readouterr().out


def test_connect_yes_takes_the_proposal_without_asking(app, capsys, repo, monkeypatch):
    (repo / "Makefile").write_text("test:\n\tgo test ./...\n")
    (repo / "go.mod").write_text("module x\n")
    (repo / "x_test.go").write_text("")
    commit_all(repo)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("asked"))
    cli.main(["repo", "connect", str(repo), "-y"])
    [entry] = asyncio.run(client.repos())
    assert entry["test_command"] == "make test"


@pytest.mark.parametrize(
    ("test_command", "code", "said"),
    [("true", None, "verify: passed"), ("false", 1, "verify: failed")],
    ids=["passing", "failing"],
)
def test_connect_verify_exits_by_whether_the_commands_passed(
    app, capsys, repo, test_command, code, said
):
    argv = ["repo", "connect", str(repo), "--test-command", test_command, "--setup-command", ""]
    try:
        cli.main([*argv, "--verify"])
        exited = None
    except SystemExit as caught:
        exited = caught.code
    assert exited == code
    assert said in capsys.readouterr().out


def test_connect_verify_json_says_why_it_failed(app, capsys, repo):
    """`--verify --json` exited 1 with nothing on stdout or stderr saying why."""
    argv = ["repo", "connect", str(repo), "--test-command", "false", "--setup-command", ""]
    with pytest.raises(SystemExit) as caught:
        cli.main([*argv, "--verify", "--json"])
    assert caught.value.code == 1
    verified = json.loads(capsys.readouterr().out)["verify"]
    assert verified["passed"] is False
    assert "  test [**]: false ..." in verified["output"]
    assert verified["output"][-1].startswith("verify: failed")


def test_reconnecting_survives_a_broken_detectors_file(app, capsys, repo, tmp_path):
    cli.main(["repo", "connect", str(repo)])
    templates = Path(os.environ["KRAFT_TEMPLATES_DIR"])
    (templates / "detectors.yaml").write_text("detectorz: []\n")
    cli.main(["repo", "connect", str(repo)])
    assert "already connected" in capsys.readouterr().out


def test_connect_saves_a_pyproject_without_uv_lock_disabled_and_says_why(app, capsys, repo):
    """`uv sync` and `uv run` would each write a `uv.lock` into the worktree,
    so neither is proposed: the repo lands disabled, and connect names the
    missing lockfile rather than leave the operator to guess."""
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    commit_all(repo)
    cli.main(["repo", "connect", str(repo)])
    out = capsys.readouterr().out
    assert "test command:" not in out
    assert "setup command: none found" in out
    assert "saved disabled: no test command found" in out
    assert "no test command proposed: the root is a pyproject.toml with no lockfile" in out


def test_connect_a_non_git_directory_surfaces_the_api_error(app, tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(SystemExit) as caught:
        cli.main(["repo", "connect", str(plain)])
    assert caught.value.code == 1
    assert "not a git repository" in capsys.readouterr().err


def test_path_prints_exactly_one_line(app, capsys, make_item, repo):
    wid = make_item(repo)
    cli.main(["repo", "path", wid])
    out = capsys.readouterr().out
    # consumed by cd "$(kraft path ID)": one line, no decoration, nothing else
    assert out.endswith("\n")
    assert out.count("\n") == 1
    assert out.strip() == asyncio.run(client.get_work_item(wid))["worktree_path"]


def test_cd_is_an_alias_for_path(app, capsys, make_item, repo):
    wid = make_item(repo)
    cli.main(["repo", "path", wid])
    expected = capsys.readouterr().out
    cli.main(["repo", "cd", wid])
    assert capsys.readouterr().out == expected


def test_path_defaults_to_the_resolved_work_item(app, monkeypatch, capsys, make_item, repo):
    wid = make_item(repo)
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    cli.main(["repo", "path"])
    assert wid in capsys.readouterr().out


def test_path_shell_prints_a_function(capsys):
    cli.main(["repo", "path", "--shell"])
    out = capsys.readouterr().out
    assert "kcd()" in out or "function" in out
    assert "kraft repo path" in out


def test_open_on_a_headless_server_is_a_kraft_message(app, monkeypatch, capsys, make_item, repo):
    wid = make_item(repo)

    async def fake_open(work_item_id=None, editor=None):
        raise ValueError("kraft 501: no editor available on the server for default")

    monkeypatch.setattr(client, "open_worktree", fake_open)
    with pytest.raises(SystemExit) as caught:
        cli.main(["repo", "open", wid])
    assert caught.value.code == 1
    assert "501" in capsys.readouterr().err


def test_open_passes_the_editor_through(app, monkeypatch, capsys, make_item, repo):
    wid = make_item(repo)
    seen = {}

    async def fake_open(work_item_id=None, editor=None):
        seen.update(work_item_id=work_item_id, editor=editor)
        return {"path": "/wt", "editor": editor or "system"}

    monkeypatch.setattr(client, "open_worktree", fake_open)
    cli.main(["repo", "open", wid, "--editor", "zed"])
    assert seen == {"work_item_id": wid, "editor": "zed"}


def test_path_rejects_json_rather_than_ignoring_it(app, capsys, make_item, repo):
    wid = make_item(repo)
    with pytest.raises(SystemExit) as caught:
        cli.main(["repo", "path", wid, "--json"])
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert "unrecognized arguments: --json" in captured.err
    # nothing on stdout: a caller that piped this must not get a path anyway
    assert captured.out == ""
    with pytest.raises(SystemExit):
        cli.main(["repo", "path", "--help"])
    assert "--json" not in capsys.readouterr().out


def test_repos_says_disabled_in_words_not_only_in_colour(app, tmp_path, monkeypatch, capsys):
    """pytest has no tty, so this is exactly the piped case: dim paint is gone
    and the word has to carry it (spec D §3, spec F §2.5)."""
    monkeypatch.setenv("COLUMNS", "300")
    on = make_repo(tmp_path, name="on")
    off = make_repo(tmp_path, name="off")

    async def go():
        async with client.transport.http() as http:
            r = await http.post("/api/repos", json={"path": str(on), "test_command": "pytest"})
            assert r.status_code == 201, r.text
            r = await http.post("/api/repos", json={"path": str(off), "test_command": "pytest"})
            assert r.status_code == 201, r.text
            response = await http.patch(
                "/api/repos", params={"path": str(off)}, json={"enabled": False}
            )
        assert response.status_code < 400, response.text

    asyncio.run(go())
    cli.main(["repo", "list"])
    lines = capsys.readouterr().out.splitlines()
    assert "disabled" in next(line for line in lines if str(off) in line)
    assert "enabled" in next(line for line in lines if str(on) in line)


def test_repos_hides_auto_connected_children_by_default(app, tmp_path, monkeypatch, capsys):
    """Kraft-jknn0: the CLI's answer to Settings' collapsed Detected section
    -- connecting a superproject must not dump every auto-connected,
    never-touched child into the plain table."""
    monkeypatch.setenv("COLUMNS", "300")
    root, _sub = make_repo_with_submodule(tmp_path)
    child = root / "repos" / "pkg"
    asyncio.run(client.ensure_repo(str(root)))

    cli.main(["repo", "list"])
    out = capsys.readouterr().out
    assert str(root) in out
    assert str(child) not in out
    assert "1 more detected, not managed" in out
    assert "--all" in out


def test_repos_all_shows_the_detected_children(app, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "300")
    root, _sub = make_repo_with_submodule(tmp_path)
    child = root / "repos" / "pkg"
    asyncio.run(client.ensure_repo(str(root)))

    cli.main(["repo", "list", "--all"])
    out = capsys.readouterr().out
    assert str(root) in out
    assert str(child) in out
    assert "detected" not in out


def test_repos_json_ignores_the_managed_filter(app, tmp_path, capsys):
    """`--json` is the raw API payload contract (common.emit's docstring) --
    it must not silently drop rows `--all` would otherwise be needed for."""
    root, _sub = make_repo_with_submodule(tmp_path)
    child = root / "repos" / "pkg"
    asyncio.run(client.ensure_repo(str(root)))

    cli.main(["repo", "list", "--json"])
    paths = {entry["path"] for entry in json.loads(capsys.readouterr().out)}
    assert paths == {str(root), str(child)}


def test_disconnect_removes_the_connected_repo(app, capsys, repo):
    asyncio.run(client.ensure_repo(str(repo)))
    cli.main(["repo", "disconnect", str(repo)])
    assert str(repo) in capsys.readouterr().out
    assert asyncio.run(client.repos()) == []


def test_disconnect_of_an_unconnected_path_is_a_readable_404(app, capsys, repo):
    """DELETE /repos answers 204 with no body, so the client must not try to
    parse one — and its 404 has to read as a sentence, like every other verb."""
    with pytest.raises(SystemExit) as caught:
        cli.main(["repo", "disconnect", str(repo)])
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "404" in err
    assert "not connected" in err
    assert "Traceback" not in err


def test_disconnect_from_inside_a_worktree_disconnects_the_repo(
    app, tmp_path, monkeypatch, capsys, repo
):
    """The symmetric half of Kraft-97e: after Task 1 the stored path is the main
    checkout, so sending the raw cwd from a worktree would 404. `disconnect_repo`
    probes first, the way `ensure_repo` does for its 409 branch."""

    asyncio.run(client.ensure_repo(str(repo)))
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(worktree), "-b", "wt-branch"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(worktree)
    cli.main(["repo", "disconnect"])
    assert asyncio.run(client.repos()) == []


def test_disconnect_removes_an_entry_registered_under_a_worktree_path(
    app, tmp_path, monkeypatch, capsys, repo
):
    """Kraft-7qgb, the gap Kraft-sws6 + Kraft-97e left between them. An entry
    written before 97e is keyed by a *worktree* path, and every CLI door probes
    now, so the probe rewrites the argument to the main checkout and no door can
    address the stale entry -- removing one needed a raw
    `curl -X DELETE /repos?path=...`.

    Written into repos.yaml by hand because that is the only way the state
    exists: `POST /repos` stores git's resolved toplevel, so the API cannot
    create this row any more.
    """

    asyncio.run(client.ensure_repo(str(repo)))
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(worktree), "-b", "wt-branch"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    repos_yaml = Path(os.environ["KRAFT_TEMPLATES_DIR"]) / "repos.yaml"
    on_disk = yaml.safe_load(repos_yaml.read_text())
    on_disk["repos"].append({"path": str(worktree), "name": "stale", "enabled": True})
    repos_yaml.write_text(yaml.safe_dump(on_disk))

    cli.main(["repo", "disconnect", str(worktree)])
    assert str(worktree) in capsys.readouterr().out
    assert [entry["path"] for entry in asyncio.run(client.repos())] == [str(repo)]


def test_repos_yaml_round_trips_a_test_command(tmp_path):
    from kraft import config

    p = tmp_path / "repos.yaml"
    config.save_repos(p, [{"path": "/r", "test_command": "just ci"}])
    (entry,) = config.load_repos(p)
    assert entry.test_command == "just ci"


@pytest.mark.parametrize("field", ["test_command", "test_scopes"])
def test_repos_yaml_defaults_an_absent_test_field_to_none(tmp_path, field):
    from kraft import config

    p = tmp_path / "repos.yaml"
    p.write_text("repos:\n  - path: /r\n")
    (entry,) = config.load_repos(p)
    assert getattr(entry, field) is None


@pytest.mark.parametrize(
    "tail",
    [
        "    test_command: 3\n",
        "    test_scopes:\n      - command: just test\n",
        "    test_scopes:\n      - paths: ['**']\n        command: ''\n",
        "    test_scopes: nope\n",
    ],
    ids=[
        "non-string-test-command",
        "scope-missing-paths",
        "scope-empty-command",
        "non-list-scopes",
    ],
)
def test_repos_yaml_rejects_a_malformed_test_field(tmp_path, tail):
    from kraft import config

    p = tmp_path / "repos.yaml"
    p.write_text("repos:\n  - path: /r\n" + tail)
    with pytest.raises(config.ConfigError):
        config.load_repos(p)


# ── test_scopes (Kraft-9wzy) ─────────────────────────────────────────────────


def test_repos_yaml_does_not_synthesize_test_scopes_from_test_command(tmp_path):
    """The wrap-into-`["**"]` happens where scopes are used (`executor.py`),
    not on load -- baking it into the loaded entry is what let a `PATCH
    /repos` re-save persist a stale synthesized scope past a later
    `test_command` edit (verify finding, Kraft-9wzy)."""
    from kraft import config

    p = tmp_path / "repos.yaml"
    config.save_repos(p, [{"path": "/r", "test_command": "just ci"}])
    (entry,) = config.load_repos(p)
    assert entry.test_scopes is None
    assert entry.test_command == "just ci"


def test_repos_yaml_test_command_edit_is_not_shadowed_by_a_stale_scope(tmp_path):
    """Regression for the exact sequence that used to go stale: load (which
    used to synthesize and persist a scope for the old command), edit
    `test_command`, save, reload."""
    from kraft import config

    p = tmp_path / "repos.yaml"
    config.save_repos(p, [{"path": "/r", "test_command": "just ci"}])
    (entry,) = config.load_repos(p)
    config.save_repos(p, [{**entry.model_dump(), "test_command": "just ci-v2"}])
    (reloaded,) = config.load_repos(p)
    assert reloaded.test_command == "just ci-v2"
    assert reloaded.test_scopes is None


def test_repos_yaml_round_trips_explicit_test_scopes(tmp_path):
    from kraft import config

    p = tmp_path / "repos.yaml"
    scopes = [
        {"paths": ["frontend/**"], "command": "just test-ui"},
        {"paths": ["*", "src/**", "!frontend/**"], "command": "just test"},
    ]
    config.save_repos(p, [{"path": "/r", "test_scopes": scopes}])
    (entry,) = config.load_repos(p)
    assert [s.model_dump() for s in entry.test_scopes] == scopes


def test_probe_repo_excludes_the_nested_scope_from_the_root_scope(repo):
    """Mirrors Kraft's own layout — root `pyproject.toml`, `package.json`
    under `frontend/` — the failure mode Kraft-9wzy names directly."""

    from kraft import config

    (repo / "pyproject.toml").write_text("[project]\nname='x'\n[tool.pytest.ini_options]\n")
    (repo / "uv.lock").write_text("version = 1\n")
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"scripts": {"test": "jest"}}')

    commit_all(repo)
    probed = config.probe_repo(repo)
    nested = next(s for s in probed["test_scopes"] if "npm test" in s["command"])
    assert nested["paths"] == ["frontend/**"]
    root = next(s for s in probed["test_scopes"] if s["command"] == "uv run pytest")
    assert "frontend" not in root["paths"]
    assert "pyproject.toml" in root["paths"]


def test_probe_repo_root_scope_globs_match_files_inside_its_directories(repo):
    """A bare directory name in `paths` (e.g. "src") never matches
    `fnmatch`-checked paths like "src/foo.py", so a backend-only diff failed
    open to every scope, nested ones included (verify finding, Kraft-9wzy).
    Root-level directories need the `/**` suffix; root-level files don't."""

    from kraft import config

    (repo / "pyproject.toml").write_text("[project]\nname='x'\n[tool.pytest.ini_options]\n")
    (repo / "uv.lock").write_text("version = 1\n")
    src = repo / "src"
    src.mkdir()
    (src / "app.py").write_text("")
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"scripts": {"test": "jest"}}')

    commit_all(repo)
    probed = config.probe_repo(repo)
    root = next(s for s in probed["test_scopes"] if s["command"] == "uv run pytest")
    assert "src/**" in root["paths"]
    assert "pyproject.toml" in root["paths"]
    src_pattern = next(p for p in root["paths"] if p.startswith("src"))
    assert fnmatch.fnmatchcase("src/kraft/config.py", src_pattern)
