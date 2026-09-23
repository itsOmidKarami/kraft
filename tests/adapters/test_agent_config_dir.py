"""`kraft.adapters.agent` on a harness's own config dir (Kraft-bosip).

Cursor reads its config from `CURSOR_CONFIG_DIR`. Kraft points every cursor
launch at a directory it owns under $KRAFT_HOME/run and writes Cursor's
default `cli-config.json` there with commit attribution off: with it on,
Cursor adds a `Co-authored-by: Cursor` trailer to each commit, and
`--auto-review` refused those commits."""

import json

import pytest

from kraft.adapters.agent import LaunchRefused
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


def test_a_cursor_launch_under_a_tool_allowlist_is_refused(run):
    """No per-launch tool flags and no mode that asks a permission gate, so
    the bound could not be held: refused, not run unbounded."""
    with pytest.raises(LaunchRefused, match="cannot hold an agent to a tool list"):
        run(harness="cursor", command="agent", allowed_tools=("Read",))
