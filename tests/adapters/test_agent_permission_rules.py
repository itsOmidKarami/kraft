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


@pytest.mark.parametrize(
    "policy,name",
    [({"deny_tools": ("Monitor",)}, "Monitor"), ({"allowed_tools": ("Read", "Task2")}, "Task2")],
)
def test_a_policy_name_the_cli_has_no_tool_for_is_refused(run, tmp_path, policy, name):
    with pytest.raises(LaunchRefused, match=rf"'opencode'.*'{name}'"):
        _opencode(run, tmp_path, **policy)
