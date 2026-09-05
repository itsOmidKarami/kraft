# Agent Integration Phase 4 (Worker Identity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A session the executor starts knows which work item it belongs to, so the phase 3 self-action guard actually fires.

**Architecture:** One `env` dict, passed from `adapters/agent.py` into the merge that `adapters/subprocess.py:118` already performs. No new plumbing, no MCP config, no vendor-specific flags.

**Tech Stack:** Python 3.14, pytest.

**Spec:** `docs/superpowers/specs/2026-09-05-agent-integration-design.md` §7

## Global Constraints

- Phases 1–3 shipped. `client.resolve_context()` reads `KRAFT_WORK_ITEM_ID` and returns `origin="worker"` when it is set; `client._forbid_self_action` keys on that.
- **No `--mcp-config` flag, and nothing Claude-specific in `adapters/agent.py`.** `01_conceptual_model.md` §1.2: the core hardcodes no vendor. Tool availability for workers comes from the human's own `kraft init`, not from Kraft rewriting the command line. See spec §7.
- `adapters/subprocess.py:118` already does `full_env = {**os.environ, **(env or {}), "KRAFT_RESULT_PATH": str(result_path)}` — this phase supplies `env`, nothing more.
- Async tests are sync functions wrapping `asyncio.run(...)`, per `tests/test_adapters_subprocess.py`.
- Run `just lint` before the commit.

---

### Task 1: The executor tells a session which work item it is

Without this, a worker session's `resolve_context()` falls back to the cwd walk. That happens to give the right *id* (the worktree is named after the work item) but the wrong *origin*: `"user"`. A worker would then be allowed to approve its own gate — the precise failure §6 rule 2 exists to prevent. The guard shipped in phase 3 is inert until this lands.

**Files:**
- Modify: `src/kraft/adapters/agent.py:80-92` (the `run_task` call)
- Test: `tests/test_worker_identity.py`

**Interfaces:**
- Consumes: `_subprocess.run_task(..., env=...)` (already exists, `subprocess.py:97`); `client.resolve_context` from phase 1.
- Produces: `KRAFT_WORK_ITEM_ID` and `KRAFT_SESSION_ID` in every agent session's environment.

- [ ] **Step 1: Write the failing test**

Create `tests/test_worker_identity.py`:

```python
"""A session the executor starts knows which work item it is (design §7).

This is what makes the §6 rule 2 guard real: without the env var a worker looks
like a human standing in a worktree, and would be allowed to approve its own gate.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

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
        client._forbid_self_action(None)
```

`Database.close()` exists (`kraft/db.py:289`) and `tests/test_adapters_agent.py`
closes in a `finally` the same way; this follows that.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_worker_identity.py -q`
Expected: FAIL — `KRAFT_WORK_ITEM_ID` is absent from the dumped environment, because `run_agent_task` never passes `env`.

- [ ] **Step 3: Write minimal implementation**

In `src/kraft/adapters/agent.py`, pass `env` in the `run_task` call:

```python
    return await _subprocess.run_task(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        cmd=cmd,
        cwd=cwd,
        # Identity, not configuration. `client.resolve_context()` keys `origin`
        # on KRAFT_WORK_ITEM_ID, and `origin` is the whole of the design §6 rule
        # 2 guard: without this, a worker in its own worktree reads as a human
        # and may approve its own gate. Deliberately no MCP config here — that
        # would make this vendor-aware, against conceptual model §1.2.
        env={"KRAFT_WORK_ITEM_ID": work_item_id, "KRAFT_SESSION_ID": session_id},
        post_resolve=_envelope_is_error,
        round=round,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_worker_identity.py -q`
Expected: PASS, 4 passed.

- [ ] **Step 5: Check the adapter suites, then commit**

Run: `uv run pytest tests/test_adapters_agent.py tests/test_adapters_subprocess.py tests/test_executor.py -q && just lint`
Expected: all pass.

```bash
git add src/kraft/adapters/agent.py tests/test_worker_identity.py
git commit -m "A worker session knows which work item it is"
```

---

## Phase 4 done when

An agent session the executor started reports `origin="worker"` from `resolve_context()`, and every act tool refuses to touch the item running it — enforcement, not documentation. Nothing about the agent command line changed, so the adapter is still vendor-neutral.

Phase 5 (`create_work_item_from_plan`, needing chain-template entry points) is the last piece and the only one with a genuine unknown.
