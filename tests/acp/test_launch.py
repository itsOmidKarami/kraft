import pytest

from kraft import harness
from kraft.acp import launch
from kraft.caps import SpendCaps

H = harness.Harness.from_mapping(
    {"id": "fake-acp", "kind": "acp", "command": ["agent", "acp"]}, where="t"
)
NONE = dict(allowed_tools=None, deny_tools=(), permission_mode=None, sandbox=None, runner=None)


@pytest.mark.parametrize(
    ("patch", "expect"),
    [
        ({"allowed_tools": ("Read",)}, "tool limits over ACP are not supported yet"),
        ({"allowed_tools": ()}, "tool limits over ACP are not supported yet"),
        ({"deny_tools": ("Bash",)}, "tool limits over ACP are not supported yet"),
        ({"permission_mode": "auto"}, "tool limits over ACP are not supported yet"),
        ({"sandbox": {"kind": "docker"}}, "part 3"),
        ({"runner": "box"}, "part 3"),
    ],
    ids=["allowlist", "empty-allowlist", "deny-list", "permission-mode", "sandbox", "remote"],
)
def test_a_launch_the_client_cannot_yet_hold_is_refused_with_its_reason(patch, expect):
    assert expect in launch.launch_refusal(H, **{**NONE, **patch})


def test_an_unrestricted_local_launch_is_not_refused():
    assert launch.launch_refusal(H, **NONE) is None


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        (None, ["agent", "acp"]),
        ("/opt/bin/agent", ["/opt/bin/agent", "acp"]),
        ("node /x/agent.js", ["node", "/x/agent.js", "acp"]),
    ],
    ids=["entry", "executable", "wrapper"],
)
def test_a_profile_executable_replaces_only_the_agents_first_word(command, argv):
    assert launch.agent_argv(H, command) == argv


@pytest.mark.parametrize(
    ("reports", "caps", "model", "refused"),
    [
        ([], SpendCaps(False, False), None, False),
        ([], SpendCaps(True, False), None, True),
        (["tokens"], SpendCaps(True, False), None, False),
        (["cost"], SpendCaps(True, False), None, True),
        (["cost"], SpendCaps(False, True), None, False),
        (["tokens", "cost"], SpendCaps(True, True), None, False),
        (["tokens"], SpendCaps(False, True), "claude-haiku-4-5", False),
        (["tokens"], SpendCaps(False, True), "no-such-model", True),
        (["tokens"], SpendCaps(False, True), None, True),
        ([], SpendCaps(False, True), "claude-haiku-4-5", True),
    ],
    ids=[
        "no-cap",
        "tokens-cap-no-report",
        "tokens-cap-tokens",
        "tokens-cap-cost-only",
        "usd-cap-cost",
        "both",
        "usd-cap-priced-tokens",
        "usd-cap-unpriced",
        "usd-cap-no-model",
        "usd-cap-nothing",
    ],
)
def test_a_cap_the_agent_cannot_be_counted_against_refuses_the_launch(
    reports, caps, model, refused
):
    h = harness.Harness.from_mapping(
        {"id": "fake-acp", "kind": "acp", "command": ["a"], "reports": reports}, where="t"
    )
    why = launch.cap_refusal(h, caps, model)
    assert (why is not None) is refused
    if refused:
        assert "fake-acp" in why and "reports" in why
