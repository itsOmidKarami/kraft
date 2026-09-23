"""Shared fixtures for the adapter tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from kraft.adapters import agent


@pytest.fixture
def run(monkeypatch):
    """`run(**overrides)` calls `run_agent_task` with dummy plumbing and returns
    the kwargs it handed `subprocess.run_task` (`cmd`, `env`, `sandbox`, ...)."""
    seen: dict = {}

    async def fake_run_task(db, run_dirs, **kw):
        seen.update(kw)
        return "done"

    monkeypatch.setattr("kraft.adapters.agent._subprocess.run_task", fake_run_task)

    def go(**overrides):
        import asyncio

        kwargs = dict(
            db=None,
            # Only `results` is read: a harness declaring `result_dir` is handed it.
            run_dirs=SimpleNamespace(results=Path("/kraft/run/results")),
            session_id="s1",
            work_item_id="w1",
            node_id="implementation",
            hook_point="on.implementation.start",
            command="claude",
            title="t",
            task_instruction="do the thing",
            repo_path="/repo",
            cwd="/repo",
        )
        seen.clear()
        asyncio.run(agent.run_agent_task(**{**kwargs, **overrides}))
        return dict(seen)

    return go
