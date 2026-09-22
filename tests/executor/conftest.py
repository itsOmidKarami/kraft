import asyncio
import json
import sys
from pathlib import Path

import pytest
from support.harness import seed_v1_library, v1_named_chain

from kraft.executor import dispatch

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


class Script:
    """`dispatch.dispatch_node`, scripted per task id. `plan[id]` is the
    statuses that task answers in turn (the last one repeats; `done` when
    unplanned); a task id in `real` runs the real dispatch instead, and
    `effects[id]` is awaited with the work item's row before it answers. `log` is
    every `("start"|"end", id)` in the order it happened, `steers[id]` the
    steer each launch was handed and `instructions[id]` its override."""

    def __init__(self, real_dispatch):
        self._real = real_dispatch
        self.real: set[str] = set()
        self.plan: dict[str, list[str]] = {}
        self.delay: dict[str, float] = {}
        self.effects: dict = {}
        self.log: list[tuple[str, str]] = []
        self.steers: dict[str, list] = {}
        self.instructions: dict[str, list] = {}

    @property
    def calls(self) -> list[str]:
        return [task for event, task in self.log if event == "start"]

    def index(self, event: str, task: str, nth: int = 0) -> int:
        return [i for i, e in enumerate(self.log) if e == (event, task)][nth]

    async def __call__(self, db_, run_dirs_, task, node, row_, wt, **kw):
        tid = task.task.id
        self.log.append(("start", tid))
        self.steers.setdefault(tid, []).append(kw.get("steer"))
        self.instructions.setdefault(tid, []).append(kw.get("instruction_override"))
        if tid in self.real:
            status = await self._real(db_, run_dirs_, task, node, row_, wt, **kw)
        else:
            if tid in self.effects:
                await self.effects[tid](row_)
            await asyncio.sleep(self.delay.get(tid, 0))
            seq = self.plan.get(tid, ["done"])
            status = seq.pop(0) if len(seq) > 1 else seq[0]
        self.log.append(("end", tid))
        return status


@pytest.fixture
def script(monkeypatch) -> Script:
    """`dispatch_node` answered by a `Script` rather than by running tasks."""
    spy = Script(dispatch.dispatch_node)
    monkeypatch.setattr(dispatch, "dispatch_node", spy)
    return spy
