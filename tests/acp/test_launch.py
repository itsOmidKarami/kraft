from pathlib import Path
from types import SimpleNamespace

import pytest

from kraft import harness
from kraft.acp import launch
from kraft.caps import SpendCaps

H = harness.Harness.from_mapping(
    {"id": "fake-acp", "kind": "acp", "command": ["agent", "acp"]}, where="t"
)
NONE = dict(allowed_tools=None, deny_tools=(), permission_mode=None)


def _acp(**entry):
    return harness.Harness.from_mapping(
        {
            "id": "fake-acp",
            "kind": "acp",
            "command": ["agent", "acp"],
            "asks_everything": {"env": {"A": "1"}},
            **entry,
        },
        where="t",
    )


@pytest.mark.parametrize(
    ("entry", "patch", "expect"),
    [
        ({}, {"permission_mode": "auto"}, "permission_mode"),
        ({}, {"deny_tools": ("Bash",), "grants": ("git-commit",)}, "named grant"),
        ({}, {"allowed_tools": ("Read",), "grants": ("git-commit",)}, "named grant"),
        ({"asks_everything": None}, {"deny_tools": ("Bash",)}, "declares no `asks_everything`"),
        (
            {"asks_everything": None, "always_deny": ["Monitor"]},
            {},
            "declares no `asks_everything`",
        ),
        (
            {"tool_names": {"edit": ["Write", "Edit"]}},
            {"allowed_tools": ("Write",)},
            "cannot tell them apart",
        ),
        (
            {"tool_names": {"edit": ["Write", "Edit"]}},
            {"deny_tools": ("Edit",)},
            "cannot tell them apart",
        ),
        ({"settles_itself": ["Read"]}, {"deny_tools": ("Read",)}, "without asking"),
        ({"settles_itself": ["Read"]}, {"allowed_tools": ("Bash",)}, "without asking"),
        ({"settles_itself": ["Read"], "always_deny": ["Read"]}, {}, "without asking"),
    ],
    ids=[
        "permission-mode",
        "grant-with-bash-denied",
        "grant-with-allowlist-omitting-bash",
        "deny-list-no-asks",
        "always-deny-no-asks",
        "allowlist-splits-a-kind",
        "deny-list-splits-a-kind",
        "deny-of-a-settled-tool",
        "allowlist-omits-a-settled-tool",
        "always-deny-of-a-settled-tool",
    ],
)
def test_a_tool_limit_the_entry_cannot_hold_is_refused_with_its_reason(entry, patch, expect):
    assert expect in launch.launch_refusal(_acp(**entry), **{**NONE, **patch})


@pytest.mark.parametrize(
    ("entry", "patch"),
    [
        ({}, {"allowed_tools": ("Read", "Bash")}),
        ({}, {"allowed_tools": ()}),
        ({}, {"deny_tools": ("Bash",)}),
        ({"tool_names": {"edit": ["Write", "Edit"]}}, {"allowed_tools": ("Write", "Edit")}),
        ({"tool_names": {"edit": ["Write", "Edit"]}}, {"deny_tools": ("Write", "Edit")}),
        ({"settles_itself": ["Read"]}, {"allowed_tools": ("Read",)}),
        ({"tool_names": {"write": ["Write", "Edit"]}}, {"deny_tools": ("Edit",)}),
        ({}, {"grants": ("git-commit", "git-rebase", "git-push")}),
        ({}, {"deny_tools": ("WebFetch",), "grants": ("git-commit", "git-rebase", "git-push")}),
    ],
    ids=[
        "allowlist",
        "empty-allowlist",
        "deny-list",
        "kind-kept-whole-allow",
        "kind-kept-whole-deny",
        "settled-tool-allowed",
        "name-covering-two-tools-is-the-gates-also-rule",
        "grants-with-no-policy",
        "grants-with-a-deny-that-leaves-bash",
    ],
)
def test_a_tool_limit_the_entry_can_hold_is_not_refused(entry, patch):
    assert launch.launch_refusal(_acp(**entry), **{**NONE, **patch}) is None


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


def test_the_launch_spec_names_a_marker_and_a_sandbox_rewrite_keeps_the_unwrapped_argv(tmp_path):
    files = SimpleNamespace(instruction=tmp_path / "instruction.md", result=tmp_path / "r.json")
    argv = launch.command(
        H,
        files=files,
        agent_argv=["agent", "acp"],
        cwd=tmp_path,
        prompt="p",
        context="c",
        resume=None,
        model=None,
        effort=None,
    )
    path = Path(argv[-1])
    assert launch.read_spec(path).started_marker == str(launch.marker_path(files))
    assert launch.marker_path(files).name == "acp-started"
    launch.rewrite_agent(path, ["docker", "run", "-i", "img", "agent", "acp"])
    launch.rewrite_agent(path, ["docker", "run", "-i", "-e", "X=1", "img", "agent", "acp"])
    spec = launch.read_spec(path)
    assert spec.agent_argv[-3:] == ["img", "agent", "acp"] and spec.logged_agent_argv == [
        "agent",
        "acp",
    ]
    assert oct(path.stat().st_mode & 0o777) == "0o600"


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
