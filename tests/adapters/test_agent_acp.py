import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.acp import FAKE

from kraft import harness
from kraft.adapters.agent import LaunchRefused
from kraft.caps import SpendCaps
from kraft.paths import RunDirs
from kraft.policy import Budget
from kraft.remote import launch as remote_launch
from kraft.remote import protocol

ENTRY = {
    "id": "fake-acp",
    "kind": "acp",
    "command": [sys.executable, str(FAKE)],
    "reports": ["cost"],  # so the cap checks of later parts pass unless a test makes them fail
    "options": {"model": {"config": "model"}},
}


def _launch(run, tmp_path, entry=ENTRY, **kw):
    hs = harness.HarnessSet(
        valid={"fake-acp": harness.Harness.from_mapping(entry, where="t")}, invalid={}
    )
    return run(
        harness="fake-acp",
        harnesses=hs,
        command="",
        cwd=str(tmp_path),
        run_dirs=RunDirs(base=tmp_path / "run"),
        **kw,
    )


ASKING = {
    **ENTRY,
    "asks_everything": {"env": {"FAKE_ACP_ASK_ALL": "1"}},
    "tool_names": {"read": "Read"},
}


@pytest.mark.parametrize(
    ("entry", "kw", "policy", "allowlist"),
    [
        (ASKING, {"allowed_tools": ("Read",)}, True, True),
        (ASKING, {"deny_tools": ("Bash",)}, True, False),
        ({**ASKING, "always_deny": ["Monitor"]}, {}, True, False),
        (ASKING, {}, False, False),
    ],
    ids=["allowlist", "deny-only", "always-deny-alone", "no-policy-still-asks"],
)
def test_an_acp_launch_hands_its_tool_policy_to_the_client(
    run, tmp_path, entry, kw, policy, allowlist
):
    spec = json.loads(Path(_launch(run, tmp_path, entry, **kw)["cmd"][7]).read_text())
    assert (spec["policy"], spec["allowlist"], spec["harness_id"]) == (
        policy,
        allowlist,
        "fake-acp",
    )
    assert spec["agent_env"] == {"FAKE_ACP_ASK_ALL": "1"}  # applied on every launch, policy or not
    assert spec["tool_names"] == {"read": ["Read"]}
    assert spec["always_deny"] == entry.get("always_deny", [])
    assert spec["deny_tools"] == list(kw.get("deny_tools", ()))  # names the limit in a stop


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


def test_the_launch_door_refuses_what_it_cannot_hold(run, tmp_path):
    with pytest.raises(LaunchRefused, match="asks_everything"):
        _launch(run, tmp_path, deny_tools=("Bash",))


@pytest.mark.parametrize(
    ("kw", "refused"),
    [
        ({"sandbox": {"kind": "docker", "image": "x", "network": {"allow": []}}}, None),
        ({"sandbox": {"kind": "docker", "image": "x"}}, "no `network:` policy"),
        ({"runner": "box"}, None),
    ],
    ids=[
        "sandbox-with-network-runs",
        "sandbox-without-network-keeps-the-general-refusal",
        "machine-runs",
    ],
)
def test_an_acp_launch_in_a_sandbox_or_on_a_machine_runs_or_is_refused_by_the_general_rule(
    run, tmp_path, kw, refused
):
    if refused:
        with pytest.raises(LaunchRefused, match=refused):
            _launch(run, tmp_path, **kw)
    else:
        assert _launch(run, tmp_path, **kw)["reader"] == "acp"


@pytest.mark.parametrize(
    ("caps", "places_again"),
    [(frozenset(), True), (frozenset({protocol.ACP_CLIENT}), False)],
    ids=["machine-lacks-the-client", "machine-has-the-client"],
)
def test_an_acp_launch_placed_on_a_machine_without_the_client_asks_to_be_placed_again(
    run, tmp_path, monkeypatch, caps, places_again
):
    monkeypatch.setattr(
        "kraft.remote.runs.current",
        lambda: SimpleNamespace(hub=SimpleNamespace(caps=lambda m: caps)),
    )
    kw = {"runner": "gpu", "placed_claim": SimpleNamespace(machine="a")}
    if places_again:
        with pytest.raises(remote_launch.PlaceAgain) as raised:
            _launch(run, tmp_path, **kw)
        assert protocol.ACP_CLIENT in raised.value.needs
    else:
        assert _launch(run, tmp_path, **kw)["reader"] == "acp"


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
