# Intake From Existing Artifacts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a work item start from a spec and/or plan that already exists — attach the documents at intake, drop the chain phases they satisfy, and hand them to the agent.

**Architecture:** An `attachments` JSON column on `work_items` carries `[{kind, path}]` chosen at intake. `materialize()` drops nodes whose `gate_after` is a gate an attachment satisfies, so the item's stored chain is genuinely short. `_dispatch` appends the paths to every agent instruction. `env_setup` copies attachments into the worktree so uncommitted documents exist for the agent. Visibility reuses existing surfaces: a read-time join in the indexer, an intake event, and a badge.

**Tech Stack:** Python 3.14 / FastAPI / SQLite / pytest; React + TypeScript / Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-05-intake-from-existing-artifacts-design.md`

**Bead:** Kraft-dgh

## Global Constraints

- Attachment kinds are exactly `"spec"` and `"plan"`. At most one of each per work item.
- Gate mapping is fixed: `spec` → `spec_approval`, `plan` → `plan_approval`. Both gate names are already in `templates.GATE_NAMES`.
- Attachment `path` is **repo-relative**, always stored normalized (`str(target.relative_to(root))`).
- Path validation is a trust boundary: resolve under the repo root and reject anything that escapes, before any filesystem read or write.
- Existence is checked against the working tree, never `HEAD` — an uncommitted document is legal input.
- A template with no `spec_approval` / `plan_approval` gate is not an error; trimming is a no-op and the attachment still rides along as agent context.
- `instruction_override` (the fix-loop prompt) always wins over the attachment block.
- Run backend tests with `just test`, frontend with `just test-ui`. Lint with `just lint`.

---

### Task 1: Schema and store

**Files:**
- Modify: `src/kraft/db.py:16` (`SCHEMA_VERSION`), `src/kraft/db.py:19-37` (`SCHEMA_SQL` work_items), `src/kraft/db.py:144-147` (`_MIGRATIONS`)
- Modify: `src/kraft/store.py:29-70` (`create_work_item`)
- Test: `tests/test_db.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `work_items.attachments` (TEXT, JSON list or NULL); `store.create_work_item(..., attachments: list[dict] | None = None)`; event `work_item_attachments` with payload `{"attachments": [{"kind": ..., "path": ...}]}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_store.py`:

```python
def test_create_work_item_stores_attachments_and_events_them(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/repo",
        chain_template="default",
        chain_definition="{}",
        attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
    )
    row = conn.execute("SELECT attachments FROM work_items WHERE id='w1'").fetchone()
    assert json.loads(row["attachments"]) == [
        {"kind": "plan", "path": ".engineering/plans/p.md"}
    ]
    types = [e["type"] for e in events.read_after(conn, 0, "w1")]
    assert "work_item_attachments" in types


def test_create_work_item_without_attachments_stores_null(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/repo",
        chain_template="default",
        chain_definition="{}",
    )
    row = conn.execute("SELECT attachments FROM work_items WHERE id='w1'").fetchone()
    assert row["attachments"] is None
    types = [e["type"] for e in events.read_after(conn, 0, "w1")]
    assert "work_item_attachments" not in types
```

Add `import json` and `from kraft import db, events, store` at the top of the file if they are not already there.

In `tests/test_db.py`:

```python
def test_migration_7_adds_attachments_column(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    conn.execute("PRAGMA user_version = 7")
    conn.execute("ALTER TABLE work_items DROP COLUMN attachments")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert "attachments" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test -k "attachments_column or stores_attachments or stores_null"`
Expected: FAIL — `create_work_item() got an unexpected keyword argument 'attachments'`, and no such column.

- [ ] **Step 3: Add the column and the migration**

In `src/kraft/db.py`, bump the version:

```python
SCHEMA_VERSION = 8
```

In `SCHEMA_SQL`, inside `CREATE TABLE work_items`, after `root_merge_policy TEXT,`:

```sql
  -- documents attached at intake (Kraft-dgh): [{"kind": "spec"|"plan", "path": ...}]
  attachments      TEXT,
```

In `_MIGRATIONS`, add:

```python
    7: ["ALTER TABLE work_items ADD COLUMN attachments TEXT"],
```

- [ ] **Step 4: Store and event the attachments**

In `src/kraft/store.py`, extend `create_work_item`:

```python
def create_work_item(
    conn: sqlite3.Connection,
    *,
    id,
    bead_id,
    title,
    repo,
    chain_template,
    chain_definition,
    submodules: list[str] | None = None,
    root_merge_policy: str | None = None,
    attachments: list[dict] | None = None,
) -> None:
```

Extend the docstring with one line:

```python
    """...

    `attachments` are the spec/plan documents chosen at intake (Kraft-dgh),
    stored as JSON for the same reason `submodules` is: chosen once, never
    queried across items.
    """
```

Change the INSERT to carry the column:

```python
    conn.execute(
        "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
        "chain_definition, current_node_id, status, created_at, updated_at, "
        "submodules, root_merge_policy, attachments) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, 'active', ?, ?, ?, ?, ?)",
        (
            id,
            bead_id,
            title,
            repo,
            chain_template,
            chain_definition,
            now,
            now,
            json.dumps(submodules) if submodules else None,
            root_merge_policy if submodules else None,
            json.dumps(attachments) if attachments else None,
        ),
    )
```

And after the existing `work_item_created` append:

```python
    if attachments:
        # Its own event, not a field on work_item_created: the timeline has to
        # explain why this item's chain has no spec node.
        events.append(conn, id, "work_item_attachments", {"attachments": attachments})
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `just test -k "attachments_column or stores_attachments or stores_null"`
Expected: PASS

- [ ] **Step 6: Run the full backend suite**

Run: `just test`
Expected: PASS — no other caller passes `attachments`, and the parameter defaults to `None`.

- [ ] **Step 7: Commit**

```bash
git add src/kraft/db.py src/kraft/store.py tests/test_db.py tests/test_store.py
git commit -m "feat(intake): store attachments on work items"
```

---

### Task 2: Chain trimming in materialize

**Files:**
- Modify: `src/kraft/templates.py:17` (constants), `src/kraft/templates.py:147-159` (`materialize`)
- Test: `tests/test_templates.py`

**Interfaces:**
- Consumes: `templates.Template` and `templates.GATE_NAMES`, both existing.
- Produces: `templates.ATTACHMENT_GATES: dict[str, str]` mapping `"spec"`/`"plan"` to gate names; `materialize(template: Template, *, satisfied_gates: frozenset[str] = frozenset()) -> dict`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_templates.py`:

```python
def test_materialize_drops_nodes_whose_gate_is_satisfied():
    template = Template(
        id="t",
        nodes=[
            {"id": "spec", "tasks": ["on.spec.requested"], "gate_after": "spec_approval"},
            {"id": "plan", "tasks": ["on.plan.requested"], "gate_after": "plan_approval"},
            {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
        ],
    )
    out = materialize(template, satisfied_gates=frozenset({"spec_approval", "plan_approval"}))
    assert [n["id"] for n in out["nodes"]] == ["implementation"]


def test_materialize_keys_on_the_gate_not_the_node_id():
    # A custom template may name the node anything; the gate is the vocabulary.
    template = Template(
        id="t",
        nodes=[
            {"id": "write-the-plan", "tasks": ["on.plan.requested"], "gate_after": "plan_approval"},
            {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
        ],
    )
    out = materialize(template, satisfied_gates=frozenset({"plan_approval"}))
    assert [n["id"] for n in out["nodes"]] == ["implementation"]


def test_materialize_without_satisfied_gates_is_unchanged():
    template = Template(
        id="t",
        nodes=[{"id": "spec", "tasks": ["on.spec.requested"], "gate_after": "spec_approval"}],
    )
    assert [n["id"] for n in materialize(template)["nodes"]] == ["spec"]


def test_materialize_on_a_template_without_those_gates_is_a_noop():
    template = Template(
        id="quick",
        nodes=[{"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None}],
    )
    out = materialize(template, satisfied_gates=frozenset({"plan_approval"}))
    assert [n["id"] for n in out["nodes"]] == ["implementation"]


def test_attachment_gates_are_real_gate_names():
    assert set(ATTACHMENT_GATES.values()) <= GATE_NAMES
```

Import what the tests use at the top of the file: `from kraft.templates import ATTACHMENT_GATES, GATE_NAMES, Template, materialize` (merge into the existing import line).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test -k materialize or attachment_gates`
Expected: FAIL — `materialize() got an unexpected keyword argument 'satisfied_gates'`; `ImportError` for `ATTACHMENT_GATES`.

- [ ] **Step 3: Implement**

In `src/kraft/templates.py`, under the existing `GATE_NAMES` definition:

```python
#: What an intake attachment stands in for (Kraft-dgh). Keyed on the gate rather
#: than the node id: gate names are a validated closed vocabulary, node ids are
#: free text a custom template chooses.
ATTACHMENT_GATES = {"spec": "spec_approval", "plan": "plan_approval"}
```

Replace `materialize`:

```python
def materialize(template: Template, *, satisfied_gates: frozenset[str] = frozenset()) -> dict:
    """The template's nodes, minus any whose gate an intake attachment already
    satisfies — the item genuinely has no spec node, rather than one skipped at
    runtime (Kraft-dgh)."""
    return {
        "template_id": template.id,
        "nodes": [
            {
                "id": n["id"],
                "tasks": list(n["tasks"]),
                "gate_after": n.get("gate_after"),
                "fix_loop": n.get("fix_loop"),
            }
            for n in template.nodes
            if n.get("gate_after") not in satisfied_gates
        ],
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test -k "materialize or attachment_gates"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/kraft/templates.py tests/test_templates.py
git commit -m "feat(templates): materialize can drop gate-satisfied nodes"
```

---

### Task 3: Executor — intake trims, dispatch instructs

**Files:**
- Modify: `src/kraft/executor.py:49-73` (`intake`), `src/kraft/executor.py:76-115` (`_dispatch`)
- Test: `tests/test_executor.py`

**Interfaces:**
- Consumes: `templates.ATTACHMENT_GATES`, `templates.materialize(..., satisfied_gates=...)` (Task 2); `store.create_work_item(..., attachments=...)` (Task 1).
- Produces: `executor.intake(..., attachments: list[dict] | None = None) -> str`; `executor._attachments(row) -> list[dict]`; `executor._attachment_note(attachments) -> str`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_executor.py`:

```python
def test_attachment_note_lists_kinds_and_paths():
    note = executor._attachment_note(
        [
            {"kind": "spec", "path": ".engineering/specs/a.md"},
            {"kind": "plan", "path": ".engineering/plans/a.md"},
        ]
    )
    assert "Spec: .engineering/specs/a.md" in note
    assert "Plan: .engineering/plans/a.md" in note
    assert "do not re-plan" in note.lower()


def test_attachment_note_is_empty_without_attachments():
    assert executor._attachment_note([]) == ""


def test_attachments_reads_a_row_without_the_column():
    # Rows built by older fixtures have no 'attachments' key; that must not raise.
    class Row(dict):
        def keys(self):
            return super().keys()

    assert executor._attachments(Row(title="t")) == []
    assert executor._attachments(Row(attachments=None)) == []
    assert executor._attachments(
        Row(attachments='[{"kind": "plan", "path": "p.md"}]')
    ) == [{"kind": "plan", "path": "p.md"}]
```

And an integration test that intake trims the chain (uses the existing fixtures in this file — follow the file's established `asyncio.run(scenario())` shape):

```python
def test_intake_with_a_plan_attachment_drops_the_plan_node(tmp_path):
    template = Template(
        id="default",
        nodes=[
            {"id": "spec", "tasks": ["on.spec.requested"], "gate_after": "spec_approval"},
            {"id": "plan", "tasks": ["on.plan.requested"], "gate_after": "plan_approval"},
            {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
        ],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(tmp_path),
                template=template,
                bd_cwd=str(isolated_bd(tmp_path)),
                attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT chain_definition, attachments FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            chain = json.loads(row["chain_definition"])
            assert [n["id"] for n in chain["nodes"]] == ["spec", "implementation"]
            assert json.loads(row["attachments"])[0]["kind"] == "plan"
        finally:
            await database.close()

    asyncio.run(scenario())
```

Add any missing imports the test needs (`asyncio`, `json`, `from kraft.paths import RunDirs`, `from kraft.templates import Template`, `from support.harness import isolated_bd`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test -k "attachment_note or attachments_reads or plan_attachment_drops"`
Expected: FAIL — `module 'kraft.executor' has no attribute '_attachment_note'`.

- [ ] **Step 3: Implement intake**

In `src/kraft/executor.py`, add the import of the new constant:

```python
from kraft.templates import ATTACHMENT_GATES, Registry, Template, materialize
```

Extend `intake`:

```python
async def intake(
    db,
    run_dirs,
    *,
    title: str,
    repo: str,
    template: Template,
    bd_cwd: str | None = None,
    submodules: list[str] | None = None,
    root_merge_policy: str = "bump",
    attachments: list[dict] | None = None,
) -> str:
    work_item_id = uuid.uuid4().hex
    bead_id = await beads.intake(title, cwd=bd_cwd)
    satisfied = frozenset(ATTACHMENT_GATES[a["kind"]] for a in attachments or [])
    chain_definition = json.dumps(materialize(template, satisfied_gates=satisfied))
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
            attachments=attachments,
        )
    )
    return work_item_id
```

- [ ] **Step 4: Implement the instruction block**

Near `_STEER_PROMPT` in `src/kraft/executor.py`:

```python
# What an agent is told about documents attached at intake. It follows the title
# because the title is the task and these are how it was already decided.
_ATTACHMENT_PROMPT = (
    "\n\n{lines}\nFollow the documents above; they are the agreed spec and plan "
    "for this work item. Do not re-plan."
)


def _attachments(work_item_row) -> list[dict]:
    """The row's intake attachments, tolerating a row that predates the column."""
    if "attachments" not in work_item_row.keys():
        return []
    raw = work_item_row["attachments"]
    return json.loads(raw) if raw else []


def _attachment_note(attachments: list[dict]) -> str:
    if not attachments:
        return ""
    lines = "\n".join(f"{a['kind'].capitalize()}: {a['path']}" for a in attachments)
    return _ATTACHMENT_PROMPT.format(lines=lines)
```

In `_dispatch`, replace the instruction line:

```python
        instruction = instruction_override or (
            work_item_row["title"] + _attachment_note(_attachments(work_item_row))
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `just test -k "attachment_note or attachments_reads or plan_attachment_drops"`
Expected: PASS

- [ ] **Step 6: Run the full backend suite**

Run: `just test`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/kraft/executor.py tests/test_executor.py
git commit -m "feat(executor): trim gate-satisfied nodes and instruct agents with attachments"
```

---

### Task 4: Copy attachments into the worktree

**Files:**
- Modify: `src/kraft/builtins.py:7-25` (`env_setup`)
- Modify: `src/kraft/executor.py:97-99` (the `env_setup` dispatch branch)
- Test: `tests/test_builtins.py`

**Interfaces:**
- Consumes: `executor._attachments` (Task 3).
- Produces: `builtins.env_setup(..., attachments: list[dict] | None = None)`.

- [ ] **Step 1: Write the failing test**

In `tests/test_builtins.py`:

```python
def test_env_setup_copies_an_uncommitted_attachment_into_the_worktree(tmp_path):
    repo = make_repo(tmp_path)
    plan = repo / ".engineering" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# the plan\n")  # never committed

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B",
                    title="t",
                    repo=str(repo),
                    chain_template="quick-task",
                    chain_definition="{}",
                )
            )
            status = await kraft_builtins.env_setup(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="env_setup",
                repo=str(repo),
                attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
            )
            assert status == "done"
            copied = rd.worktrees / "w1" / ".engineering" / "plans" / "p.md"
            assert copied.read_text() == "# the plan\n"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_env_setup_leaves_a_committed_attachment_alone(tmp_path):
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# committed\n"})
    (repo / ".engineering" / "plans" / "p.md").write_text("# dirty working tree\n")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await kraft_builtins.env_setup(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="env_setup",
                repo=str(repo),
                attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
            )
            copied = rd.worktrees / "w1" / ".engineering" / "plans" / "p.md"
            # git brought the committed version; the copy must not clobber it
            assert copied.read_text() == "# committed\n"
        finally:
            await database.close()

    asyncio.run(scenario())
```

The second test needs a work item row only if the subprocess adapter requires one — mirror whatever the existing `test_env_setup_creates_worktree_and_branch` does, and add `make_repo_with_engineering` to the `support.harness` import line.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test -k env_setup`
Expected: FAIL — `env_setup() got an unexpected keyword argument 'attachments'`.

- [ ] **Step 3: Implement**

In `src/kraft/builtins.py`, add `import shutil` and `from pathlib import Path` at the top, then:

```python
def _copy_attachments(repo: Path, worktree: Path, attachments: list[dict]) -> None:
    """Intake attachments that are not committed do not exist in a fresh
    worktree (`git worktree add` branches from HEAD), so copy them in. A
    committed one arrived through git and is left exactly as git wrote it."""
    for attachment in attachments:
        dest = worktree / attachment["path"]
        src = repo / attachment["path"]
        if dest.exists() or not src.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)


async def env_setup(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    repo: str,
    round: int = 0,
    attachments: list[dict] | None = None,
) -> str:
    worktree = run_dirs.worktrees / work_item_id
    branch = f"kraft/{work_item_id}"
    if worktree.is_dir():
        # idempotent: a prior (crashed) run already created the worktree, and
        # with it any attachment copies.
        return "done"
    status = await _subprocess.run_task(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point="on.env.prepare",
        cmd=["git", "worktree", "add", str(worktree), "-b", branch],
        cwd=repo,
        round=round,
    )
    if status == "done":
        _copy_attachments(Path(repo), worktree, attachments or [])
    return status
```

In `src/kraft/executor.py`, pass them through:

```python
    if kind == "builtin" and binding.get("handler") == "env_setup":
        return await _builtins.env_setup(
            db,
            run_dirs,
            repo=work_item_row["repo"],
            attachments=_attachments(work_item_row),
            **common,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test -k env_setup`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/kraft/builtins.py src/kraft/executor.py tests/test_builtins.py
git commit -m "feat(env): copy intake attachments into the worktree"
```

---

### Task 5: API — validation and pass-through

**Files:**
- Modify: `src/kraft/api.py:256-263` (`NewWorkItem`), `src/kraft/api.py:266-300` (`create_work_item`), `src/kraft/api.py:390-406` (list item dict), `src/kraft/api.py:423-436` (`get_work_item`)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `executor.intake(..., attachments=...)` (Task 3).
- Produces: request field `attachments: [{kind, path}]`; `_validated_attachments(repo: str, attachments: list[Attachment]) -> list[dict]`; response field `attachments: list[dict]` (parsed, `[]` when none) on both the list and detail endpoints.

- [ ] **Step 1: Write the failing tests**

In `tests/test_api.py`:

```python
def test_intake_with_a_plan_attachment_trims_the_chain_and_reports_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# plan\n"})
    r = client.post(
        "/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
        },
    )
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    item = client.get(f"/work-items/{wid}").json()
    assert item["attachments"] == [{"kind": "plan", "path": ".engineering/plans/p.md"}]
    assert "plan_approval" not in [n["gate_after"] for n in item["chain_definition"]["nodes"]]
    listed = next(i for i in client.get("/work-items").json()["items"] if i["id"] == wid)
    assert listed["attachments"] == [{"kind": "plan", "path": ".engineering/plans/p.md"}]


def test_intake_rejects_a_traversing_attachment_path(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    repo = make_repo(tmp_path)
    r = client.post(
        "/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "attachments": [{"kind": "plan", "path": "../outside.md"}],
        },
    )
    assert r.status_code == 422
    assert "escapes" in r.text


def test_intake_rejects_a_missing_attachment_and_a_duplicate_kind(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    repo = make_repo(tmp_path)
    missing = client.post(
        "/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "attachments": [{"kind": "plan", "path": ".engineering/plans/nope.md"}],
        },
    )
    assert missing.status_code == 422
    (repo / ".engineering" / "plans").mkdir(parents=True)
    (repo / ".engineering" / "plans" / "p.md").write_text("# p\n")
    dupe = client.post(
        "/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "attachments": [
                {"kind": "plan", "path": ".engineering/plans/p.md"},
                {"kind": "plan", "path": ".engineering/plans/p.md"},
            ],
        },
    )
    assert dupe.status_code == 422


def test_intake_accepts_an_uncommitted_attachment(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    repo = make_repo(tmp_path)
    plan = repo / ".engineering" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# uncommitted\n")
    r = client.post(
        "/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
        },
    )
    assert r.status_code == 201, r.text
```

Add `make_repo_with_engineering` to the `support.harness` import line at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test -k "attachment"`
Expected: FAIL — the attachments field is ignored, so the chain still has `plan_approval` and the response has no `attachments` key.

- [ ] **Step 3: Implement the model and validation**

In `src/kraft/api.py`, add `Literal` to the `typing` import, then:

```python
class Attachment(BaseModel):
    kind: Literal["spec", "plan"]
    #: repo-relative; validated and normalized server-side before it is stored
    path: str


class NewWorkItem(BaseModel):
    title: str
    repo: str
    chain_template: str = "quick-task"
    submodules: list[str] = []
    root_merge_policy: str = "bump"
    #: spec/plan documents that already exist — they trim the gates they satisfy
    attachments: list[Attachment] = []


def _validated_attachments(repo: str, attachments: list[Attachment]) -> list[dict]:
    """Trust boundary: `path` comes from a browser and is used to read a file and
    to write into a worktree. Resolve under the repo and reject any escape."""
    kinds = [a.kind for a in attachments]
    if len(set(kinds)) != len(kinds):
        raise HTTPException(422, "at most one attachment per kind")
    root = Path(repo).resolve()
    out = []
    for a in attachments:
        target = (root / a.path).resolve()
        if not target.is_relative_to(root):
            raise HTTPException(422, f"attachment path escapes the repo: {a.path}")
        # Working tree, not HEAD: a document written minutes ago is legal input,
        # and env_setup copies it into the worktree.
        if not target.is_file():
            raise HTTPException(422, f"attachment not found: {a.path}")
        out.append({"kind": a.kind, "path": str(target.relative_to(root))})
    return out
```

In `create_work_item`, after the existing repo-directory check:

```python
    attachments = _validated_attachments(body.repo, body.attachments)
```

and pass `attachments=attachments` to `executor.intake(...)`.

- [ ] **Step 4: Report attachments on both read paths**

In `list_work_items`, add to the item dict:

```python
            "attachments": json.loads(r["attachments"]) if r["attachments"] else [],
```

In `get_work_item`, beside the existing `chain_definition` override:

```python
        "attachments": json.loads(row["attachments"]) if row["attachments"] else [],
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `just test -k attachment`
Expected: PASS

- [ ] **Step 6: Run the full backend suite and lint**

Run: `just test && just lint`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/kraft/api.py tests/test_api.py
git commit -m "feat(api): accept and report intake attachments"
```

---

### Task 6: Attachments in the Documents tab

**Files:**
- Modify: `src/kraft/index/service.py:263-279` (`documents_for_work_item`)
- Test: `tests/test_index_service.py`

**Interfaces:**
- Consumes: `work_items.attachments` (Task 1).
- Produces: `documents_for_work_item` rows may carry `attachment_kind: "spec" | "plan"`; link-sourced rows carry `attachment_kind: None`.

- [ ] **Step 1: Write the failing test**

In `tests/test_index_service.py`:

```python
def test_documents_for_work_item_includes_intake_attachments(tmp_path):
    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            repo = make_repo_with_engineering(
                tmp_path, {".engineering/plans/p.md": "# Plan\nbody\n"}
            )
            await state.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at, attachments) VALUES "
                    "(?, 'b1', 't', ?, 'default', '{}', 'active', 'now', 'now', ?)",
                    ("w1", str(repo), '[{"kind": "plan", "path": ".engineering/plans/p.md"}]'),
                )
            )
            ix = Indexer(conn, state, repos_env=str(repo))
            await ix.rescan_repo(str(repo))
            docs = ix.documents_for_work_item("w1")
            assert [d["path"] for d in docs] == [".engineering/plans/p.md"]
            assert docs[0]["attachment_kind"] == "plan"
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())


def test_documents_for_work_item_survives_a_rescan(tmp_path):
    """Attachments are joined at read time, so re-ingesting the document — which
    rewrites its document_links wholesale — cannot drop them."""

    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            repo = make_repo_with_engineering(
                tmp_path, {".engineering/plans/p.md": "# Plan\nbody\n"}
            )
            await state.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at, attachments) VALUES "
                    "(?, 'b1', 't', ?, 'default', '{}', 'active', 'now', 'now', ?)",
                    ("w1", str(repo), '[{"kind": "plan", "path": ".engineering/plans/p.md"}]'),
                )
            )
            ix = Indexer(conn, state, repos_env=str(repo))
            await ix.rescan_repo(str(repo))
            await ix.rescan_repo(str(repo))
            assert len(ix.documents_for_work_item("w1")) == 1
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test -k documents_for_work_item`
Expected: FAIL — the attachment is not linked, so the list is empty.

- [ ] **Step 3: Implement**

In `src/kraft/index/service.py`, replace the tail of `documents_for_work_item` so link rows and attachments merge:

```python
    def documents_for_work_item(self, work_item_id: str) -> list[dict]:
        """04 §9: what is linked to this work item. No content — the UI fetches
        that per document via GET /documents/{id}."""
        # ... existing query unchanged ...
        docs = [{**{k: r[k] for k in r.keys()}, "attachment_kind": None} for r in rows]
        seen = {d["document_id"] for d in docs}
        return docs + [d for d in self._attachment_docs(work_item_id) if d["document_id"] not in seen]

    def _attachment_docs(self, work_item_id: str) -> list[dict]:
        """Intake attachments (Kraft-dgh), joined at read time rather than stored
        as document_links: `upsert_document` rewrites a document's links whole on
        every rescan, so a stored row would not survive one."""
        row = self._state.read(
            lambda c: c.execute(
                "SELECT repo, attachments FROM work_items WHERE id = ?", (work_item_id,)
            ).fetchone()
        )
        if row is None or not row["attachments"]:
            return []
        out = []
        for attachment in json.loads(row["attachments"]):
            doc = self._conn.execute(
                "SELECT id AS document_id, repo, title, kind, source_kind, path "
                "FROM documents WHERE repo = ? AND path = ?",
                (row["repo"], attachment["path"]),
            ).fetchone()
            if doc is None:
                # Not indexed yet: scan_repo lists via `git ls-files`, so an
                # uncommitted attachment appears here only once it is committed
                # and rescanned. The timeline event names it meanwhile.
                continue
            out.append(
                {
                    **{k: doc[k] for k in doc.keys()},
                    "node_id": None,
                    "hook_point": None,
                    "worker_session_id": None,
                    "attachment_kind": attachment["kind"],
                }
            )
        return out
```

Add `import json` at the top of `service.py` if it is not already imported.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test -k documents_for_work_item`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `just test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/kraft/index/service.py tests/test_index_service.py
git commit -m "feat(index): surface intake attachments in a work item's documents"
```

---

### Task 7: Intake UI — pick, preview, badge

**Files:**
- Modify: `frontend/src/types.ts:15-37` (`WorkItem`), `frontend/src/api.ts:63-70` (`createWorkItem`)
- Modify: `frontend/src/components/IntakeModal.tsx`
- Modify: `frontend/src/views/Board.tsx` (the work-item card), `frontend/src/views/WorkItemDetail.tsx` (header)
- Test: `frontend/src/components/IntakeModal.test.tsx`, `frontend/src/views/Board.test.tsx`

**Interfaces:**
- Consumes: `POST /work-items` `attachments` field and the `attachments` response field (Task 5); `GET /search` with `repo`, `source_kind=artifact`, `kind=specs|plans`.
- Produces: `WorkItemAttachment { kind: "spec" | "plan"; path: string }` in `types.ts`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/IntakeModal.test.tsx`, following the file's existing mock-and-render shape:

```tsx
it("searches the chosen repo's artifacts and submits the picked plan", async () => {
  vi.spyOn(api, "search").mockResolvedValue({
    query: "auth",
    mode: "hybrid",
    results: [
      {
        id: "d1",
        repo: "/repo",
        kind: "plans",
        source_kind: "artifact",
        title: "Auth plan",
        path: ".engineering/plans/auth.md",
        snippet: "",
        score: 1,
        links: [],
      },
    ],
  } as never);
  const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w1" } as never);
  render(<IntakeModal onClose={() => {}} />, { wrapper: Wrapper });

  await userEvent.type(screen.getByLabelText("repo"), "/repo");
  await userEvent.type(screen.getByLabelText("title"), "t");
  await userEvent.type(screen.getByLabelText("existing plan"), "auth");
  await userEvent.click(await screen.findByText("Auth plan"));
  await userEvent.click(screen.getByRole("button", { name: "Create" }));

  expect(api.search).toHaveBeenCalledWith(
    expect.objectContaining({ repo: "/repo", kind: "plans", source_kind: "artifact" }),
  );
  expect(create).toHaveBeenCalledWith(
    expect.objectContaining({
      attachments: [{ kind: "plan", path: ".engineering/plans/auth.md" }],
    }),
  );
});

it("accepts a path typed by hand for a document that is not indexed", async () => {
  const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w1" } as never);
  render(<IntakeModal onClose={() => {}} />, { wrapper: Wrapper });

  await userEvent.type(screen.getByLabelText("repo"), "/repo");
  await userEvent.type(screen.getByLabelText("title"), "t");
  await userEvent.type(screen.getByLabelText("spec path"), ".engineering/specs/x.md");
  await userEvent.click(screen.getByRole("button", { name: "Create" }));

  expect(create).toHaveBeenCalledWith(
    expect.objectContaining({
      attachments: [{ kind: "spec", path: ".engineering/specs/x.md" }],
    }),
  );
});
```

In `frontend/src/views/Board.test.tsx`, a badge test in the file's existing style:

```tsx
it("marks an item that started from existing documents", () => {
  renderBoard([
    makeItem({
      id: "w1",
      attachments: [{ kind: "plan", path: ".engineering/plans/p.md" }],
    }),
  ]);
  expect(screen.getByText("from plan")).toBeInTheDocument();
});
```

Use whatever item factory and render helper `Board.test.tsx` already defines rather than inventing new ones.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test-ui`
Expected: FAIL — no `existing plan` / `spec path` fields, no badge.

- [ ] **Step 3: Type and API surface**

In `frontend/src/types.ts`:

```ts
export interface WorkItemAttachment {
  kind: "spec" | "plan";
  /** Repo-relative path, normalized by the server. */
  path: string;
}
```

and on `WorkItem`:

```ts
  /** Documents attached at intake; the gates they satisfy are absent from the chain. */
  attachments?: WorkItemAttachment[];
```

In `frontend/src/api.ts`, extend the `createWorkItem` body type with:

```ts
  attachments?: { kind: "spec" | "plan"; path: string }[];
```

- [ ] **Step 4: Intake fields and chain preview**

In `IntakeModal.tsx`, add state and a search effect per kind, and submit them. The picker is type-to-search because `GET /search` requires a non-empty `q`:

```tsx
const KINDS = [
  { kind: "spec" as const, docKind: "specs", gate: "spec_approval", label: "spec" },
  { kind: "plan" as const, docKind: "plans", gate: "plan_approval", label: "plan" },
];

const [attachPath, setAttachPath] = useState<Record<string, string>>({}); // kind -> path
const [query, setQuery] = useState<Record<string, string>>({});         // kind -> typed text
const [hits, setHits] = useState<Record<string, SearchResult[]>>({});
```

One debounced effect, mirroring the existing submodule probe:

```tsx
useEffect(() => {
  const t = setTimeout(() => {
    KINDS.forEach(({ kind, docKind }) => {
      const q = (query[kind] ?? "").trim();
      if (!repo.trim() || !q) return setHits((h) => ({ ...h, [kind]: [] }));
      api
        .search({ q, repo, kind: docKind, source_kind: "artifact", limit: 5 })
        .then((r) => setHits((h) => ({ ...h, [kind]: r.results })))
        .catch(() => setHits((h) => ({ ...h, [kind]: [] })));
    });
  }, 300);
  return () => clearTimeout(t);
}, [repo, query]);
```

The attachments sent on submit:

```tsx
const attachments = KINDS.filter(({ kind }) => attachPath[kind]?.trim()).map(({ kind }) => ({
  kind,
  path: attachPath[kind].trim(),
}));
```

added to the `createWorkItem` call as `...(attachments.length ? { attachments } : {})`.

Markup, after the Title field — a typeahead and a free path input per kind, where picking a hit fills the same `attachPath[kind]` the path input writes:

```tsx
<div className="field">
  <label>
    Start from existing <span className="field-hint">· skips the phases these cover</span>
  </label>
  {KINDS.map(({ kind, label }) => (
    <div key={kind} className="attachment-row">
      <input
        className="input"
        aria-label={`existing ${label}`}
        placeholder={`search ${label}s in this repo`}
        value={query[kind] ?? ""}
        onChange={(e) => setQuery((q) => ({ ...q, [kind]: e.target.value }))}
      />
      <input
        className="input"
        aria-label={`${label} path`}
        placeholder="or a repo-relative path"
        value={attachPath[kind] ?? ""}
        onChange={(e) => setAttachPath((p) => ({ ...p, [kind]: e.target.value }))}
      />
      {(hits[kind] ?? []).map((h) => (
        <button
          type="button"
          key={h.id}
          className="attachment-hit"
          onClick={() => {
            setAttachPath((p) => ({ ...p, [kind]: h.path }));
            setQuery((q) => ({ ...q, [kind]: "" }));
          }}
        >
          {h.title} <span className="field-hint">{h.path}</span>
        </button>
      ))}
    </div>
  ))}
</div>
```

And the preview line under the template control, so the skip is visible before Create:

```tsx
{attachments.length > 0 && (
  <p className="field-hint chain-preview">
    skips: {KINDS.filter(({ kind }) => attachPath[kind]?.trim()).map(({ label }) => label).join(" + ")}
  </p>
)}
```

Style `.attachment-row`, `.attachment-hit` and `.chain-preview` in `frontend/src/styles.css` next to the existing `.submodules` rules, matching their spacing and colors.

- [ ] **Step 5: Badge**

In `Board.tsx`'s card, beside the existing status/gate markers:

```tsx
{item.attachments?.length ? (
  <span className="tag tag-outline">
    from {item.attachments.map((a) => a.kind).join("+")}
  </span>
) : null}
```

The same expression goes in `WorkItemDetail.tsx`'s header next to the title.

- [ ] **Step 6: Run the frontend tests**

Run: `just test-ui`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): start a work item from an existing spec or plan"
```

---

### Task 8: End-to-end check and documentation

**Files:**
- Test: `tests/test_e2e.py` (follow the file's existing marker and skip conventions)

**Interfaces:**
- Consumes: everything above.
- Produces: no new interfaces.

- [ ] **Step 1: Write the end-to-end test**

In `tests/test_e2e.py`, in the style of the file's existing cases:

```python
def test_item_with_a_plan_attachment_never_runs_the_plan_node(tmp_path, monkeypatch):
    """The trimmed node must be absent from the run, not merely skipped in the UI."""
    client = _client(tmp_path, monkeypatch)
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# plan\n"})
    wid = client.post(
        "/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
        },
    ).json()["id"]
    _await_gate(client, wid, "spec_approval")
    client.post(f"/work-items/{wid}/gates/spec_approval/approve")
    events = _poll_events(client, wid, "node_entered", count=2)
    entered = [e["payload"]["node_id"] for e in events if e["type"] == "node_entered"]
    assert "plan" not in entered
```

- [ ] **Step 2: Run it**

Run: `just test -k plan_attachment_never_runs`
Expected: PASS

- [ ] **Step 3: Manual check against a dev instance**

```bash
just dev-reset && just dev
```

In the UI: create an item against a repo that has `.engineering/plans/*.md`, type into "existing plan", pick a hit, create. Confirm the detail view's chain has no plan node, the timeline shows `work_item_attachments`, the card shows `from plan`, and the Documents tab lists the attached plan.

- [ ] **Step 4: Full gates**

Run: `just test && just test-ui && just lint`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test(e2e): a plan attachment removes the plan node from the run"
```
