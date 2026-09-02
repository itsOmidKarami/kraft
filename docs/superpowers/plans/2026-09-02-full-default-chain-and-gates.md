# Full `default.yaml` Chain + Scheduled Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the 10-node `default.yaml` chain and the four scheduled human gates (approve / reject via the local API), with noop placeholder bindings for the nine hooks that have no plugin yet.

**Architecture:** A new `noop` builtin handler lets an unbound hook run as a zero-work task that produces a `done` `worker_sessions` row. The executor gains a `start_index` parameter and, after any node with a `gate_after`, emits `gate_requested` + sets `status = needs_human` and stops. Two new API endpoints (`.../gates/{gate}/approve`, `.../reject`) validate the pending gate (derived from the event log), write the outcome event, and — on approve — re-spawn the executor from the node after the gate.

**Tech Stack:** Python 3.12+, asyncio, SQLite (WAL), FastAPI, pytest. YAML templates.

**Spec:** `docs/superpowers/specs/2026-09-02-full-default-chain-and-gates-design.md`

## Global Constraints

- Python 3.12+, `asyncio` throughout; all DB writes go through `db.write(fn)` (single serialized writer), reads through `db.read(fn)`.
- No DB schema migration in this effort. `SCHEMA_VERSION` stays `1`. `events.type` is free-text (no CHECK); `work_items.status` stays within the existing CHECK set `('active', 'needs_human', 'completed')`.
- The four gate names, used verbatim everywhere: `spec_approval`, `plan_approval`, `chain_finalized`, `human_review_approval`.
- New event types, used verbatim: `gate_requested`, `gate_approved`, `gate_rejected`.
- `gate_requested` payload: `{"gate": <name>, "node_id": <id>}`. `gate_approved` payload: `{"gate": <name>}`. `gate_rejected` payload: `{"gate": <name>, "note": <string>}`.
- Reject is terminal in this effort: write `gate_rejected`, leave `status = needs_human`, do not re-invoke any hook, no counters.
- No pause/steer, no `paused` status, no `retry_counters`, no backward-motion coordinator.
- Match existing test style: `asyncio.run(scenario())` wrapping an inner async fn; helpers from `tests/support/harness.py` (`make_repo`, `isolated_bd`, `fake_registry`, `fake_templates_dir`); event-type assertions via a local `_events()` helper.
- Commit after each task with a `feat:` / `test:` / `refactor:` prefix.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/kraft/builtins.py` | builtin task handlers (`env_setup`, new `noop`) | modify |
| `src/kraft/executor.py` | chain walk; `_dispatch` routing; gate stop; `start_index` | modify |
| `src/kraft/store.py` | transactional write helpers; new gate helpers | modify |
| `src/kraft/templates.py` | registry + template loading; new `gate_after` validation | modify |
| `src/kraft/api.py` | local API; new approve/reject endpoints + `_pending_gate` | modify |
| `templates/default.yaml` | the 10-node chain | create |
| `templates/registry.yaml` | hook bindings; +9 noop bindings | modify |
| `tests/support/harness.py` | test fixtures; `fake_templates_dir` also ships `default.yaml` + noop hooks | modify |
| `tests/test_builtins.py` | `noop` handler unit test | modify |
| `tests/test_templates.py` | `default.yaml` validity; `gate_after` validation; updated registry-set assertion | modify |
| `tests/test_gates.py` | in-process gate walk (stop / approve / reject) | create |
| `tests/test_api.py` | approve/reject endpoints over HTTP | modify |

---

## Task 1: `noop` builtin handler

**Files:**
- Modify: `src/kraft/builtins.py`
- Modify: `src/kraft/executor.py` (`_dispatch`)
- Test: `tests/test_builtins.py`

**Interfaces:**
- Consumes: `store.create_session(c, *, id, work_item_id, node_id, hook_point, log_path, result_path)`, `store.session_exited(c, session_id, status)` — both already exist.
- Produces: `kraft.builtins.noop(db, run_dirs, *, session_id: str, work_item_id: str, node_id: str, hook_point: str) -> str` — always returns `"done"`, leaves one `worker_sessions` row with `status = "done"` and a readable `log_path`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_builtins.py`:

```python
def test_noop_creates_done_session_with_log(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c, id="w1", bead_id="B", title="t", repo="/r",
                    chain_template="default", chain_definition="{}",
                )
            )
            status = await kraft_builtins.noop(
                database, rd,
                session_id="s1", work_item_id="w1",
                node_id="spec", hook_point="on.spec.requested",
            )
            assert status == "done"
            row = database.read(
                lambda c: c.execute(
                    "SELECT hook_point, status, log_path FROM worker_sessions WHERE id='s1'"
                ).fetchone()
            )
            assert row["hook_point"] == "on.spec.requested"
            assert row["status"] == "done"
            assert Path(row["log_path"]).is_file()
        finally:
            await database.close()

    asyncio.run(scenario())
```

Add `from pathlib import Path` to the test file imports if not present.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_builtins.py::test_noop_creates_done_session_with_log -v`
Expected: FAIL — `AttributeError: module 'kraft.builtins' has no attribute 'noop'`

- [ ] **Step 3: Implement `noop`**

In `src/kraft/builtins.py`, add (keep the existing `env_setup`):

```python
async def noop(
    db, run_dirs, *, session_id: str, work_item_id: str, node_id: str, hook_point: str
) -> str:
    """Placeholder task for a hook with no plugin yet: records a done session, does no work."""
    log_path = run_dirs.logs / f"{session_id}.log"
    result_path = run_dirs.results / f"{session_id}.json"
    log_path.write_text(f"noop placeholder for {hook_point}\n")
    await db.write(
        lambda c: store.create_session(
            c,
            id=session_id,
            work_item_id=work_item_id,
            node_id=node_id,
            hook_point=hook_point,
            log_path=str(log_path),
            result_path=str(result_path),
        )
    )
    await db.write(lambda c: store.session_exited(c, session_id, "done"))
    return "done"
```

Add the import at the top of `builtins.py` if missing:

```python
from kraft import store
```

- [ ] **Step 4: Wire `noop` into `_dispatch`**

In `src/kraft/executor.py`, inside `_dispatch`, directly after the existing `env_setup` branch:

```python
    if kind == "builtin" and binding.get("handler") == "noop":
        return await _builtins.noop(db, run_dirs, hook_point=task_hook, **common)
```

(`common` already carries `session_id`, `work_item_id`, `node_id`.)

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_builtins.py -v`
Expected: PASS (new test + existing `test_env_setup_creates_worktree_and_branch`)

- [ ] **Step 6: Commit**

```bash
git add src/kraft/builtins.py src/kraft/executor.py tests/test_builtins.py
git commit -m "feat: noop builtin handler for unbound hooks"
```

---

## Task 2: `default.yaml` + noop registry bindings + `gate_after` validation

**Files:**
- Create: `templates/default.yaml`
- Modify: `templates/registry.yaml`
- Modify: `src/kraft/templates.py` (`load_templates`)
- Test: `tests/test_templates.py`

**Interfaces:**
- Consumes: existing `templates.load_registry`, `templates.load_templates`, `templates.materialize`.
- Produces: `templates.load_templates(dir, registry).valid["default"]` — a `Template` with 10 nodes; templates whose any node has a `gate_after` outside `{None, spec_approval, plan_approval, chain_finalized, human_review_approval}` land in `.invalid`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_templates.py`:

```python
GATE_NAMES = {"spec_approval", "plan_approval", "chain_finalized", "human_review_approval"}


def test_shipped_default_yaml_is_the_ten_node_chain():
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    ts = templates.load_templates(TEMPLATES_DIR, reg)
    assert "default" in ts.valid, ts.invalid
    nodes = ts.valid["default"].nodes
    assert [n["id"] for n in nodes] == [
        "spec", "plan", "chain_review", "env_setup", "implementation",
        "verify", "open_mr", "mr_checks", "human_review", "merge",
    ]
    gates = {n["id"]: n.get("gate_after") for n in nodes}
    assert gates["spec"] == "spec_approval"
    assert gates["plan"] == "plan_approval"
    assert gates["chain_review"] == "chain_finalized"
    assert gates["human_review"] == "human_review_approval"
    assert gates["env_setup"] is None and gates["merge"] is None


def test_unknown_gate_after_quarantines_template(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "quick-task.yaml": GOOD_TEMPLATE,
            "weirdgate.yaml": (
                "id: weirdgate\n"
                "nodes:\n"
                "  - { id: n1, tasks: [on.test.run], gate_after: bogus_gate }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    assert "quick-task" in ts.valid
    assert "weirdgate" in ts.invalid
    assert "bogus_gate" in ts.invalid["weirdgate"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_templates.py::test_shipped_default_yaml_is_the_ten_node_chain tests/test_templates.py::test_unknown_gate_after_quarantines_template -v`
Expected: FAIL — `default` not in `ts.valid` (KeyError/assert), and the weird-gate template is accepted as valid.

- [ ] **Step 3: Create `templates/default.yaml`**

```yaml
id: default
nodes:
  - id: spec
    tasks: [on.spec.requested]
    gate_after: spec_approval
  - id: plan
    tasks: [on.plan.requested]
    gate_after: plan_approval
  - id: chain_review
    tasks: [on.chain.review_ready]
    gate_after: chain_finalized
  - id: env_setup
    tasks: [on.env.prepare]
    gate_after: null
  - id: implementation
    tasks: [on.implementation.start]
    gate_after: null
  - id: verify
    tasks: [on.test.run, on.review.local.run]
    gate_after: null
  - id: open_mr
    tasks: [on.mr.open]
    gate_after: null
  - id: mr_checks
    tasks: [on.ci.poll, on.review.mr.run]
    gate_after: null
  - id: human_review
    tasks: [on.human_review.requested]
    gate_after: human_review_approval
  - id: merge
    tasks: [on.merge]
    gate_after: null
```

- [ ] **Step 4: Add the noop bindings to `templates/registry.yaml`**

Append under `hooks:` (keep the three existing entries unchanged):

```yaml
  # --- placeholder bindings: each replaced by its plugin effort (see 03_plugin_adapters) ---
  on.spec.requested:         { kind: builtin, handler: noop }
  on.plan.requested:         { kind: builtin, handler: noop }
  on.chain.review_ready:     { kind: builtin, handler: noop }
  on.review.local.run:       { kind: builtin, handler: noop }
  on.mr.open:                { kind: builtin, handler: noop }
  on.ci.poll:                { kind: builtin, handler: noop }
  on.review.mr.run:          { kind: builtin, handler: noop }
  on.human_review.requested: { kind: builtin, handler: noop }
  on.merge:                  { kind: builtin, handler: noop }
```

- [ ] **Step 5: Add `gate_after` validation to `load_templates`**

In `src/kraft/templates.py`, add a module constant near the top:

```python
_GATE_NAMES = {"spec_approval", "plan_approval", "chain_finalized", "human_review_approval"}
```

In `load_templates`, immediately after the existing node-shape check (the block that sets `invalid[tid] = f"template {tid!r}: each node needs a string 'id' ..."` and `continue`), add:

```python
        bad_gates = sorted(
            {
                n["gate_after"]
                for n in nodes
                if n.get("gate_after") is not None and n["gate_after"] not in _GATE_NAMES
            }
        )
        if bad_gates:
            invalid[tid] = f"template {tid!r}: unknown gate_after value(s) {bad_gates}"
            continue
```

- [ ] **Step 6: Update the registry-set assertion**

`tests/test_templates.py::test_shipped_yaml_parses_and_matches_spec` asserts `set(registry["hooks"]) == {"on.env.prepare", "on.implementation.start", "on.test.run"}`. Replace that assertion with:

```python
    assert {
        "on.env.prepare",
        "on.implementation.start",
        "on.test.run",
    } <= set(registry["hooks"])
    assert registry["hooks"]["on.spec.requested"] == {"kind": "builtin", "handler": "noop"}
    assert registry["hooks"]["on.merge"] == {"kind": "builtin", "handler": "noop"}
```

Leave the three exact per-hook assertions (`on.env.prepare`, `on.implementation.start`, `on.test.run`) in place.

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_templates.py -v`
Expected: PASS (all, including the two new tests and the updated assertion)

- [ ] **Step 8: Commit**

```bash
git add templates/default.yaml templates/registry.yaml src/kraft/templates.py tests/test_templates.py
git commit -m "feat: default.yaml 10-node chain + gate_after validation"
```

---

## Task 3: executor stops at gates; store gate helpers; resume honors gates

**Files:**
- Modify: `src/kraft/store.py`
- Modify: `src/kraft/executor.py`
- Test: `tests/test_gates.py` (create)

**Interfaces:**
- Consumes: `store.load_chain`, `store.mark_completed`, `store.mark_needs_human`, `store._now`, `events.append`, `beads.complete`, `executor._walk_node`.
- Produces:
  - `store.request_gate(conn, work_item_id, node_id, gate) -> None` — sets `status='needs_human'`, appends `gate_requested`.
  - `store.approve_gate(conn, work_item_id, gate) -> None` — sets `status='active'`, appends `gate_approved`.
  - `store.reject_gate(conn, work_item_id, gate, note) -> None` — bumps `updated_at` only, appends `gate_rejected`.
  - `executor.run(db, run_dirs, *, work_item_id, registry, bd_cwd=None, start_index=0) -> str` — return value is now one of `"completed" | "needs_human" | "awaiting_gate"`.
  - `executor.resume(...)` — same signature as today, but also returns `"awaiting_gate"` when a resumed chain reaches a gate.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gates.py`:

```python
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, store
from kraft.paths import RunDirs
from kraft.templates import load_registry, load_templates

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _default_template():
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["default"]


def _events(database, wid):
    return [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]


def _payloads(database, wid, etype):
    return [
        e["payload"]
        for e in database.read(lambda c: events.read_after(c, 0, wid))
        if e["type"] == etype
    ]


def _bd_status(repo, bead_id):
    out = subprocess.run(
        ["bd", "show", bead_id, "--json"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)[0]["status"]


def _gate_index(chain_json, gate):
    nodes = json.loads(chain_json)["nodes"]
    return next(i for i, n in enumerate(nodes) if n.get("gate_after") == gate)


def test_walk_stops_at_first_gate(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database, rd, title="make the failing test pass",
                repo=str(repo), template=_default_template(), bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "awaiting_gate"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "needs_human"
            assert row["current_node_id"] == "spec"
            types = _events(database, wid)
            assert types.count("gate_requested") == 1
            assert _payloads(database, wid, "gate_requested")[0]["gate"] == "spec_approval"
            assert "work_item_completed" not in types
        finally:
            await database.close()

    asyncio.run(scenario())


def test_approving_all_four_gates_completes_chain(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database, rd, title="make the failing test pass",
                repo=str(repo), template=_default_template(), bd_cwd=str(tracker),
            )
            chain_json = database.read(
                lambda c: c.execute(
                    "SELECT chain_definition FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )["chain_definition"]

            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            for gate in ("spec_approval", "plan_approval", "chain_finalized",
                         "human_review_approval"):
                assert result == "awaiting_gate"
                assert _payloads(database, wid, "gate_requested")[-1]["gate"] == gate
                await database.write(lambda c, g=gate: store.approve_gate(c, wid, g))
                start = _gate_index(chain_json, gate) + 1
                result = await executor.run(
                    database, rd, work_item_id=wid, registry=registry,
                    bd_cwd=str(tracker), start_index=start,
                )
            assert result == "completed"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, bead_id FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "completed"
            assert _bd_status(tracker, row["bead_id"]) == "closed"
            types = _events(database, wid)
            assert types.count("gate_requested") == 4
            assert types.count("gate_approved") == 4
            assert types.count("node_completed") == 10
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reject_is_terminal(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo),
                template=_default_template(), bd_cwd=str(tracker),
            )
            await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            await database.write(
                lambda c: store.reject_gate(c, wid, "spec_approval", "not specific enough")
            )
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id=?", (wid,)).fetchone()
            )
            assert row["status"] == "needs_human"
            rej = _payloads(database, wid, "gate_rejected")
            assert rej == [{"gate": "spec_approval", "note": "not specific enough"}]
            assert "gate_approved" not in _events(database, wid)
        finally:
            await database.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_gates.py -v`
Expected: FAIL — `AttributeError: module 'kraft.store' has no attribute 'request_gate'` (and `run()` has no `start_index` kwarg).

- [ ] **Step 3: Add the store gate helpers**

In `src/kraft/store.py`, add after `mark_completed`:

```python
def request_gate(conn: sqlite3.Connection, work_item_id, node_id, gate) -> None:
    conn.execute(
        "UPDATE work_items SET status = 'needs_human', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(
        conn, work_item_id, "gate_requested", {"gate": gate, "node_id": node_id}
    )


def approve_gate(conn: sqlite3.Connection, work_item_id, gate) -> None:
    conn.execute(
        "UPDATE work_items SET status = 'active', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "gate_approved", {"gate": gate})


def reject_gate(conn: sqlite3.Connection, work_item_id, gate, note) -> None:
    conn.execute(
        "UPDATE work_items SET updated_at = ? WHERE id = ?", (_now(), work_item_id)
    )
    events.append(conn, work_item_id, "gate_rejected", {"gate": gate, "note": note})
```

- [ ] **Step 4: Add the `_maybe_gate` helper to the executor**

In `src/kraft/executor.py`, add near `_walk_node`:

```python
async def _maybe_gate(db, work_item_id: str, node: dict) -> bool:
    """If the node ends in a gate, request it and return True (caller stops the walk)."""
    gate = node.get("gate_after")
    if not gate:
        return False
    await db.write(
        lambda c: store.request_gate(c, work_item_id, node["id"], gate)
    )
    return True
```

Add `from kraft import store` to `executor.py` imports if not already present (it is imported as `from kraft import store` already — verify).

- [ ] **Step 5: Thread `start_index` + gate stop through `run()`**

Replace the body of `run()` from the `await db.write(lambda c: store.load_chain(...))` line onward:

```python
async def run(
    db, run_dirs, *, work_item_id: str, registry: Registry, bd_cwd: str | None = None,
    start_index: int = 0,
) -> str:
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")
    chain = json.loads(row["chain_definition"])
    nodes = chain["nodes"]
    worktree = run_dirs.worktrees / work_item_id

    if start_index == 0:
        await db.write(lambda c: store.load_chain(c, work_item_id, nodes[0]["id"]))

    for node in nodes[start_index:]:
        result = await _walk_node(db, run_dirs, work_item_id, node, row, registry, worktree)
        if result == "needs_human":
            return "needs_human"
        if await _maybe_gate(db, work_item_id, node):
            return "awaiting_gate"

    await db.write(lambda c: store.mark_completed(c, work_item_id))
    try:
        await beads.complete(row["bead_id"], cwd=bd_cwd)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bead close failed for %s: %r", row["bead_id"], exc)
    return "completed"
```

- [ ] **Step 6: Make `resume()` honor gates**

In `src/kraft/executor.py`, in `resume()`, after the `_reconcile_current_node(...)` call returns non-`needs_human` and before the `for node in nodes[start + 1 :]:` loop, add:

```python
    if await _maybe_gate(db, work_item_id, nodes[start]):
        return "awaiting_gate"
```

And inside that following loop, after the `_walk_node` `needs_human` check, add the same gate stop:

```python
    for node in nodes[start + 1 :]:
        if (
            await _walk_node(db, run_dirs, work_item_id, node, row, registry, worktree)
            == "needs_human"
        ):
            return "needs_human"
        if await _maybe_gate(db, work_item_id, node):
            return "awaiting_gate"
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_gates.py tests/test_executor.py tests/test_resume.py -v`
Expected: PASS — new gate tests green; `test_executor.py` (quick-task, no gates) unchanged; `test_resume.py` unchanged (quick-task has no gates, `_maybe_gate` returns False).

- [ ] **Step 8: Commit**

```bash
git add src/kraft/store.py src/kraft/executor.py tests/test_gates.py
git commit -m "feat: executor stops at gate_after; gate approve/reject store helpers"
```

---

## Task 4: API approve / reject endpoints

**Files:**
- Modify: `tests/support/harness.py` (`fake_templates_dir` ships `default.yaml` + noop hooks)
- Modify: `src/kraft/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `store.approve_gate`, `store.reject_gate` (Task 3); `executor.run(..., start_index=)` (Task 3); `events.read_after(c, 0, wid)`; `api._spawn`, `api._guard`, `api._bd_cwd`, `api._work_item_row`.
- Produces:
  - `POST /work-items/{wid}/gates/{gate}/approve` → 200 (refreshed row) | 404 | 409.
  - `POST /work-items/{wid}/gates/{gate}/reject` with body `{"note": str}` → 200 | 404 | 409 | 422.
  - `api._pending_gate(st, wid) -> str | None`.

- [ ] **Step 1: Extend `fake_templates_dir` to ship `default.yaml`**

In `tests/support/harness.py`, change `fake_templates_dir` so it also copies `default.yaml` and registers the nine noop hooks:

```python
def fake_templates_dir(tmp_path: Path, agent_command: str) -> Path:
    d = tmp_path / "templates"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "quick-task.yaml", d / "quick-task.yaml")
    shutil.copy(_REPO_ROOT / "templates" / "default.yaml", d / "default.yaml")
    noop = {"kind": "builtin", "handler": "noop"}
    (d / "registry.yaml").write_text(
        yaml.safe_dump(
            {
                "hooks": {
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.implementation.start": {"kind": "agent", "command": agent_command},
                    "on.test.run": {
                        "kind": "subprocess",
                        "command": ["python", "-m", "pytest", "-q"],
                    },
                    "on.spec.requested": noop,
                    "on.plan.requested": noop,
                    "on.chain.review_ready": noop,
                    "on.review.local.run": noop,
                    "on.mr.open": noop,
                    "on.ci.poll": noop,
                    "on.review.mr.run": noop,
                    "on.human_review.requested": noop,
                    "on.merge": noop,
                }
            }
        )
    )
    return d
```

- [ ] **Step 2: Run the existing API suite to confirm no regression from the fixture change**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS — existing tests use `quick-task`; the extra `default.yaml` + hooks are inert for them. (`test_health_ok_and_degraded` still sees its `broken.yaml` in `invalid_templates`.)

- [ ] **Step 3: Write the failing endpoint tests**

Add to `tests/test_api.py`:

```python
def _post_default(client, repo):
    return client.post(
        "/work-items",
        json={"title": "make the failing test pass", "repo": str(repo),
              "chain_template": "default"},
    ).json()["id"]


def test_gate_approve_advances_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        item = client.get(f"/work-items/{wid}").json()
        assert item["status"] == "needs_human"

        r = client.post(f"/work-items/{wid}/gates/spec_approval/approve")
        assert r.status_code == 200, r.text
        # chain proceeds to the plan node, then stops at plan_approval
        seen = _poll_events(client, wid, "gate_approved")
        assert any(e["type"] == "node_started" and e["payload"]["node_id"] == "plan"
                   for e in client.get(f"/work-items/{wid}/events").json())


def test_gate_approve_wrong_gate_409(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        r = client.post(f"/work-items/{wid}/gates/plan_approval/approve")
        assert r.status_code == 409


def test_gate_unknown_name_404(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        assert client.post(f"/work-items/{wid}/gates/not_a_gate/approve").status_code == 404


def test_gate_reject_requires_note_and_is_terminal(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")

        assert client.post(f"/work-items/{wid}/gates/spec_approval/reject",
                           json={}).status_code == 422

        r = client.post(f"/work-items/{wid}/gates/spec_approval/reject",
                        json={"note": "too vague"})
        assert r.status_code == 200, r.text
        evts = client.get(f"/work-items/{wid}/events").json()
        rej = [e for e in evts if e["type"] == "gate_rejected"]
        assert rej and rej[0]["payload"] == {"gate": "spec_approval", "note": "too vague"}
        assert client.get(f"/work-items/{wid}").json()["status"] == "needs_human"


def test_gate_approve_unknown_work_item_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.post(
            "/work-items/nope/gates/spec_approval/approve"
        ).status_code == 404
```

- [ ] **Step 4: Run to verify failure**

Run: `uv run pytest tests/test_api.py -k gate -v`
Expected: FAIL — 404 (route not defined) on the approve/reject paths.

- [ ] **Step 5: Implement the endpoints**

In `src/kraft/api.py`:

Add the gate-name set near the top (after `TEMPLATES_DIR`):

```python
_GATE_NAMES = {"spec_approval", "plan_approval", "chain_finalized", "human_review_approval"}
```

Add the pending-gate helper (next to `_work_item_row`):

```python
def _pending_gate(st, wid: str) -> str | None:
    evts = st.db.read(lambda c: events.read_after(c, 0, wid))
    for e in reversed(evts):
        if e["type"] in ("gate_requested", "gate_approved", "gate_rejected"):
            return e["payload"]["gate"] if e["type"] == "gate_requested" else None
    return None


def _gate_node_index(chain: dict, gate: str) -> int:
    return next(i for i, n in enumerate(chain["nodes"]) if n.get("gate_after") == gate)
```

Add a request model near `NewWorkItem`:

```python
class GateReject(BaseModel):
    note: str
```

Add the endpoints (after `get_events`):

```python
@app.post("/work-items/{wid}/gates/{gate}/approve")
async def approve_gate(wid: str, gate: str, request: Request):
    st = request.app.state
    row = _work_item_row(st, wid)
    if gate not in _GATE_NAMES:
        raise HTTPException(404, f"unknown gate {gate!r}")
    if _pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")
    await st.db.write(lambda c: store.approve_gate(c, wid, gate))
    chain = json.loads(row["chain_definition"])
    start = _gate_node_index(chain, gate) + 1
    _spawn(
        request.app,
        wid,
        _guard(
            st.db,
            wid,
            executor.run(
                st.db, st.run_dirs, work_item_id=wid, registry=st.registry,
                bd_cwd=_bd_cwd(), start_index=start,
            ),
        ),
    )
    return {k: v for k, v in dict(_work_item_row(st, wid)).items()}


@app.post("/work-items/{wid}/gates/{gate}/reject")
async def reject_gate(wid: str, gate: str, body: GateReject, request: Request):
    st = request.app.state
    _work_item_row(st, wid)
    if gate not in _GATE_NAMES:
        raise HTTPException(404, f"unknown gate {gate!r}")
    if _pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")
    await st.db.write(lambda c: store.reject_gate(c, wid, gate, body.note))
    return {k: v for k, v in dict(_work_item_row(st, wid)).items()}
```

Note: `_work_item_row` returns a `sqlite3.Row`; `dict(row)` is the existing idiom for JSON-ing it (see `get_work_item`). If FastAPI cannot serialize a nested value, mirror `get_work_item`'s explicit `{k: row[k] for k in row.keys()}` shape instead.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS (all — new gate tests + existing)

- [ ] **Step 7: Full suite**

Run: `uv run pytest -q`
Expected: PASS — same count as before plus the new tests; the one pre-existing skip (e2e) still skips.

- [ ] **Step 8: Lint**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean. Fix any findings, re-run.

- [ ] **Step 9: Commit**

```bash
git add src/kraft/api.py tests/test_api.py tests/support/harness.py
git commit -m "feat: gate approve/reject API endpoints"
```

---

## Task 5: File the follow-up bead for the approve-then-crash gap

**Files:** none (bd only)

- [ ] **Step 1: Create the bead**

```bash
bd create --title="Resume 'active' work items with no live worker session after restart" \
  --type=bug --priority=2 \
  --description="Spec 2026-09-02-full-default-chain-and-gates §11 gap 1. After POST .../gates/{gate}/approve, store.approve_gate commits status='active' before _spawn schedules executor.run. If the orchestrator dies in that window, restart's reattach (kraft/reattach.py) only rebuilds tasks tied to a running/pending worker_sessions row; an 'active' work item with current_node_id on the just-approved gate node and no live session stalls silently. Fix belongs with the reattach/policy work (effort #2): after the session scan, for every work_items row still 'active' with no live/adopted task, re-spawn executor.run/resume from current_node_id."
```

- [ ] **Step 2: Note it in the spec**

Append to the spec's §11 gap 1 bullet: `Tracked as <bead-id>.`

```bash
git add docs/superpowers/specs/2026-09-02-full-default-chain-and-gates-design.md .beads/
git commit -m "docs: track approve-then-crash gap as a bead"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task |
|---|---|
| §1 `default.yaml` | Task 2 |
| §1 registry noop bindings | Task 2 |
| §1 `kraft.templates` gate validation | Task 2 |
| §1 `kraft.builtins` noop | Task 1 |
| §1 `kraft.executor` noop dispatch + gate stop | Tasks 1, 3 |
| §1 `kraft.store` gate helpers | Task 3 |
| §1 `kraft.api` approve/reject | Task 4 |
| §2.A gate-wait = `needs_human` + derived pending gate | Task 3 (`request_gate`), Task 4 (`_pending_gate`) |
| §2.B `run(start_index=)` resume-after-approve | Tasks 3, 4 |
| §2.C `noop` builtin handler | Task 1 |
| §3 `default.yaml` content | Task 2 Step 3 |
| §4 registry additions | Task 2 Step 4 |
| §5 `gate_after` validation | Task 2 Step 5 |
| §6.1 `_dispatch` noop branch | Task 1 Step 4 |
| §6.2 `run()` stop at gate | Task 3 Step 5 |
| §6.3 resume unchanged | **Deviation** — plan *does* change `resume()` (Task 3 Step 6) so a resumed `default` chain does not skip `human_review_approval`. Flagged to the user; spec §6.3 / §11 to be amended. |
| §7 store helpers | Task 3 Step 3 |
| §8 endpoints + `_pending_gate` + `NewWorkItem` default | Task 4 |
| §9 error table | Task 4 tests (404/409/422), Task 3 tests (task-failure path unchanged) |
| §10 testing | Tasks 1–4 test steps |
| §11 gap 1 (approve-then-crash) | Task 5 |
| §11 gap 2 (chain review inert) | no code — noop by design, covered by Task 2 |
| §11 gap 3 (reject terminal) | Task 3 (`reject_gate` writes event only) |
| §12 build order | Tasks 1→4 follow it |

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step has literal code. The one soft spot — Task 4 Step 5's note about `dict(row)` vs explicit key copy — gives both concrete alternatives and the existing precedent to copy.

**3. Type consistency:**
- `noop(db, run_dirs, *, session_id, work_item_id, node_id, hook_point) -> str` — defined Task 1, called Task 1 Step 4 with `hook_point=task_hook, **common`. Consistent.
- `request_gate(conn, work_item_id, node_id, gate)` / `approve_gate(conn, work_item_id, gate)` / `reject_gate(conn, work_item_id, gate, note)` — defined Task 3 Step 3, called Task 3 (`_maybe_gate`, tests) and Task 4 (endpoints) with matching arg order.
- `run(..., start_index=0)` — defined Task 3 Step 5, called Task 4 approve endpoint with `start_index=start`. Consistent.
- `_pending_gate(st, wid)` / `_gate_node_index(chain, gate)` — defined and called within Task 4.
- Event type strings and payload keys match the Global Constraints block everywhere they appear.

Deviation from spec §6.3 is deliberate and noted; everything else maps cleanly.
