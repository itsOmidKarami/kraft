"""Kraft's preToolUse entry in a cursor worktree (Kraft-4in7z): installed only
when there is policy to enforce, once however often the worktree launches,
beside a repo's own hooks, and never committed."""

import json
import subprocess

from kraft.adapters import hook_install as hi

ARGV = ["/py", "-m", "kraft", "admin", "permission-hook", "cursor"]


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def _worktree(tmp_path):
    main = tmp_path / "main"
    _git(tmp_path, "init", "-q", str(main))
    _git(
        main,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "i",
    )
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(wt), "-b", "w")
    return main, wt


def _pre_tool_use(wt):
    return json.loads((wt / ".cursor/hooks.json").read_text())["hooks"]["preToolUse"]


def test_needs_a_hook_only_when_there_is_policy_to_enforce():
    assert not hi.needs_hook(None, (), ("git-commit",))
    assert hi.needs_hook(None, ("Bash",), ())
    assert hi.needs_hook(("Read",), (), ())
    assert hi.needs_hook((), (), ())  # allow nothing is still a list
    assert hi.needs_hook(None, (), ("git-push",))


def test_hook_argv_is_fail_closed_only_when_asked():
    assert hi.hook_argv("cursor", fail_closed=True)[-2:] == ["cursor", "--fail-closed"]
    assert hi.hook_argv("cursor", fail_closed=False)[-1] == "cursor"


def test_installed_once_and_never_seen_by_git(tmp_path):
    main, wt = _worktree(tmp_path)
    hi.install_cursor_hook(wt, ARGV)
    hi.install_cursor_hook(wt, [*ARGV, "--fail-closed"])  # relaunch, tighter policy
    assert [h["command"] for h in _pre_tool_use(wt)] == [hi.command_of([*ARGV, "--fail-closed"])]
    assert _pre_tool_use(wt)[0]["timeout"] == 10
    assert _git(wt, "status", "--porcelain", "--untracked-files=all") == ""
    assert (main / ".git/info/exclude").read_text().count(".cursor/hooks.json") == 1


def test_a_relaunch_with_nothing_to_enforce_drops_kraft_entry(tmp_path):
    _, wt = _worktree(tmp_path)
    hi.install_cursor_hook(wt, ARGV)
    hi.install_cursor_hook(wt, None)
    assert _pre_tool_use(wt) == []


def test_nothing_to_enforce_writes_nothing(tmp_path):
    main, wt = _worktree(tmp_path)
    hi.install_cursor_hook(wt, None)
    assert not (wt / ".cursor").exists()
    assert ".cursor" not in (main / ".git/info/exclude").read_text()


def test_a_tracked_hooks_file_keeps_its_own_hooks_and_is_not_staged(tmp_path):
    _, wt = _worktree(tmp_path)
    (wt / ".cursor").mkdir()
    own = {"command": "./repo-gate.sh"}
    (wt / ".cursor/hooks.json").write_text(
        json.dumps({"version": 1, "hooks": {"stop": [{"command": "x"}], "preToolUse": [own]}})
    )
    _git(wt, "add", ".cursor/hooks.json")
    _git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "repo hooks")
    hi.install_cursor_hook(wt, ARGV)
    hi.install_cursor_hook(wt, ARGV)
    merged = json.loads((wt / ".cursor/hooks.json").read_text())["hooks"]
    assert merged["stop"] == [{"command": "x"}]
    assert merged["preToolUse"] == [own, {"command": hi.command_of(ARGV), "timeout": 10}]
    _git(wt, "add", "-A")
    assert _git(wt, "diff", "--cached", "--name-only") == ""
