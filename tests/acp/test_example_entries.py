"""The entries an operator copies into config/harnesses/ (spec §13 part 4)."""

import json
from pathlib import Path

import pytest

from kraft import harness
from kraft.acp import launch
from kraft.yamlload import safe_load

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def load(name: str) -> harness.Harness:
    path = FIXTURES / f"{name}.yaml"
    return harness.Harness.from_mapping(safe_load(path.read_text()), where=str(path), path=path)


def test_the_claude_entry_asks_in_manual_mode_and_always_denies_monitor():
    h = load("claude-acp")
    entry = h.acp
    assert (h.kind, h.command) == ("acp", ("claude-agent-acp",))
    asks = entry.asks_everything
    assert (asks.mode, asks.env, asks.config) == ("default", {}, {})  # the bridge's "Manual"
    assert entry.always_deny == ("Monitor",)
    assert entry.reports == frozenset({"tokens", "cost"})
    assert entry.options == {"model": "model", "effort": "effort"}
    assert "Read" in entry.settles_itself  # §11: reads run unasked in Manual mode


def test_the_opencode_entry_asks_through_its_config_env():
    h = load("opencode-acp")
    env = h.acp.asks_everything.env
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"]) == {"permission": {"*": "ask"}}
    assert (h.command, h.tool_names["shell"], h.tool_names["write"]) == (
        ("opencode", "acp"),
        ("Bash",),
        ("Write",),
    )


@pytest.mark.parametrize(
    ("limits", "reason"),
    [
        ({"allowed_tools": ["Write", "Bash"]}, "runs Read without asking"),
        ({"permission_mode": "auto"}, "permission_mode"),
        ({"allowed_tools": ["Read", "Write", "Bash"]}, None),
        ({"deny_tools": ["Write"]}, None),
        ({"deny_tools": ["Monitor"]}, None),
        ({}, None),
    ],
    ids=[
        "allowlist-omits-a-settled-tool",
        "permission-mode",
        "allowlist-keeps-it",
        "deny-list",
        "deny-monitor",
        "no-limit",
    ],
)
def test_the_claude_entry_refuses_a_limit_that_denies_a_tool_the_bridge_settles(limits, reason):
    got = launch.tool_limit_refusal(
        load("claude-acp"),
        **{"allowed_tools": None, "deny_tools": (), "permission_mode": None, **limits},
    )
    assert (got is None) if reason is None else (reason in got)
