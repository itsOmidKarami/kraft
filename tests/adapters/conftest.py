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
            # Knows no work item: the launch lists no documents.
            db=SimpleNamespace(read=lambda fn: None),
            # Only `base` is read: where the session's files are, and what a
            # harness declaring `writable_dirs` is handed of them.
            run_dirs=SimpleNamespace(base=Path("/kraft/run")),
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


@pytest.fixture
def in_place(monkeypatch):
    """`run` overrides for an item that runs in place: its row, read off the
    launch's database, is the one row the launch reads."""
    monkeypatch.setattr("kraft.store.is_in_place", lambda row: True)
    return {"db": SimpleNamespace(read=lambda fn: {"id": "w1", "branch": None})}
