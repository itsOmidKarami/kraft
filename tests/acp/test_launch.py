import pytest

from kraft import harness
from kraft.acp import launch

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
