import pytest

from kraft import harness

ACP = {
    "id": "fake-acp",
    "kind": "acp",
    "command": ["fake-acp-agent"],
    "reports": ["tokens", "cost"],
    "tool_names": {"read": "Read", "shell": "Bash"},
    "always_deny": ["Monitor"],
    "asks_everything": {"env": {"X_ASK": "1"}},
    "options": {"model": {"config": "model"}, "effort": {"config": "effort"}},
}


def test_an_acp_entry_loads_with_the_capabilities_kraft_reads():
    h = harness.Harness.from_mapping(ACP, where="t")
    assert (h.kind, h.command) == ("acp", ("fake-acp-agent",))
    assert h.acp.reports == frozenset({"tokens", "cost"})
    assert h.acp.always_deny == ("Monitor",)
    assert h.acp.options == {"model": "model", "effort": "effort"}
    assert h.acp.asks_everything.env == {"X_ASK": "1"}
    assert h.tool_names == {"read": ("Read",), "shell": ("Bash",)}
    usage = h.capabilities["usage"]
    assert (usage.source, usage.reader) == ("envelope", "acp")
    assert h.capabilities["context"].channel == "system_prompt"
    assert {"prompt", "resume", "model", "effort", "structured_log"} <= set(h.capabilities)
    assert not h.supports("deny_tools") and not h.supports("permission_mode")
    assert h.supports("model") and h.value_ok("model", "anything")


def test_an_acp_entry_declares_only_what_the_agent_cannot_tell():
    minimal = {"id": "m", "kind": "acp", "command": ["agent", "acp"]}
    h = harness.Harness.from_mapping(minimal, where="t")
    assert h.command == ("agent", "acp")
    assert (h.acp.reports, h.acp.options, h.acp.asks_everything) == (frozenset(), {}, None)
    assert not h.supports("model") and not h.supports("effort")


def test_a_command_given_as_one_string_is_one_word_not_split():
    # `command` is a list of argv words. A bare string is one word (`Prefix` wraps it, as it does
    # for kind: cli), so `"agent acp"` would try to run a program named `agent acp`: the docs say
    # to write a list, and this pins the behaviour they warn about.
    h = harness.Harness.from_mapping({"id": "m", "kind": "acp", "command": "agent acp"}, where="t")
    assert h.command == ("agent acp",)


@pytest.mark.parametrize(
    ("patch", "expect"),
    [
        ({"reports": ["tokens", "dollars"]}, "reports"),
        ({"reports": "tokens"}, "reports"),
        ({"asks_everything": {"env": {"A": "1"}, "mode": "default"}}, "exactly one"),
        ({"asks_everything": {}}, "exactly one"),
        ({"always_deny": "Monitor"}, "always_deny"),
        ({"options": {"color": {"config": "c"}}}, "options"),
        ({"options": {"model": {}}}, "options"),
        ({"capabilities": {"prompt": {"cli": ["{value}"]}}}, "capabilities"),
        ({"command_resume": ["x"]}, "command_resume"),
        ({"permission_hook": "codex"}, "permission_hook"),
        ({"permission_rules": "opencode"}, "permission_rules"),
        ({"command": []}, "command"),
    ],
    ids=[
        "unknown-report",
        "reports-not-a-list",
        "asks-two-ways",
        "asks-nothing",
        "always-deny-not-a-list",
        "unknown-option",
        "option-without-config",
        "capabilities-given",
        "command-resume",
        "permission-hook",
        "permission-rules",
        "no-command",
    ],
)
def test_a_malformed_acp_entry_is_refused_naming_the_key(patch, expect):
    with pytest.raises(harness.HarnessError, match=expect):
        harness.Harness.from_mapping({**ACP, **patch}, where="t")
