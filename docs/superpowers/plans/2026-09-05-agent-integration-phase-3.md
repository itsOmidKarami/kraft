# Agent Integration Phase 3 (Act Path) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A human working in an agent session can drive a stuck work item — approve or reject its gate, pause it, resume it with a steer — without switching to the browser. A *worker* session cannot do any of that to the item running it.

**Architecture:** Four functions on `client.py` over endpoints that exist, four MCP tools over those, and one guard: `_forbid_self_action` rejects act calls whose target resolves to the caller's own work item when `origin == "worker"`.

**Tech Stack:** Python 3.14, httpx, `mcp>=2` (`MCPServer`), FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-09-05-agent-integration-design.md`

## Global Constraints

- Phases 1–2 shipped: `client.py` has `_get`, `_post`, `http`, `base_url`, `resolve_context`, `list_work_items`, `get_work_item`, `search`, `create_work_item`, `ensure_repo`. Build on them.
- `from __future__ import annotations` in every new module.
- Async tests are sync functions wrapping `asyncio.run(...)`; use `run_with_app` from `tests/test_client_read.py` because `httpx.ASGITransport` does not run the app lifespan.
- Gate names are exactly `{"spec_approval", "plan_approval", "chain_finalized", "human_review_approval"}` (`src/kraft/templates.py:16`).
- `POST .../reject` requires a non-empty `note` (`GateReject`, `api.py:371-372`). `POST /resume` takes an optional `steer` (`Resume`, `api.py:383-384`).
- **No standalone `steer` tool** — `/steer` 409s unless the item is already paused (`api.py:628`). The surface is `pause()` then `resume(steer=...)`.
- Ruff on Python 3.14 wants PEP 758 syntax: `except OSError, ValueError:`, not parenthesised. Run `just fix` if lint objects.
- Run `just lint` before each commit.

---

### Task 1: The self-action guard

The load-bearing task of this phase. Everything after it depends on this being right, so it lands first and alone.

`resolve_context()` returns `origin`. When a **worker** session tries to approve, reject, pause, or resume the work item that is currently running it, the call is refused before it leaves the process. An agent approving its own gate would collapse the human-gate model (`01_conceptual_model.md` §6).

The guard lives client-side because the client is the only component that knows `origin` — by the time a request reaches `api.py`, it is just an authenticated local caller.

**Files:**
- Modify: `src/kraft/client.py`
- Test: `tests/test_client_guard.py`

**Interfaces:**
- Consumes: `resolve_context()` from phase 1.
- Produces: `client._forbid_self_action(work_item_id: str | None) -> str` — returns the resolved target id, or raises `PermissionError`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_client_guard.py`:

```python
"""A worker cannot act on the work item that is running it (design §6 rule 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kraft import client


@pytest.fixture
def run_dir(monkeypatch, tmp_path):
    base = tmp_path / "run"
    (base / "worktrees").mkdir(parents=True)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(base))
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    return base


def test_a_worker_cannot_act_on_its_own_item(run_dir, monkeypatch):
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "mine")
    with pytest.raises(PermissionError, match="its own work item"):
        client._forbid_self_action("mine")


def test_a_worker_acting_with_no_target_resolves_to_itself_and_is_refused(run_dir, monkeypatch):
    """The default target IS the caller's item, so a bare approve_gate() from a
    worker is exactly the self-approval the guard exists to stop."""
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "mine")
    with pytest.raises(PermissionError, match="its own work item"):
        client._forbid_self_action(None)


def test_a_worker_may_act_on_a_different_item(run_dir, monkeypatch):
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "mine")
    assert client._forbid_self_action("someone-elses") == "someone-elses"


def test_a_human_in_a_worktree_may_act_on_that_item(run_dir, monkeypatch):
    """A person standing in a worktree approving that item's gate is the whole
    point of the feature — origin is `user`, so the guard does not apply."""
    wt = run_dir / "worktrees" / "abc"
    wt.mkdir()
    monkeypatch.chdir(wt)
    assert client._forbid_self_action(None) == "abc"


def test_acting_with_no_target_and_no_context_says_so(run_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="no work item"):
        client._forbid_self_action(None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client_guard.py -q`
Expected: FAIL — `AttributeError: module 'kraft.client' has no attribute '_forbid_self_action'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/kraft/client.py`, after `resolve_context`:

```python
def _forbid_self_action(work_item_id: str | None) -> str:
    """Resolve the target of an act call, refusing a worker's own item.

    Design §6 rule 2. A worker session approving its own gate would collapse the
    human-gate model, so the refusal sits at the boundary the agent cannot route
    around rather than in a skill's prose. Client-side because `origin` is known
    only here: `api.py` sees an authenticated local caller either way.
    """
    resolved, origin = resolve_context()
    target = work_item_id or resolved
    if target is None:
        raise ValueError("no work item: pass an id, or run from a Kraft worktree")
    if origin == "worker" and target == resolved:
        raise PermissionError(
            f"a worker session cannot act on its own work item ({target}). "
            "Gates are where a human decides; report what you found instead."
        )
    return target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client_guard.py -q`
Expected: PASS, 5 passed.

- [ ] **Step 5: Commit**

Run: `just lint`

```bash
git add src/kraft/client.py tests/test_client_guard.py
git commit -m "A worker cannot approve its own gate"
```

---

### Task 2: The act functions

Four functions, each routing its target through the guard from Task 1.

**Files:**
- Modify: `src/kraft/client.py`
- Test: `tests/test_client_act.py`

**Interfaces:**
- Consumes: `_forbid_self_action`, `_post`, `get_work_item`.
- Produces:
  - `async client.approve_gate(gate: str | None = None, work_item_id: str | None = None) -> dict`
  - `async client.reject_gate(note: str, gate: str | None = None, work_item_id: str | None = None) -> dict`
  - `async client.pause(work_item_id: str | None = None) -> dict`
  - `async client.resume(steer: str | None = None, work_item_id: str | None = None) -> dict`

- [ ] **Step 1: Write the failing test**

Create `tests/test_client_act.py`:

```python
"""Driving a work item from a session: gates, pause, resume."""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest
from support.harness import fake_templates_dir, isolated_bd, make_repo
from test_client_read import run_with_app

from kraft import client

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    import kraft.api as api

    monkeypatch.setattr(
        client,
        "http",
        lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://kraft"
        ),
    )
    return api


def test_resume_starts_a_paused_item(wired, tmp_path):
    """A created-paused item is the phase 2 output; resume is how it begins."""
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("drive me", repo=str(repo))
        resumed = await client.resume(work_item_id=created["id"])
        return created, resumed

    created, resumed = run_with_app(wired, scenario)
    assert resumed["id"] == created["id"]


def test_pause_refuses_an_item_that_is_not_running(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("already paused", repo=str(repo))
        return await client.pause(work_item_id=created["id"])

    with pytest.raises(ValueError, match="409"):
        run_with_app(wired, scenario)


def test_approving_a_gate_that_is_not_pending_is_refused(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("no gate yet", repo=str(repo))
        return await client.approve_gate("spec_approval", work_item_id=created["id"])

    with pytest.raises(ValueError, match="409"):
        run_with_app(wired, scenario)


def test_an_unknown_gate_name_is_refused(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("bad gate", repo=str(repo))
        return await client.approve_gate("not_a_gate", work_item_id=created["id"])

    with pytest.raises(ValueError, match="404"):
        run_with_app(wired, scenario)


def test_rejecting_without_a_note_is_refused_before_the_request(wired, tmp_path):
    """A rejection with no reason strands whoever picks the work up next."""
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("no note", repo=str(repo))
        return await client.reject_gate("   ", "spec_approval", work_item_id=created["id"])

    with pytest.raises(ValueError, match="note"):
        run_with_app(wired, scenario)


def test_approve_gate_defaults_to_the_pending_gate(wired, tmp_path):
    """Naming the gate is the caller repeating what the item already knows."""
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("default gate", repo=str(repo))
        # nothing is pending on a never-started item, so this reports that
        return await client.approve_gate(work_item_id=created["id"])

    with pytest.raises(ValueError, match="no gate is pending"):
        run_with_app(wired, scenario)


@pytest.mark.parametrize(
    "call",
    [
        lambda wid: client.approve_gate("spec_approval", work_item_id=wid),
        lambda wid: client.reject_gate("no good", "spec_approval", work_item_id=wid),
        lambda wid: client.pause(work_item_id=wid),
        lambda wid: client.resume(work_item_id=wid),
    ],
    ids=["approve", "reject", "pause", "resume"],
)
def test_every_act_function_refuses_a_worker_acting_on_itself(wired, tmp_path, monkeypatch, call):
    """The guard has to be wired into all four, not just the one that was
    written first — a single unguarded act function is the whole hole."""
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("mine", repo=str(repo))
        monkeypatch.setenv("KRAFT_WORK_ITEM_ID", created["id"])
        return await call(created["id"])

    with pytest.raises(PermissionError, match="its own work item"):
        run_with_app(wired, scenario)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client_act.py -q`
Expected: FAIL — `AttributeError: module 'kraft.client' has no attribute 'resume'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/kraft/client.py`:

```python
async def _pending_gate_of(work_item_id: str) -> str:
    gate = (await get_work_item(work_item_id)).get("pending_gate")
    if not gate:
        raise ValueError(f"kraft: no gate is pending on {work_item_id}")
    return gate


async def approve_gate(gate: str | None = None, work_item_id: str | None = None) -> dict:
    """Approve the gate a work item is waiting on."""
    target = _forbid_self_action(work_item_id)
    gate = gate or await _pending_gate_of(target)
    status, body = await _post(f"/work-items/{target}/gates/{gate}/approve")
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    return body


async def reject_gate(
    note: str, gate: str | None = None, work_item_id: str | None = None
) -> dict:
    """Reject the gate a work item is waiting on. The note is required — a
    rejection with no reason strands whoever picks the work up next."""
    if not note or not note.strip():
        raise ValueError("a reject note is required: say what is wrong")
    target = _forbid_self_action(work_item_id)
    gate = gate or await _pending_gate_of(target)
    status, body = await _post(
        f"/work-items/{target}/gates/{gate}/reject", {"note": note.strip()}
    )
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    return body


async def pause(work_item_id: str | None = None) -> dict:
    """Stop the running node's sessions. Only an active item can be paused."""
    target = _forbid_self_action(work_item_id)
    status, body = await _post(f"/work-items/{target}/pause")
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    return body


async def resume(steer: str | None = None, work_item_id: str | None = None) -> dict:
    """Restart a paused item, optionally carrying a steer into the next attempt.

    This is also how a created-paused item is started for the first time: a NULL
    current_node_id resolves to node zero (design §6 rule 1).
    """
    target = _forbid_self_action(work_item_id)
    payload = {"steer": steer.strip()} if steer and steer.strip() else {}
    status, body = await _post(f"/work-items/{target}/resume", payload)
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    return body
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client_act.py -q`
Expected: PASS, 10 passed (the guard case is parametrized over all four act functions).

- [ ] **Step 5: Commit**

Run: `just lint`

```bash
git add src/kraft/client.py tests/test_client_act.py
git commit -m "A stuck work item can be driven from the session reading it"
```

---

### Task 3: The act tools on the MCP surface

**Files:**
- Modify: `src/kraft/mcp.py`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Consumes: `client.approve_gate`, `client.reject_gate`, `client.pause`, `client.resume`.
- Produces: MCP tools `approve_gate`, `reject_gate`, `pause_work_item`, `resume_work_item`.

- [ ] **Step 1: Write the failing test**

In `tests/test_mcp.py`, replace `test_the_tools_are_registered` and add one test:

```python
def test_the_tools_are_registered():
    assert {t.name for t in _tools()} == {
        "list_work_items",
        "get_work_item",
        "search",
        "create_work_item",
        "ensure_repo",
        "approve_gate",
        "reject_gate",
        "pause_work_item",
        "resume_work_item",
    }


def test_no_standalone_steer_tool_is_exposed():
    """`/steer` 409s unless the item is already paused, so the one moment an
    agent would reach for it is the one moment it fails. pause() then
    resume(steer=...) is the honest surface (design §9 phase 3)."""
    assert "steer" not in {t.name for t in _tools()}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp.py -q`
Expected: FAIL — the registered set is missing the four act tools.

- [ ] **Step 3: Write minimal implementation**

In `src/kraft/mcp.py`, inside `build()` before `return server`:

```python
    @server.tool()
    async def approve_gate(gate: str | None = None, work_item_id: str | None = None) -> dict:
        """Approve the human gate a Kraft work item is waiting on, letting the
        chain continue. With no gate name, approves whichever gate is pending.
        Gates are spec_approval, plan_approval, chain_finalized, and
        human_review_approval. Only a human should decide this — ask first."""
        return await client.approve_gate(gate, work_item_id)

    @server.tool()
    async def reject_gate(
        note: str, gate: str | None = None, work_item_id: str | None = None
    ) -> dict:
        """Reject the human gate a Kraft work item is waiting on, sending it back
        to be re-planned. `note` says what is wrong and is required. Only a human
        should decide this — ask first."""
        return await client.reject_gate(note, gate, work_item_id)

    @server.tool()
    async def pause_work_item(work_item_id: str | None = None) -> dict:
        """Stop a running Kraft work item's current attempt. Pair with
        resume_work_item to redirect work that is going wrong: there is no way to
        talk to a running agent, so steering means pausing and resuming."""
        return await client.pause(work_item_id)

    @server.tool()
    async def resume_work_item(
        steer: str | None = None, work_item_id: str | None = None
    ) -> dict:
        """Start or restart a paused Kraft work item. `steer` is carried into the
        next attempt's prompt. This is also how a work item created by
        create_work_item is started for the first time."""
        return await client.resume(steer, work_item_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp.py -q`
Expected: PASS, 6 passed (one `slow`).

- [ ] **Step 5: Update the installed skill, run everything, commit**

The skill `kraft init` writes describes only the read and write tools. Add an act
section to `SKILL` in `src/kraft/init.py`, after the "Reading" section:

```markdown
## Driving a stuck item

- `pause_work_item()` then `resume_work_item(steer="...")` - redirect work that
  is going wrong. There is no channel into a running agent, so this is what
  steering means.
- `approve_gate()` / `reject_gate(note="...")` - the human gates. **Ask the
  person before calling either.** A gate is where a human decides; if you are a
  Kraft worker session, you cannot act on your own item at all.
```

Run: `just test && just test-ui && just lint`
Expected: all pass.

```bash
git add src/kraft/mcp.py tests/test_mcp.py src/kraft/init.py
git commit -m "Gates and pause/resume reach the MCP surface"
```

---

## Phase 3 done when

From a session, a human can ask "what's blocked?", read the item, and approve or reject its gate, or pause and resume it with a steer — all without opening the board. A worker session calling any of those against the item running it gets a `PermissionError` before a request is sent.

Phase 4 (worker-side MCP: the executor injects `KRAFT_WORK_ITEM_ID` and the MCP config) is what makes the guard load-bearing rather than theoretical, and per spec §4 it must not ship without that injection.
