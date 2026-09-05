# Agent Integration Phase 2 (Write Path) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent can create a Kraft work item that lands waiting for a human, and can register the repo it is standing in — and the board says honestly that such an item has never run.

**Architecture:** `POST /work-items` gains `autostart`; when false it inserts `status='paused'` and skips `_spawn`, so the existing `/resume` starts it at node zero. `client.py` gains two write functions and `mcp.py` two tools. `kraft init` installs the MCP registration and skills at user or repo scope.

**Tech Stack:** Python 3.14, httpx, `mcp>=2` (`MCPServer`), FastAPI, pytest; React + vitest + @testing-library/react for the one UI change.

**Spec:** `docs/superpowers/specs/2026-09-05-agent-integration-design.md`

## Global Constraints

- Phase 1 shipped: `src/kraft/client.py`, `src/kraft/mcp.py`, `kraft.cli.main(argv)`, `auth.ensure_mcp_token`. Build on them; do not re-create them.
- `from __future__ import annotations` in every new Python module.
- No new writer on `orchestrator.db`; everything goes through `api.py`.
- Async tests are sync functions wrapping `asyncio.run(...)`. `httpx.ASGITransport` does **not** run the app lifespan — use `run_with_app` from `tests/test_client_read.py`.
- `mcp>=2`: the class is `MCPServer` (`from mcp.server.mcpserver import MCPServer`), and `list_tools()` is async despite its annotation.
- Frontend tests: vitest, `render`/`screen` from `@testing-library/react`, `vi.spyOn(api, ...)` — follow `frontend/src/components/Gate.test.tsx`.
- Run `just lint` (Python) and `just test-ui` (frontend) before the relevant commits.

---

### Task 1: `autostart` — create a work item that waits

`POST /work-items` runs `executor.intake()` then `_spawn()` (`api.py:294`). `intake` INSERTs with `status` hardcoded `'active'` (`store.py:51`). This task makes both conditional.

Resuming such an item is already correct and must stay that way: `api.py:639` computes `start = next((i for i, n in enumerate(chain["nodes"]) if n["id"] == row["current_node_id"]), 0)`, and a NULL `current_node_id` falls to `0`. That default was not written for this purpose, so it gets a test naming it.

**Files:**
- Modify: `src/kraft/store.py:29-51` (`create_work_item`)
- Modify: `src/kraft/executor.py:49-76` (`intake`)
- Modify: `src/kraft/api.py:256-263` (`NewWorkItem`), `src/kraft/api.py:281-310` (create handler)
- Test: `tests/test_autostart.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `NewWorkItem.autostart: bool = True`; `store.create_work_item(..., status: str = "active")`; `executor.intake(..., status: str = "active")`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_autostart.py`:

```python
"""A work item can be created without starting it (design §6 rule 1)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _poll_for(client, wid, event_type, timeout=30):
    """Wait for an event type to land. The executor runs in a background task, so
    a status read straight after resume races it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matching = [
            e for e in client.get(f"/work-items/{wid}/events").json() if e["type"] == event_type
        ]
        if matching:
            return matching
        time.sleep(0.2)
    raise AssertionError(f"{event_type} never arrived for {wid}")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    import kraft.api as api

    with TestClient(api.app) as c:
        yield c


def test_autostart_false_lands_paused_and_never_ran(client, tmp_path):
    repo = make_repo(tmp_path)
    wid = client.post(
        "/work-items", json={"title": "wait for me", "repo": str(repo), "autostart": False}
    ).json()["id"]

    item = client.get(f"/work-items/{wid}").json()
    assert item["status"] == "paused"
    assert item["current_node_id"] is None
    # never started means no session was ever launched, not "a session was killed"
    assert item["worker_sessions"] == []


def test_autostart_defaults_true_so_the_ui_is_unaffected(client, tmp_path):
    repo = make_repo(tmp_path)
    wid = client.post("/work-items", json={"title": "go now", "repo": str(repo)}).json()["id"]
    assert client.get(f"/work-items/{wid}").json()["status"] == "active"


def test_resuming_a_never_started_item_begins_at_node_zero(client, tmp_path):
    """api.py's `next(..., 0)` default is what makes a NULL current_node_id
    resolve to the first node. Nothing else was written for this case, so if that
    expression is ever refactored, this test is the thing that notices."""
    repo = make_repo(tmp_path)
    wid = client.post(
        "/work-items", json={"title": "start me", "repo": str(repo), "autostart": False}
    ).json()["id"]
    first_node = client.get(f"/work-items/{wid}").json()["chain_definition"]["nodes"][0]["id"]

    assert client.post(f"/work-items/{wid}/resume", json={}).status_code == 200

    started = _poll_for(client, wid, "node_started")
    assert started[0]["payload"]["node_id"] == first_node, (
        "a never-started item must begin at the first node, not skip it"
    )


def test_pausing_a_never_started_item_is_refused(client, tmp_path):
    """It is already paused; /pause requires an active item."""
    repo = make_repo(tmp_path)
    wid = client.post(
        "/work-items", json={"title": "already waiting", "repo": str(repo), "autostart": False}
    ).json()["id"]
    assert client.post(f"/work-items/{wid}/pause").status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_autostart.py -q`
Expected: FAIL — `test_autostart_false_lands_paused_and_never_ran` asserts `status == "paused"` but gets `"active"`, because `autostart` is ignored today.

- [ ] **Step 3: Write minimal implementation**

In `src/kraft/store.py`, add a `status` parameter to `create_work_item` and use it in the INSERT. Change the signature to end with:

```python
    submodules: list[str] | None = None,
    root_merge_policy: str | None = None,
    status: str = "active",
) -> None:
```

and replace the whole `conn.execute(...)` call with this — the `'active'` literal
becomes a `?`, and `status` joins the tuple in that column's position:

```python
    conn.execute(
        "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
        "chain_definition, current_node_id, status, created_at, updated_at, "
        "submodules, root_merge_policy) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
        (
            id,
            bead_id,
            title,
            repo,
            chain_template,
            chain_definition,
            status,
            now,
            now,
            json.dumps(submodules) if submodules else None,
            root_merge_policy if submodules else None,
        ),
    )
```

In `src/kraft/executor.py`, add `status: str = "active"` to `intake`'s keyword-only parameters and forward it:

```python
    await db.write(
        lambda c: store.create_work_item(
            c,
            id=work_item_id,
            bead_id=bead_id,
            title=title,
            repo=repo,
            chain_template=template.id,
            chain_definition=chain_definition,
            submodules=submodules,
            root_merge_policy=root_merge_policy,
            status=status,
        )
    )
```

In `src/kraft/api.py`, add the field to `NewWorkItem`:

```python
class NewWorkItem(BaseModel):
    title: str
    repo: str
    chain_template: str = "quick-task"
    #: cross-repo (design 1g "Advanced · cross-repo"): submodule paths from the
    #: repo's .gitmodules, and what happens to the root pointer when they land
    submodules: list[str] = []
    root_merge_policy: str = "bump"
    #: False creates the item without running it (design §6 rule 1). An agent
    #: cannot spend tokens unattended; a human starts it from the board.
    autostart: bool = True
```

Pass it into `intake`, and make the spawn conditional. Replace the `_spawn(...)` call in the create handler with:

```python
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=body.title,
            repo=body.repo,
            template=template,
            bd_cwd=_bd_cwd(),
            submodules=body.submodules,
            root_merge_policy=body.root_merge_policy,
            status="active" if body.autostart else "paused",
        )
    except Exception as exc:  # noqa: BLE001 -- beads.intake raises several unrelated types
        raise HTTPException(502, f"bd intake failed: {exc}") from exc

    if not body.autostart:
        # Created, not started. `/resume` begins it at node zero, because a NULL
        # current_node_id falls through this handler's `next(..., 0)` default.
        return {"id": wid, "status": "paused"}

    _spawn(
```

Leave the rest of the existing `_spawn(...)` call and the handler's return exactly as they are.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_autostart.py -q`
Expected: PASS, 4 passed.

- [ ] **Step 5: Check nothing else regressed, then commit**

Run: `uv run pytest tests/test_api.py tests/test_pause_resume.py tests/test_resume.py tests/test_store.py tests/test_executor.py -q && just lint`
Expected: all pass.

```bash
git add src/kraft/store.py src/kraft/executor.py src/kraft/api.py tests/test_autostart.py
git commit -m "A work item can be created without being started"
```

---

### Task 2: The board tells the truth about work that never ran

`PausedCard.tsx` assumes a pause interrupted something: `const attempt = paused.length ? paused[0].attempt + 1 : 2;` and a hint reading `relaunches {paused[0]?.hook_point ?? item.current_node_id} as attempt {attempt}`. For an agent-created item that is "relaunches  as attempt 2" — false in every word.

**Files:**
- Modify: `frontend/src/components/PausedCard.tsx`
- Test: `frontend/src/components/PausedCard.test.tsx` (create)

**Interfaces:**
- Consumes: `autostart` from Task 1 (this is the UI for items it creates).
- Produces: no new exports; `PausedCard` gains a never-started branch.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/PausedCard.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { PausedCard } from "./PausedCard";

const started = { id: "w1", current_node_id: "implement" } as never;
const neverStarted = { id: "w2", current_node_id: null } as never;

const session = {
  node_id: "implement",
  status: "paused",
  attempt: 1,
  hook_point: "implement",
} as never;

describe("PausedCard", () => {
  it("an interrupted item offers to resume the attempt it killed", () => {
    render(<PausedCard item={started} sessions={[session]} />);
    expect(screen.getByRole("button", { name: /resume with steer/i })).toBeTruthy();
    expect(screen.getByText(/as attempt 2/i)).toBeTruthy();
  });

  it("an item that never ran offers to start it, not resume it", () => {
    render(<PausedCard item={neverStarted} sessions={[]} />);
    expect(screen.getByRole("button", { name: /^start/i })).toBeTruthy();
    expect(screen.queryByText(/attempt/i)).toBeNull();
    expect(screen.queryByText(/relaunches/i)).toBeNull();
  });

  it("starting a never-run item resumes it through the same endpoint", async () => {
    const spy = vi.spyOn(api, "resumeWorkItem").mockResolvedValue();
    render(<PausedCard item={neverStarted} sessions={[]} />);
    await userEvent.click(screen.getByRole("button", { name: /^start/i }));
    expect(spy).toHaveBeenCalledWith("w2", undefined);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test-ui PausedCard`
Expected: FAIL — no button matching `/^start/i`; the card renders "Resume with steer".

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/components/PausedCard.tsx`, after the `attempt` line, add:

```tsx
  // An agent-created item (design §6 rule 1) is paused without ever having run:
  // no sessions, no current node. "Resume attempt 2" would be false in every word.
  const neverStarted = item.current_node_id === null;
```

Then branch the render. Insert this immediately after `const [err, setErr] = useState<string | null>(null);`'s dependent `resume` definition, before the existing `return`:

```tsx
  if (neverStarted) {
    return (
      <div className="card elev-sm paused-card" data-testid="paused-card">
        <p className="field-hint">
          Waiting to start · created by an agent, so nothing runs until you say so
        </p>
        <div className="gate-actions capped-actions">
          <button className="btn btn-primary" disabled={busy} onClick={() => resume(false)}>
            <Play size={14} />
            Start
          </button>
        </div>
        {err && <p className="form-error">{err}</p>}
      </div>
    );
  }
```

The steer box is deliberately absent: steer is context carried into the *next* attempt of something already tried, and there is no previous attempt to steer away from.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test-ui PausedCard`
Expected: PASS, 3 passed.

- [ ] **Step 5: Run the whole frontend suite and commit**

Run: `just test-ui && just lint`
Expected: all pass.

```bash
git add frontend/src/components/PausedCard.tsx frontend/src/components/PausedCard.test.tsx
git commit -m "The board stops calling never-run work 'attempt 2'"
```

---

### Task 3: `create_work_item` and `ensure_repo` in the client

Two write functions. `ensure_repo` is idempotent by construction: `POST /repos` already 409s on a path it holds (`api.py:1007-1008`), so a 409 is success, not an error.

**Files:**
- Modify: `src/kraft/client.py`
- Test: `tests/test_client_write.py`

**Interfaces:**
- Consumes: `_detail`, `http`, `resolve_context` from phase 1; `autostart` from Task 1.
- Produces:
  - `async client.create_work_item(title: str, repo: str | None = None, chain_template: str = "quick-task") -> dict`
  - `async client.ensure_repo(path: str | None = None) -> dict`

- [ ] **Step 1: Write the failing test**

Create `tests/test_client_write.py`:

```python
"""The write half of the agent surface: create an item, register a repo."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from support.harness import fake_templates_dir, isolated_bd, make_repo

from kraft import client
from test_client_read import run_with_app

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


def test_create_work_item_never_starts_it(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("from an agent", repo=str(repo))
        return created, await client.get_work_item(created["id"])

    created, item = run_with_app(wired, scenario)
    assert created["status"] == "paused"
    # an agent cannot spend tokens unattended — design §6 rule 1
    assert item["status"] == "paused"
    assert item["current_node_id"] is None


def test_create_work_item_without_a_repo_or_a_context_says_so(wired, tmp_path, monkeypatch):
    """With no repo argument and no worktree, there is nothing to guess."""

    async def scenario():
        return await client.create_work_item("no repo anywhere")

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="repo"):
        run_with_app(wired, scenario)


def test_ensure_repo_registers_a_repo_kraft_has_not_seen(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        return await client.ensure_repo(str(repo))

    entry = run_with_app(wired, scenario)
    assert entry["path"] == str(Path(repo).resolve())


def test_ensure_repo_is_idempotent(wired, tmp_path):
    """A 409 from POST /repos means 'already connected', which is the goal state."""
    repo = make_repo(tmp_path)

    async def scenario():
        await client.ensure_repo(str(repo))
        return await client.ensure_repo(str(repo))

    entry = run_with_app(wired, scenario)
    assert entry["path"] == str(Path(repo).resolve())
    assert entry["already_connected"] is True


def test_ensure_repo_reports_a_path_that_is_not_a_repo(wired, tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    async def scenario():
        return await client.ensure_repo(str(plain))

    with pytest.raises(ValueError, match="400"):
        run_with_app(wired, scenario)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client_write.py -q`
Expected: FAIL — `AttributeError: module 'kraft.client' has no attribute 'create_work_item'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/kraft/client.py`. First a POST helper mirroring `_get`:

```python
async def _post(path: str, payload: dict | None = None) -> tuple[int, dict]:
    """Status alongside the body: some callers treat a 4xx as a normal outcome
    (a 409 from POST /repos means the repo is already connected)."""
    async with http() as session:
        response = await session.post(path, json=payload or {})
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text}
    return response.status_code, body
```

Then the two functions:

```python
async def create_work_item(
    title: str, repo: str | None = None, chain_template: str = "quick-task"
) -> dict:
    """Create a work item. It lands paused: an agent files work, a human starts it.

    `repo` defaults to the repo of the work item this session is standing in,
    which is the common case for a worker filing follow-up work.
    """
    if repo is None:
        work_item_id, _origin = resolve_context()
        if work_item_id is not None:
            repo = (await get_work_item(work_item_id)).get("repo")
    if not repo:
        raise ValueError(
            "no repo: pass one, or run from a Kraft worktree so the repo can be resolved"
        )
    status, body = await _post(
        "/work-items",
        {"title": title, "repo": repo, "chain_template": chain_template, "autostart": False},
    )
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    return {"id": body["id"], "status": body.get("status", "paused"), "title": title}


async def ensure_repo(path: str | None = None) -> dict:
    """Register a repo with Kraft if it is not already connected.

    Idempotent by construction: `POST /repos` 409s on a path it already holds,
    and "already connected" is the goal state, not a failure.
    """
    path = path or os.getcwd()
    status, body = await _post("/repos", {"path": path})
    if status == 409:
        probe_status, probed = await _post("/repos/probe", {"path": path})
        if probe_status >= 400:
            raise ValueError(f"kraft {probe_status}: {probed.get('detail', probed)}")
        return {**probed, "already_connected": True}
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    return {**body, "already_connected": False}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client_write.py -q`
Expected: PASS, 5 passed.

- [ ] **Step 5: Commit**

Run: `just lint`

```bash
git add src/kraft/client.py tests/test_client_write.py
git commit -m "An agent can file work and connect the repo it is standing in"
```

---

### Task 4: The write tools on the MCP surface

Two more entries in the dispatch table. The docstrings carry the rule an agent needs to know — that creating an item does not run it — because that is where an agent reads it.

**Files:**
- Modify: `src/kraft/mcp.py`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Consumes: `client.create_work_item`, `client.ensure_repo` from Task 3.
- Produces: MCP tools `create_work_item`, `ensure_repo`.

- [ ] **Step 1: Write the failing test**

In `tests/test_mcp.py`, replace the body of `test_the_read_tools_are_registered` and add a new test:

```python
def test_the_tools_are_registered():
    assert {t.name for t in _tools()} == {
        "list_work_items",
        "get_work_item",
        "search",
        "create_work_item",
        "ensure_repo",
    }


def test_create_work_item_tells_the_agent_it_will_not_run():
    """An agent that thinks create means start will file work and walk away."""
    create = next(t for t in _tools() if t.name == "create_work_item")
    assert "paused" in create.description.lower()
```

Rename the old test's remaining references accordingly (the file previously asserted a three-tool set).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp.py -q`
Expected: FAIL — the registered set is the three read tools; `create_work_item` is missing.

- [ ] **Step 3: Write minimal implementation**

In `src/kraft/mcp.py`, inside `build()` and before `return server`:

```python
    @server.tool()
    async def create_work_item(
        title: str, repo: str | None = None, chain_template: str = "quick-task"
    ) -> dict:
        """File a new Kraft work item. It is created **paused** and does not run:
        a human starts it from the board. Use this to hand finished work off to
        Kraft rather than doing it in this session. `repo` defaults to the repo
        of the work item this session is standing in."""
        return await client.create_work_item(title, repo, chain_template)

    @server.tool()
    async def ensure_repo(path: str | None = None) -> dict:
        """Connect a repository to Kraft if it is not already connected, so work
        items can be created against it. Idempotent — safe to call every time.
        `path` defaults to the current working directory."""
        return await client.ensure_repo(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp.py -q`
Expected: PASS, 4 passed (one is `slow`).

- [ ] **Step 5: Commit**

Run: `just lint`

```bash
git add src/kraft/mcp.py tests/test_mcp.py
git commit -m "The write tools reach the MCP surface"
```

---

### Task 5: `kraft init` and the skills it installs

Two scopes. User scope delegates registration to `claude mcp add` rather than editing `~/.claude.json`, which is large, shared, agent-owned state. Repo scope writes `.mcp.json` directly — small, documented schema, and the file belongs to the repo Kraft was pointed at.

This task also carries the §8.1 amendment to `01_conceptual_model.md`, because it is the change that starts writing repo-owned files.

**Files:**
- Create: `src/kraft/init.py`
- Modify: `src/kraft/cli.py` (`main` dispatch)
- Modify: `docs/consolidated/01_conceptual_model.md` (§1.4)
- Test: `tests/test_init.py`

**Interfaces:**
- Consumes: `kraft.cli.main(argv)` from phase 1 Task 1.
- Produces: `kraft.init.install(repo_scope: bool, cwd: Path, run: Callable = subprocess.run) -> list[str]` returning the paths written, and a `kraft init` subcommand.

- [ ] **Step 1: Write the failing test**

Create `tests/test_init.py`:

```python
"""`kraft init` — what it writes, and what it refuses to touch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kraft import init


class _Recorder:
    """Stands in for subprocess.run so no test shells out to a real `claude`."""

    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)

        class Result:
            pass

        result = Result()
        result.returncode = self.returncode
        result.stdout = ""
        result.stderr = "" if self.returncode == 0 else "claude: no such command"
        return result


def test_repo_scope_writes_mcp_json_and_a_skill(tmp_path):
    written = init.install(repo_scope=True, cwd=tmp_path, run=_Recorder())

    config = json.loads((tmp_path / ".mcp.json").read_text())
    assert config["mcpServers"]["kraft"]["command"] == "kraft"
    assert config["mcpServers"]["kraft"]["args"] == ["mcp"]
    assert (tmp_path / ".claude" / "skills" / "kraft" / "SKILL.md").is_file()
    assert str(tmp_path / ".mcp.json") in written


def test_repo_scope_does_not_touch_claude_md(tmp_path):
    """Design §1.4: Kraft's process never lands in an ambient, repo-owned file.
    `kraft init` writes files a human opted into, and CLAUDE.md is not one."""
    (tmp_path / "CLAUDE.md").write_text("# mine\n")
    init.install(repo_scope=True, cwd=tmp_path, run=_Recorder())
    assert (tmp_path / "CLAUDE.md").read_text() == "# mine\n"


def test_repo_scope_preserves_other_servers_in_an_existing_mcp_json(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "other-server"}}})
    )
    init.install(repo_scope=True, cwd=tmp_path, run=_Recorder())

    servers = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"other", "kraft"}


def test_user_scope_delegates_registration_to_the_claude_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    recorder = _Recorder()
    init.install(repo_scope=False, cwd=tmp_path, run=recorder)

    assert recorder.calls, "user scope must shell out rather than edit ~/.claude.json"
    cmd = recorder.calls[0]
    assert cmd[:3] == ["claude", "mcp", "add"]
    assert "--scope" in cmd and "user" in cmd
    # ~/.claude.json is large shared user state; an installer must not rewrite it
    assert not (tmp_path / ".claude.json").exists()


def test_user_scope_still_installs_the_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    init.install(repo_scope=False, cwd=tmp_path, run=_Recorder())
    assert (tmp_path / ".claude" / "skills" / "kraft" / "SKILL.md").is_file()


def test_a_missing_claude_cli_reports_the_command_instead_of_guessing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        init.install(repo_scope=False, cwd=tmp_path, run=_Recorder(returncode=1))
    assert "claude mcp add" in str(exc.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_init.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.init'`

- [ ] **Step 3: Write minimal implementation**

Create `src/kraft/init.py`:

```python
"""`kraft init` — install the agent-facing surface at user or repo scope.

Design §8. User scope delegates MCP registration to the `claude` CLI:
`~/.claude.json` is large, shared, agent-owned user state, and hand-editing it is
how an installer corrupts somebody's whole configuration. The repo-scope
`.mcp.json` is written directly — small documented schema, and the file belongs
to the repo the human pointed Kraft at.

Nothing here touches `CLAUDE.md`, `AGENTS.md`, or any other ambient repo file
(§1.4 as amended in §8.1): every path written is one `kraft init` was asked for.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

SKILL = """---
name: kraft
description: Use when work should be handed to Kraft rather than done in this
  session - filing a work item after a spec and plan are agreed, checking what
  Kraft is running, or connecting the current repo to Kraft.
---

# Kraft

Kraft runs semi-autonomous work items as chains, with human gates. This session
talks to it over MCP.

## Handing work off

After a spec and plan are agreed and the work is bigger than this session should
do inline, file it:

1. `ensure_repo()` - connects the current repo if Kraft has not seen it.
2. `create_work_item(title)` - files the work.

**`create_work_item` does not start anything.** The item lands paused and a human
starts it from the board. Say so when you report back; do not tell the user work
is underway.

## Reading

- `list_work_items(status)` - the board. `status="paused"` is what is waiting on a human.
- `get_work_item()` - the item this session is standing in, when the cwd is a Kraft worktree.
- `search(q)` - specs, plans, and session summaries across every connected repo.
  Worth a call before writing a spec, to find whether the decision was already made.
"""


def _write_skill(root: Path) -> str:
    path = root / "skills" / "kraft" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SKILL)
    return str(path)


def _write_repo_mcp_json(cwd: Path) -> str:
    """Merge into any existing .mcp.json — other servers are not ours to drop."""
    path = cwd / ".mcp.json"
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError):
        config = {}
    config.setdefault("mcpServers", {})["kraft"] = {"command": "kraft", "args": ["mcp"]}
    path.write_text(json.dumps(config, indent=2) + "\n")
    return str(path)


def install(
    repo_scope: bool,
    cwd: Path | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[str]:
    """Install the Kraft agent surface. Returns the paths written."""
    cwd = Path(cwd or Path.cwd())
    if repo_scope:
        return [_write_repo_mcp_json(cwd), _write_skill(cwd / ".claude")]

    command = ["claude", "mcp", "add", "--scope", "user", "kraft", "--", "kraft", "mcp"]
    try:
        result = run(command, capture_output=True, text=True)
    except FileNotFoundError:
        result = None
    if result is None or result.returncode != 0:
        # A missing agent CLI is a thing to report, not to work around by
        # rewriting user state we do not own.
        raise SystemExit(
            "kraft init: could not register the MCP server automatically. Run this yourself:\n"
            f"  {' '.join(command)}"
        )
    return [_write_skill(Path.home() / ".claude")]
```

In `src/kraft/cli.py`, add the branch to `main`, immediately before the `raise SystemExit(...)` line:

```python
    if args[0] == "init":
        from kraft.init import install

        for path in install(repo_scope="--repo" in args[1:]):
            print(f"kraft: wrote {path}")
        return
```

and update the unknown-command message to mention it:

```python
    raise SystemExit(
        f"kraft: unknown command {args[0]!r} (try `kraft`, `kraft mcp`, or `kraft init`)"
    )
```

Then amend `docs/consolidated/01_conceptual_model.md` §1.4. Find the principle beginning "**The app's process never leaks into the repo.**" and append to it:

```markdown
   This governs what Kraft's *executor* injects into sessions it starts: that
   context is per-invocation and is never persisted to a repo-owned file. Files a
   human explicitly opts into by running `kraft init --repo` are that human's
   choice, not Kraft leaking its process into a repo — the distinction is ambient
   versus opted-into. See `docs/superpowers/specs/2026-09-05-agent-integration-design.md` §8.1.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_init.py tests/test_cli.py -q`
Expected: PASS, 14 passed.

- [ ] **Step 5: Run everything and commit**

Run: `just test && just test-ui && just lint`
Expected: all pass.

```bash
git add src/kraft/init.py src/kraft/cli.py tests/test_init.py docs/consolidated/01_conceptual_model.md
git commit -m "kraft init installs the agent surface, at the scope you choose"
```

---

## Phase 2 done when

An agent can run `ensure_repo()` then `create_work_item("...")`, and the work lands on the board as **Waiting to start** with a single Start button — no false "attempt 2", no tokens spent until a human clicks it. `kraft init` installs that surface at user or repo scope, and `01_conceptual_model.md` §1.4 says what repo-scope installation means.

Phase 3 (act tools: gates, steer, pause/resume, and the worker self-action guard) builds on `client._post` and the tool table this phase establishes.
