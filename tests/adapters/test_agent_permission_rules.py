"""`kraft.adapters.agent` on a harness whose tool policy is written into the
CLI's own permission config at launch (Kraft-4in7z.4 opencode, .2 amp)."""

import json

import pytest

from kraft.adapters.agent import LaunchRefused
from kraft.paths import RunDirs

CONFIG = "OPENCODE_CONFIG_CONTENT"


def _opencode(run, tmp_path, **kw):
    return run(harness="opencode", command="opencode", run_dirs=RunDirs(base=tmp_path), **kw)


def test_an_opencode_launch_carries_its_deny_tools_in_a_standalone_config(run, tmp_path):
    seen = _opencode(run, tmp_path, deny_tools=("Bash",))
    assert seen["cmd"][:3] == ["opencode", "run", "--standalone"]
    assert "Bash" not in seen["cmd"]
    assert json.loads(seen["env"][CONFIG])["permission"]["execute"] == "deny"


def test_an_opencode_launch_with_nothing_to_enforce_is_unchanged(run, tmp_path):
    seen = _opencode(run, tmp_path, grants=("git-push",))
    assert "--standalone" not in seen["cmd"]
    assert CONFIG not in seen["env"]


def test_an_opencode_launch_runs_under_an_allowlist(run, tmp_path):
    """No restrict_tools or under_allowlist mode: the config holds the list."""
    seen = _opencode(run, tmp_path, allowed_tools=("Read",))
    assert json.loads(seen["env"][CONFIG])["permission"]["*"] == "deny"


def test_a_denied_name_the_cli_has_no_tool_for_is_refused(run, tmp_path):
    with pytest.raises(LaunchRefused, match=r"'opencode'.*'Monitor'"):
        _opencode(run, tmp_path, deny_tools=("Monitor",))


def test_an_allowlisted_name_the_cli_has_no_tool_for_grants_nothing(run, tmp_path):
    permission = json.loads(
        _opencode(run, tmp_path, allowed_tools=("Read", "Task2"))["env"][CONFIG]
    )["permission"]
    assert permission["*"] == "deny" and permission["read"] == "allow"


def _amp(run, tmp_path, **kw):
    return run(harness="amp", command="amp", run_dirs=RunDirs(base=tmp_path), **kw)


def test_an_amp_launch_gets_its_own_settings_file(run, tmp_path):
    seen = _amp(run, tmp_path, deny_tools=("Bash",))
    path = tmp_path / "harness-config" / "amp" / "s1.json"
    assert seen["cmd"][:4] == ["amp", "--no-archive-after-execute", "--settings-file", str(path)]
    assert json.loads(path.read_text())["amp.permissions"][0]["action"] == "reject"


def test_an_amp_resume_keeps_its_settings_file(run, tmp_path):
    seen = _amp(run, tmp_path, allowed_tools=("Edit",), resume_session_id="T-1")
    i = seen["cmd"].index("--settings-file")
    assert seen["cmd"][:i] == ["amp", "threads", "continue", "T-1", "--no-archive-after-execute"]


def test_an_amp_launch_with_nothing_to_enforce_reads_the_users_settings(run, tmp_path):
    seen = _amp(run, tmp_path)
    assert "--settings-file" not in seen["cmd"]
    assert not (tmp_path / "harness-config").exists()


def test_an_amp_policy_naming_read_is_refused(run, tmp_path):
    """Amp reads through its shell; there is no read tool to deny."""
    with pytest.raises(LaunchRefused, match=r"'amp'.*'Read'"):
        _amp(run, tmp_path, deny_tools=("Read",))
