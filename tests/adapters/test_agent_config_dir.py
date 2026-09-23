"""`kraft.adapters.agent` on a harness's own config dir (Kraft-bosip).

Cursor reads its config from `CURSOR_CONFIG_DIR`. Kraft points every cursor
launch at a directory it owns under $KRAFT_HOME/run and writes Cursor's
default `cli-config.json` there with commit attribution off: with it on,
Cursor adds a `Co-authored-by: Cursor` trailer to each commit, and
`--auto-review` refused those commits."""

import json
import subprocess

import pytest

from kraft.paths import RunDirs


def test_a_cursor_launch_gets_a_kraft_owned_config_with_attribution_off(run, tmp_path):
    run_dirs = RunDirs(base=tmp_path / "run")
    config_dir = run_dirs.base / "harness-config" / "cursor"
    config_dir.mkdir(parents=True)
    (config_dir / "cli-config.json").write_text('{"attribution": {}}')  # a CLI edit

    seen = run(harness="cursor", command="agent", run_dirs=run_dirs)

    assert seen["env"]["CURSOR_CONFIG_DIR"] == str(config_dir)
    config = json.loads((config_dir / "cli-config.json").read_text())
    assert config["attribution"] == {"attributeCommitsToAgent": False, "attributePRsToAgent": False}
    # Cursor's own default and nothing more: no allow rule is Kraft's.
    assert config["permissions"] == {"allow": ["Shell(ls)"], "deny": []}
    assert [p.name for p in config_dir.iterdir()] == ["cli-config.json"]


def test_a_harness_without_a_config_dir_gets_none(run, tmp_path):
    run_dirs = RunDirs(base=tmp_path / "run")
    seen = run(harness="claude", run_dirs=run_dirs)
    assert "CURSOR_CONFIG_DIR" not in seen["env"]
    assert not (run_dirs.base / "harness-config").exists()


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path / "wt")], check=True)
    return tmp_path / "wt"


def _cursor(run, tmp_path, cwd, **kw):
    return run(
        harness="cursor",
        command="agent",
        cwd=str(cwd),
        run_dirs=RunDirs(base=tmp_path / "run"),
        **kw,
    )


def _kraft_hooks(cwd):
    hooks = cwd / ".cursor" / "hooks.json"
    return json.loads(hooks.read_text())["hooks"]["preToolUse"] if hooks.exists() else None


FAIL_CLOSED = "KRAFT_PERMISSION_FAIL_CLOSED"
#: Cursor's hook never sees these, so an allowlist must list them (Kraft-4in7z).
WEB = ("WebFetch", "WebSearch")


def test_a_cursor_launch_under_a_tool_allowlist_runs_fail_closed(run, tmp_path):
    """Cursor has no per-launch tool flags; its preToolUse hook holds the
    list (Kraft-4in7z), so the launch runs, the list is in no flag, and the
    session's env makes the hook deny every call while Kraft cannot be asked."""
    cwd = _repo(tmp_path)
    seen = _cursor(run, tmp_path, cwd, allowed_tools=("Read", *WEB))
    assert "Read" not in seen["cmd"]
    assert seen["env"][FAIL_CLOSED] == "1"
    [entry] = _kraft_hooks(cwd)
    assert entry["command"].endswith("permission-hook cursor")


def test_a_cursor_launch_with_only_deny_tools_is_fail_open(run, tmp_path):
    cwd = _repo(tmp_path)
    seen = _cursor(run, tmp_path, cwd, deny_tools=("Bash",))
    assert FAIL_CLOSED not in seen["env"]
    [entry] = _kraft_hooks(cwd)
    assert entry["command"].endswith("permission-hook cursor")


def test_a_cursor_launch_with_nothing_to_enforce_writes_no_hook(run, tmp_path):
    cwd = _repo(tmp_path)
    _cursor(run, tmp_path, cwd, grants=("git-commit",))
    assert _kraft_hooks(cwd) is None


@pytest.mark.parametrize("a_first", [True, False])
def test_sibling_launches_in_one_worktree_keep_the_hook(run, tmp_path, a_first):
    """A (allowlist) and B (nothing to enforce) share a worktree: whichever
    launches last, A's hook entry is still there, and only A is fail-closed."""
    cwd = _repo(tmp_path)
    launches = {
        "A": lambda: _cursor(run, tmp_path, cwd, allowed_tools=("Read", *WEB)),
        "B": lambda: _cursor(run, tmp_path, cwd),
    }
    envs = {k: launches[k]()["env"] for k in (("A", "B") if a_first else ("B", "A"))}
    [entry] = _kraft_hooks(cwd)
    assert entry["command"].endswith("permission-hook cursor")
    assert envs["A"][FAIL_CLOSED] == "1"
    assert FAIL_CLOSED not in envs["B"]


@pytest.mark.parametrize(
    "policy",
    [
        {"deny_tools": ("WebFetch",)},
        {"allowed_tools": ("Read",)},
        {"allowed_tools": ("Read", "WebFetch")},
    ],
    ids=["denies-an-unhooked-tool", "allowlist-without-them", "allowlist-without-one"],
)
def test_a_cursor_launch_it_cannot_enforce_is_refused(run, tmp_path, policy):
    """A web fetch never reaches Cursor's hook (probe, 2026-09-23): policy
    that would have to deny one must not launch, and writes no hook."""
    from kraft.adapters.agent import LaunchRefused

    cwd = _repo(tmp_path)
    with pytest.raises(LaunchRefused, match="WebFetch/WebSearch"):
        _cursor(run, tmp_path, cwd, **policy)
    assert _kraft_hooks(cwd) is None


def test_a_claude_launch_writes_no_cursor_hook(run, tmp_path):
    cwd = _repo(tmp_path)
    run(harness="claude", deny_tools=("Bash",), grants=("git-push",), cwd=str(cwd))
    assert not (cwd / ".cursor").exists()
