"""Kraft's preToolUse entry in a cursor worktree (Kraft-4in7z): installed only
when there is policy to enforce, once however often the worktree launches,
beside a repo's own hooks, and never committed."""

import asyncio
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_hook_argv_never_carries_fail_closed():
    # Fail-closed is per session (env), so every launch writes the same entry.
    assert hi.hook_argv("cursor")[-3:] == ["admin", "permission-hook", "cursor"]


def test_installed_once_and_never_seen_by_git(tmp_path):
    main, wt = _worktree(tmp_path)
    hi.install_cursor_hook(wt, ARGV)
    hi.install_cursor_hook(wt, [*ARGV, "--fail-closed"])  # an older Kraft's entry
    hi.install_cursor_hook(wt, ARGV)  # relaunch
    assert [h["command"] for h in _pre_tool_use(wt)] == [hi.command_of(ARGV)]
    assert _pre_tool_use(wt)[0]["timeout"] == 10
    assert _git(wt, "status", "--porcelain", "--untracked-files=all") == ""
    assert (main / ".git/info/exclude").read_text().count(".cursor/hooks.json") == 1


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


def test_a_hooks_file_kraft_cannot_read_is_refused_by_name(tmp_path):
    _, wt = _worktree(tmp_path)
    (wt / ".cursor").mkdir()
    for body in ("{not json", "[]", '{"hooks": {"preToolUse": {"a": 1}}}'):
        (wt / ".cursor/hooks.json").write_text(body)
        with pytest.raises(hi.HookFileError, match=r"\.cursor/hooks\.json"):
            hi.install_cursor_hook(wt, ARGV)
        assert (wt / ".cursor/hooks.json").read_text() == body


# -- codex: per-launch -c flags, trusted from `codex app-server` (Kraft-4in7z.3)

FAKE_CODEX = [sys.executable, str(Path(__file__).parents[1] / "support/fake_codex_app_server.py")]
CODEX_ARGV = ["/py", "-m", "kraft", "admin", "permission-hook", "codex"]


@pytest.fixture
def fake_codex(monkeypatch, tmp_path):
    monkeypatch.setattr(hi, "_codex_flags", {})
    log = tmp_path / "spawns.log"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))

    def spawns():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return spawns


def test_codex_flags_install_kraft_hook_trusted_by_the_hash_codex_lists(fake_codex, tmp_path):
    flags = asyncio.run(hi.codex_hook_flags(FAKE_CODEX, CODEX_ARGV, tmp_path))
    digest = "sha256:" + hashlib.sha256(shlex.join(CODEX_ARGV).encode()).hexdigest()
    assert flags == (
        "-c",
        'hooks.PreToolUse=[{hooks=[{type="command",'
        'command="/py -m kraft admin permission-hook codex",timeout=10}]}]',
        "-c",
        'hooks.state={"/<session-flags>/config.toml:pre_tool_use:0:0"='
        f'{{trusted_hash="{digest}"}}}}',
    )
    # Listed once for the hash, once more to see codex trusts it.
    first, check = fake_codex()
    assert first == ["app-server", *flags[:2]] and check == ["app-server", *flags]


def test_codex_flags_are_cached_per_binary_and_command(fake_codex, tmp_path):
    for _ in range(3):
        asyncio.run(hi.codex_hook_flags(FAKE_CODEX, CODEX_ARGV, tmp_path))
    assert len(fake_codex()) == 2
    asyncio.run(hi.codex_hook_flags(FAKE_CODEX, [*CODEX_ARGV, "x"], tmp_path))
    assert len(fake_codex()) == 4


@pytest.mark.parametrize(
    ("mode", "match"),
    [
        ("exit", "exited before answering"),
        ("hang", "no hooks/list within 2s"),
        ("unlisted", "did not list"),
        ("distrust", "did not trust"),
    ],
)
def test_codex_that_cannot_trust_the_hook_is_an_error(
    fake_codex, monkeypatch, tmp_path, mode, match
):
    monkeypatch.setenv("FAKE_CODEX_MODE", mode)
    monkeypatch.setattr(hi, "CODEX_TRUST_TIMEOUT", 2.0)
    with pytest.raises(hi.CodexTrustError, match=match):
        asyncio.run(hi.codex_hook_flags(FAKE_CODEX, CODEX_ARGV, tmp_path))
    assert hi._codex_flags == {}


def test_a_codex_that_is_not_installed_is_an_error(fake_codex, tmp_path):
    with pytest.raises(hi.CodexTrustError, match="no-such-codex"):
        asyncio.run(hi.codex_hook_flags(["no-such-codex-xyz"], CODEX_ARGV, tmp_path))
