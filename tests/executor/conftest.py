import json
import sys
from pathlib import Path

import pytest
from support.harness import seed_v1_library, v1_named_chain

#: What an agent task on the `fake` (or `claude`) harness launches.
FAKE_AGENT_COMMAND = f"{sys.executable} {Path(__file__).parents[1] / 'support' / 'fake_agent.py'}"


class FakeAgent:
    """tests/support/fake_agent.py behind the `fake` and `claude` harnesses, in
    its default mode (it edits `calc.py` and reports done; `KRAFT_FAKE_AGENT`
    set by the test picks another). Every launch is logged: `prompts()` is the
    `-p` instruction of each, `argv()` the whole argv."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.templates = seed_v1_library(tmp_path / "templates", agent_command=FAKE_AGENT_COMMAND)
        self._prompts = tmp_path / "fake-agent-prompts.txt"
        self._argv = tmp_path / "fake-agent-argv.jsonl"
        monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
        monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(self._prompts))
        monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(self._argv))

    @property
    def quick_task(self):
        """The shipped gateless `quick-task` (implementation, verify), its
        agent task on this fake."""
        return v1_named_chain(self.templates)

    def prompts(self) -> list[str]:
        text = self._prompts.read_text() if self._prompts.exists() else ""
        return [p for p in text.split("\n\x00\n") if p.strip()]

    def argv(self) -> list[list[str]]:
        text = self._argv.read_text() if self._argv.exists() else ""
        return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.fixture
def fake_agent(tmp_path, monkeypatch) -> FakeAgent:
    """Agent tasks on the `fake` harness launch the fake agent; see `FakeAgent`."""
    return FakeAgent(tmp_path, monkeypatch)
