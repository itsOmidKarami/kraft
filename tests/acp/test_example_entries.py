"""The entries an operator copies into config/harnesses/ (spec §13 part 4)."""

import json
import re
from pathlib import Path

import pytest

from kraft import harness
from kraft.acp import launch
from kraft.yamlload import safe_load

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
DOCS = FIXTURES.parent / "docsite" / "content" / "5.reference" / "05.harnesses"


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
    # measured by the probe (plan task 5): the tools the bridge runs unasked in Manual mode
    assert entry.settles_itself == ("Read", "Bash", "ToolSearch")


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
        ({"allowed_tools": ["Read", "Write", "Bash"]}, "runs ToolSearch without asking"),
        ({"deny_tools": ["Bash"]}, "runs Bash without asking"),
        ({"allowed_tools": ["Read", "Write", "Bash", "ToolSearch"]}, None),
        ({"deny_tools": ["Write"]}, None),
        ({"deny_tools": ["Monitor"]}, None),
        ({}, None),
    ],
    ids=[
        "allowlist-omits-a-settled-tool",
        "permission-mode",
        "allowlist-omits-toolsearch",
        "deny-bash",
        "allowlist-keeps-them",
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


@pytest.mark.parametrize("name", ["claude-acp", "opencode-acp"])
def test_the_docs_show_each_example_entry_exactly_as_the_file_has_it(name):
    page = (DOCS / "8.claude-over-acp.md").read_text()
    shown = rf"```yaml \[config/harnesses/{name}\.yaml\]\n(.*?)```"
    blocks = re.findall(shown, page, re.S)
    assert blocks, f"{name} is not shown on the page"
    assert safe_load(blocks[0]) == safe_load((FIXTURES / f"{name}.yaml").read_text())


def test_no_other_page_shows_a_second_copy_of_an_example_entry():
    content = DOCS.parents[1]  # docsite/content
    shown = re.compile(r"```yaml \[config/harnesses/(claude|opencode)-acp\.yaml\]")
    pages = [p for p in content.rglob("*.md") if shown.search(p.read_text())]
    assert [p.name for p in pages] == ["8.claude-over-acp.md"]
