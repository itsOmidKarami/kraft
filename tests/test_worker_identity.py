"""A session the executor starts knows which work item it is (design §7).

This is what makes the §6 rule 2 guard real: without the env var a worker looks
like a human standing in a worktree, and would be allowed to approve its own gate.
"""

from __future__ import annotations

import asyncio
import sys

import pytest
from support.harness import make_repo

from kraft import client, db, store
from kraft.adapters import agent
from kraft.paths import RunDirs

# A stand-in agent that does one thing: print its own environment to stdout,
# which run_task redirects into the session log.
_DUMP_ENV = "import json,os;print(json.dumps(dict(os.environ)))"


def _run_agent_and_read_log(tmp_path, repo, session_id="s1", work_item_id="w1") -> str:
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=work_item_id,
                    bead_id="B",
                    title="t",
                    repo=str(repo),
                    chain_template="quick-task",
                    chain_definition="{}",
                )
            )
            await agent.run_agent_task(
                database,
                rd,
                session_id=session_id,
                work_item_id=work_item_id,
                node_id="implementation",
                hook_point="on.implementation.start",
                command=f"{sys.executable} -c {_DUMP_ENV!r}",
                title="t",
                task_instruction="do the thing",
                repo_path=str(repo),
                cwd=repo,
            )
        finally:
            await database.close()
        return (rd.logs / f"{session_id}.log").read_text()

    return asyncio.run(scenario())


def test_the_session_environment_carries_the_work_item_id(tmp_path):
    log = _run_agent_and_read_log(tmp_path, make_repo(tmp_path))
    assert '"KRAFT_WORK_ITEM_ID": "w1"' in log


def test_the_session_environment_carries_the_session_id(tmp_path):
    log = _run_agent_and_read_log(tmp_path, make_repo(tmp_path), session_id="s-abc")
    assert '"KRAFT_SESSION_ID": "s-abc"' in log


def test_a_session_with_the_env_var_resolves_as_a_worker(monkeypatch, tmp_path):
    """The end the guard cares about: env var set means origin 'worker'."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "w1")
    assert client.resolve_context() == ("w1", "worker")


def test_a_worker_in_its_own_worktree_is_still_a_worker(monkeypatch, tmp_path):
    """Belt and braces: the cwd walk alone would say 'user' and hand the session
    self-approval rights. The env var must win over the path."""
    run = tmp_path / "run"
    worktree = run / "worktrees" / "w1"
    worktree.mkdir(parents=True)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run))
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "w1")
    monkeypatch.chdir(worktree)

    assert client.resolve_context() == ("w1", "worker")
    with pytest.raises(PermissionError):
        client.context._forbid_self_action(None)
