import json
import sys
from pathlib import Path

import pytest
from support.acp import FAKE

from kraft import harness
from kraft.adapters.agent import LaunchRefused
from kraft.caps import SpendCaps
from kraft.paths import RunDirs
from kraft.policy import Budget

ENTRY = {
    "id": "fake-acp",
    "kind": "acp",
    "command": [sys.executable, str(FAKE)],
    "reports": ["cost"],  # so the cap checks of later parts pass unless a test makes them fail
    "options": {"model": {"config": "model"}},
}


def _launch(run, tmp_path, **kw):
    hs = harness.HarnessSet(
        valid={"fake-acp": harness.Harness.from_mapping(ENTRY, where="t")}, invalid={}
    )
    return run(
        harness="fake-acp",
        harnesses=hs,
        command="",
        cwd=str(tmp_path),
        run_dirs=RunDirs(base=tmp_path / "run"),
        **kw,
    )


def test_an_acp_launch_runs_the_client_with_a_spec_and_the_agent_argv_in_it(run, tmp_path):
    seen = _launch(run, tmp_path, model="big")
    cmd = seen["cmd"]
    assert cmd[:6] == [sys.executable, "-I", "-m", "kraft", "admin", "acp-client"]
    assert cmd[6] == "--spec"
    spec = json.loads(Path(cmd[7]).read_text())
    assert spec["agent_argv"] == [sys.executable, str(FAKE)]
    assert (spec["model"], spec["options"], spec["resume"]) == ("big", {"model": "model"}, None)
    assert "do the thing" in spec["prompt"] and spec["context"]
    assert seen["reader"] == "acp"
    assert oct(Path(cmd[7]).stat().st_mode & 0o777) == "0o600"


def test_a_resumed_acp_launch_carries_the_agents_session_id(run, tmp_path):
    seen = _launch(run, tmp_path, resume_session_id="fake-session-1")
    assert json.loads(Path(seen["cmd"][7]).read_text())["resume"] == "fake-session-1"


@pytest.mark.parametrize(
    ("kw", "expect"),
    [
        ({"deny_tools": ("Bash",)}, "tool limits over ACP"),
        ({"sandbox": {"kind": "docker", "image": "x", "network": {"allow": []}}}, "part 3"),
        ({"runner": "box"}, "part 3"),
    ],
    ids=["tool-policy", "sandbox", "remote"],
)
def test_the_launch_door_refuses_what_the_client_cannot_yet_hold(run, tmp_path, kw, expect):
    with pytest.raises(LaunchRefused, match=expect):
        _launch(run, tmp_path, **kw)


def test_a_launch_under_a_cap_its_entry_does_not_report_is_refused_before_it_starts(
    run, tmp_path, monkeypatch
):
    monkeypatch.setattr("kraft.adapters.agent._spend_caps", lambda *a, **k: SpendCaps(True, False))
    with pytest.raises(LaunchRefused, match=r"token_budget.*reports"):
        _launch(run, tmp_path)


@pytest.mark.parametrize(
    ("hook_point", "node_id", "scope"),
    [("build.run.impl", "build", "build.run.impl"), ("escalation", "build", "build")],
    ids=["task-hook-point", "escalation-uses-the-node-id"],
)
def test_the_scope_a_launchs_caps_are_read_at(hook_point, node_id, scope):
    from kraft.adapters import agent

    assert agent._scope_path(hook_point, node_id) == scope


def test_an_acp_launch_is_refused_under_a_daily_cap_while_an_acp_session_today_has_no_cost(
    run, tmp_path, monkeypatch
):
    monkeypatch.setattr("kraft.adapters.agent._unknown_acp_today", lambda *a, **k: 1)
    with pytest.raises(LaunchRefused, match=r"daily_usd cannot be checked.*no cost"):
        _launch(run, tmp_path, budget=Budget(daily_usd=5))


def test_a_cli_launch_is_not_refused_by_the_acp_unknown_count(run, monkeypatch):
    monkeypatch.setattr("kraft.adapters.agent._unknown_acp_today", lambda *a, **k: 1)
    seen = run(budget=Budget(daily_usd=5))  # the stock `run` launch is a cli harness (`claude`)
    assert seen["cmd"]  # it launched
