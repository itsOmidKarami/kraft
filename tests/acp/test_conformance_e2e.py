"""The conformance probe against the two real ACP agents (spec §9, §12).

The Claude case depends on the machine's Claude settings: the bridge loads `settingSources`
user/project/local, so the operator's own `permissions` rules change which calls it asks about.
"""

import asyncio
import json
from pathlib import Path

import pytest

from kraft.acp import conformance

REAL_HOME = str(Path.home())
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.mark.e2e("opencode")
def test_opencode_conforms_to_its_entry():
    h = conformance.load_entry(None, str(FIXTURES / "opencode-acp.yaml"))
    got = asyncio.run(conformance.run(h, timeout_s=300))
    assert got.ok, json.dumps({"problems": got.problems, "mismatches": got.mismatches})
    assert got.tokens_reported and got.cost_reported


@pytest.mark.e2e("claude", "claude-agent-acp", "bd")  # `bd`: see test_claude_acp_e2e
def test_claude_over_acp_conforms_to_its_entry(monkeypatch):
    monkeypatch.setenv("HOME", REAL_HOME)  # the suite points HOME at an empty folder: no login
    h = conformance.load_entry(None, str(FIXTURES / "claude-acp.yaml"))
    got = asyncio.run(conformance.run(h, model="haiku", timeout_s=300))
    assert got.ok, json.dumps({"problems": got.problems, "mismatches": got.mismatches})
    assert got.agent and "claude" in got.agent.lower()
