"""`kraft.adapters.agent` on codex (Kraft-4in7z.3): policy with something to
enforce puts Kraft's PreToolUse hook on the command line, trusted for that
launch by the hash `codex app-server` lists -- on a resume too, since codex
reads its config again per invocation. No trust, no launch."""

import shlex
import sys
from pathlib import Path

import pytest

from kraft.adapters import hook_install as hi
from kraft.adapters.agent import LaunchRefused
from kraft.paths import RunDirs

FAKE = shlex.join(
    [sys.executable, str(Path(__file__).parents[1] / "support/fake_codex_app_server.py")]
)
FAIL_CLOSED = "KRAFT_PERMISSION_FAIL_CLOSED"


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setattr(hi, "_codex_flags", {})


def _codex(run, tmp_path, **kw):
    return run(
        harness="codex",
        command=FAKE,
        cwd=str(tmp_path),
        run_dirs=RunDirs(base=tmp_path / "run"),
        **kw,
    )


def _hook_flags(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c" and cmd[i + 1].startswith("hooks.")]


def test_a_codex_launch_with_deny_tools_carries_its_trusted_hook(run, tmp_path):
    seen = _codex(run, tmp_path, deny_tools=("Bash",))
    cmd = seen["cmd"]
    hook, state = _hook_flags(cmd)
    assert cmd[2:7] == ["exec", "-c", hook, "-c", state]
    assert hook.startswith("hooks.PreToolUse=") and "permission-hook codex" in hook
    assert state.startswith("hooks.state=") and "trusted_hash=" in state
    assert "Bash" not in cmd
    assert seen["env"]["KRAFT_WORKTREE"] == str(tmp_path)
    assert FAIL_CLOSED not in seen["env"]


def test_a_codex_launch_under_an_allowlist_runs_fail_closed(run, tmp_path):
    seen = _codex(run, tmp_path, allowed_tools=("Read", "WebSearch"))
    assert len(_hook_flags(seen["cmd"])) == 2
    assert seen["env"][FAIL_CLOSED] == "1"


def test_a_codex_resume_keeps_the_hook(run, tmp_path):
    cmd = _codex(run, tmp_path, deny_tools=("Bash",), resume_session_id="thread-1")["cmd"]
    hook, state = _hook_flags(cmd)
    assert cmd[2:9] == ["exec", "resume", "thread-1", "-c", hook, "-c", state]


def test_a_codex_launch_with_nothing_to_enforce_gets_no_hook(run, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "exit")  # would refuse, were it asked
    seen = _codex(run, tmp_path, grants=("git-commit",))
    assert _hook_flags(seen["cmd"]) == []
    assert "KRAFT_WORKTREE" not in seen["env"]


def test_a_codex_launch_whose_hook_codex_will_not_trust_is_refused(run, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "distrust")
    with pytest.raises(LaunchRefused, match="permission hook.*did not trust"):
        _codex(run, tmp_path, grants=("git-push",))


@pytest.mark.parametrize("policy", [{"deny_tools": ("WebSearch",)}, {"allowed_tools": ("Read",)}])
def test_a_codex_launch_that_must_deny_web_search_is_refused(run, tmp_path, policy):
    """Codex's web search runs on OpenAI's side; its hook never sees it."""
    with pytest.raises(LaunchRefused, match="WebSearch"):
        _codex(run, tmp_path, **policy)
