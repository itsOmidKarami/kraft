# Work Item Description Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a work item a `description` — the prose brief the spec node writes a design from — and thread it through every door that creates, reads, or edits a work item.

**Architecture:** One nullable `description` column on `work_items`, following the established pattern used by `submodules`, `attachments`, `base_ref` and `bead_cwd`. The payoff is a single line in `executor.py` where a work item becomes an agent's task instruction: the description is prepended to every agent node's prompt, ahead of the attachment note. Everything else is plumbing outward to the API, CLI, MCP tool, UI and skills.

**Tech Stack:** Python 3 · SQLite (raw `sqlite3`, hand-rolled numbered migrations) · FastAPI + Pydantic · pytest · React + TypeScript + Zustand · Vitest.

**Spec:** `docs/superpowers/specs/2026-09-08-work-item-description-design.md`

**Issue:** Kraft-e821

## Global Constraints

- **Optional everywhere.** `description` is nullable in the DB and defaults to absent in every API, CLI, MCP and UI door. A work item created without one must behave byte-for-byte as it does today.
- **Both schema paths.** `db.SCHEMA_SQL` (fresh installs) and `db._MIGRATIONS` (upgrades) must agree. A column added to only one makes a fresh DB and a migrated DB diverge.
- **Migration numbering:** the new step is key `12`, and `db.SCHEMA_VERSION` becomes `13`.
- **Never shell-interpolate the description.** `bd` is invoked with an argument list through `subprocess.run` with no shell. Keep it that way; the description is arbitrary user prose.
- **Test commands:** `just test <path>` for backend (full suite takes ~14 min — always name the file), `just test-ui` for frontend, `just lint` before the final commit.
- **Existing tests must not be edited to pass.** If an existing assertion breaks, that is a signal the change altered behavior it should not have.

---

### Task 1: Schema and store

The column itself, on both schema paths, plus the write door in `store`.

**Files:**
- Modify: `src/kraft/db.py` (`SCHEMA_VERSION:16`, `SCHEMA_SQL` work_items block ~`:19-31`, `_MIGRATIONS` ~`:169`)
- Modify: `src/kraft/store.py:29-84` (`create_work_item`)
- Test: `tests/test_db.py` (also modify the `_build_old_db` helper at `:107`)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `work_items.description` (TEXT, nullable). `store.create_work_item(conn, *, id, bead_id, title, repo, chain_template, chain_definition, description: str | None = None, submodules=None, root_merge_policy=None, attachments=None, status="active", bead_cwd=None) -> None`.

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_db.py`:

```python
def test_migrate_v12_to_v13_adds_description(tmp_path):
    """The description migration adds the column via ALTER TABLE, and pre-existing
    rows survive with their other values intact and a NULL description."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 12, drop_lines=("description",))
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    conn.commit()
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(work_items)")}
    assert "description" in cols
    row = conn2.execute("SELECT title, description FROM work_items WHERE id='w1'").fetchone()
    assert row["title"] == "t"
    assert row["description"] is None


def test_fresh_schema_has_description(tmp_path):
    """SCHEMA_SQL and the migration path must agree — a fresh install and an
    upgraded one are the same database."""
    conn = db._connect(tmp_path / "fresh.db")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert "description" in cols
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_db.py -k "description" -v`
Expected: FAIL. `test_fresh_schema_has_description` fails on the missing column; `test_migrate_v12_to_v13_adds_description` fails with a `KeyError: 12` from `_MIGRATIONS` or on the missing column.

- [ ] **Step 3: Add the column to both schema paths**

In `src/kraft/db.py`, bump the version:

```python
SCHEMA_VERSION = 13
```

In `SCHEMA_SQL`, add the column to `work_items` immediately after `title`:

```sql
CREATE TABLE work_items (
  id               TEXT PRIMARY KEY,
  bead_id          TEXT,
  title            TEXT NOT NULL,
  -- the brief this work item's agents are given, ahead of any attachment note
  description      TEXT,
  repo             TEXT NOT NULL,
```

Add the migration step to `_MIGRATIONS`, beside the other single-column adds:

```python
    12: ["ALTER TABLE work_items ADD COLUMN description TEXT"],
```

- [ ] **Step 4: Teach the test helper that old schemas had no description**

`_build_old_db` builds a pre-current schema by dropping lines from `SCHEMA_SQL`. It needs to know this column arrived at v13. In `tests/test_db.py:107`, alongside the existing `if version < 11:` clause:

```python
    if version < 13:
        drop_lines = (*drop_lines, "description", "-- the brief this work item's")
```

The comment line is dropped too — it contains no other column name, and leaving it would be harmless but confusing in a v12 schema.

- [ ] **Step 5: Run the migration tests to verify they pass**

Run: `just test tests/test_db.py -v`
Expected: PASS, all of them. Every pre-existing migration test still passes — they route through `_build_old_db`, which now drops the new column for any version below 13.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/db.py tests/test_db.py
git commit -m "feat: add work_items.description column"
```

- [ ] **Step 7: Write the failing store test**

Add to `tests/test_store.py`:

```python
def test_create_work_item_round_trips_a_description(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B-1",
                    title="short label",
                    description="the long brief the spec is written from",
                    repo="/r",
                    chain_template="quick-task",
                    chain_definition=_CHAIN,
                )
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["description"] == "the long brief the spec is written from"

            # The event payload stays a scannable label: the brief does not go in it.
            evts = database.read(lambda c: events.read_after(c, 0, "w1"))
            created = [e for e in evts if e["type"] == "work_item_created"]
            assert len(created) == 1
            assert "description" not in created[0]["payload"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_create_work_item_without_a_description_stores_null(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["description"] is None
        finally:
            await database.close()

    asyncio.run(scenario())
```

- [ ] **Step 8: Run to verify it fails**

Run: `just test tests/test_store.py -k description -v`
Expected: FAIL with `TypeError: create_work_item() got an unexpected keyword argument 'description'`.

- [ ] **Step 9: Add the parameter to `store.create_work_item`**

In `src/kraft/store.py:29`, add the keyword after `title` in the signature:

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
    description: str | None = None,
    submodules: list[str] | None = None,
```

Add it to the INSERT — the column list, one more placeholder, and the values tuple:

```python
    conn.execute(
        "INSERT INTO work_items (id, bead_id, title, description, repo, chain_template, "
        "chain_definition, current_node_id, status, created_at, updated_at, "
        "submodules, root_merge_policy, attachments, bead_cwd) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)",
        (
            id,
            bead_id,
            title,
            description or None,
            repo,
            chain_template,
            chain_definition,
            status,
            now,
            now,
            json.dumps(submodules) if submodules else None,
            root_merge_policy if submodules else None,
            json.dumps(attachments) if attachments else None,
            bead_cwd,
        ),
    )
```

`description or None` collapses `""` to `NULL`: an empty string from an API default and an absent description are the same absence, and only one of them should be storable.

Leave the `events.append(... "work_item_created" ...)` payload exactly as it is.

- [ ] **Step 10: Run to verify it passes**

Run: `just test tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/kraft/store.py tests/test_store.py
git commit -m "feat: store a description on work item creation"
```

---

### Task 2: The description reaches the agent

The point of the whole feature. `executor` composes the task instruction; the description goes between the title and the attachment note.

**Files:**
- Modify: `src/kraft/executor.py` (comment `:65-66`, `intake` `:111-148`, instruction `:287-289`)
- Test: `tests/test_executor.py`

**Interfaces:**
- Consumes: `store.create_work_item(..., description=...)` from Task 1.
- Produces: `executor.intake(db, run_dirs, *, title, repo, template, description: str | None = None, bd_cwd=None, submodules=None, root_merge_policy="bump", attachments=None, status="active", bead_id=None, bead_cwd=None) -> str`, and a module-level `executor._brief(work_item_row) -> str`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_executor.py`, modeled on the existing `test_dispatch_puts_the_attachment_note_after_the_title` at `:508`:

```python
def test_dispatch_puts_the_description_after_the_title(tmp_path, monkeypatch):
    """The description is the brief; the title is a label. Both reach the agent,
    in that order. This is the assertion the whole feature exists for."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    prompts = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts))
    title = "make the failing test pass"
    description = "test_widget_totals asserts a float; the code returns Decimal."

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title=title,
                description=description,
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())

    sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
    assert len(sent) == 1
    assert title in sent[0]
    assert description in sent[0]
    assert sent[0].index(title) < sent[0].index(description)


def test_dispatch_without_a_description_sends_the_title_alone(tmp_path, monkeypatch):
    """No description must reproduce today's instruction exactly — no stray blank
    lines, no 'None' rendered into the prompt."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    prompts = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts))
    title = "make the failing test pass"

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title=title,
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
    assert len(sent) == 1
    assert sent[0].strip() == title
```

- [ ] **Step 2: Run to verify they fail**

Run: `just test tests/test_executor.py -k description -v`
Expected: FAIL with `TypeError: intake() got an unexpected keyword argument 'description'`.

- [ ] **Step 3: Add `_brief` and thread the parameter**

In `src/kraft/executor.py`, update the comment at `:65` to name the new middle term:

```python
# What an agent is told about documents attached at intake. It follows the brief
# because the brief is the task and these are how it was already decided.
_ATTACHMENT_PROMPT = (
```

Add `_brief` next to it:

```python
def _brief(work_item_row) -> str:
    """What the work item is, as an agent is told it.

    The title is a label; the description is the actual brief, and the spec node
    is expected to write a design from it. A work item with no description is
    the title alone — exactly the string this returned before descriptions
    existed.
    """
    description = work_item_row["description"]
    if not description:
        return work_item_row["title"]
    return f"{work_item_row['title']}\n\n{description}"
```

In `intake` (`:111`), add the parameter and pass it on to both the store and the bead:

```python
async def intake(
    db,
    run_dirs,
    *,
    title: str,
    repo: str,
    template: Template,
    description: str | None = None,
    bd_cwd: str | None = None,
```

```python
    bead_id = bead_id or await beads.intake(title, description=description, cwd=bd_cwd)
```

```python
        lambda c: store.create_work_item(
            c,
            id=work_item_id,
            bead_id=bead_id,
            title=title,
            description=description,
            repo=repo,
```

At `:287`, swap the title for the brief:

```python
        instruction = instruction_override or (
            _brief(work_item_row) + _attachment_note(_attachments(work_item_row))
        )
```

Leave `title=work_item_row["title"]` at `:312` and `:348` alone — those are the session's display title and the merge request's title, which stay short by design.

**Note:** `beads.intake` does not accept `description` until Task 3. Until then this call raises `TypeError`. Implement Step 3 and Task 3 Step 3 before expecting a green run; the ordering below accounts for it.

- [ ] **Step 4: Add the `description` keyword to `beads.intake` so this task can run**

This is the one-line half of Task 3, pulled forward because Task 2's tests cannot run without it. In `src/kraft/adapters/beads.py`:

```python
async def intake(title: str, *, description: str | None = None, cwd: str | None = None) -> str:
```

and in the argument list:

```python
            "-d",
            description or "Created by the Kraft orchestrator.",
```

Task 3 tests this behavior properly.

- [ ] **Step 5: Run to verify they pass**

Run: `just test tests/test_executor.py -v`
Expected: PASS, including every pre-existing executor test. `test_dispatch_puts_the_attachment_note_after_the_title` must still pass unchanged — it creates no description, so `_brief` returns the bare title.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/executor.py src/kraft/adapters/beads.py tests/test_executor.py
git commit -m "feat: put the work item description in every agent's instruction"
```

---

### Task 3: The beads adapter, both directions

Outbound: a manually created work item writes its brief into the bead it files. Inbound: an auto-intaken bead's own description stops being discarded.

**Files:**
- Modify: `src/kraft/adapters/beads.py` (`intake:8`, `ready:~85`)
- Test: `tests/test_adapters_beads.py`

**Interfaces:**
- Consumes: `beads.intake(..., description=...)` keyword added in Task 2 Step 4.
- Produces: `beads.ready()` rows gain a `"description"` key (`str | None`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_adapters_beads.py`:

```python
def test_intake_writes_the_description_onto_the_bead(tmp_path):
    repo = isolated_bd(tmp_path)

    def _description(bead_id: str) -> str:
        out = subprocess.run(
            ["bd", "show", bead_id, "--json"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout
        return json.loads(out)[0]["description"]

    async def scenario():
        bead_id = await beads.intake(
            "wire the thing", description="the brief, at length", cwd=str(repo)
        )
        assert _description(bead_id) == "the brief, at length"

        plain = await beads.intake("wire the other thing", cwd=str(repo))
        assert _description(plain) == "Created by the Kraft orchestrator."

        empty = await beads.intake("wire a third thing", description="", cwd=str(repo))
        assert _description(empty) == "Created by the Kraft orchestrator."

    asyncio.run(scenario())


def test_ready_projects_the_description(tmp_path):
    """Auto-intake reads a bead the human already wrote. Dropping its description
    is dropping the brief in the one case where nobody is present to notice."""
    repo = isolated_bd(tmp_path)

    async def scenario():
        await beads.intake("caulk the transom", description="it leaks at the seam", cwd=str(repo))
        rows = await beads.ready(cwd=str(repo))
        assert rows
        row = next(r for r in rows if r["title"] == "caulk the transom")
        assert row["description"] == "it leaks at the seam"

    asyncio.run(scenario())


def test_ready_tolerates_a_bead_with_no_description(monkeypatch):
    """`ready` is best-effort by contract; a row without the key must not raise."""

    class _Proc:
        returncode = 0
        stdout = '[{"id": "X-1", "title": "t", "priority": 1}]'

    monkeypatch.setattr(beads.subprocess, "run", lambda *a, **k: _Proc())
    rows = asyncio.run(beads.ready(cwd="/tmp"))
    assert rows == [
        {"id": "X-1", "title": "t", "priority": 1, "issue_type": None, "description": None}
    ]
```

- [ ] **Step 2: Run to verify they fail**

Run: `just test tests/test_adapters_beads.py -k "description" -v`
Expected: the two `ready` tests FAIL with `KeyError: 'description'`. The `intake` test PASSES already — Task 2 Step 4 implemented it. That is expected and fine; it is being pinned by a test here for the first time.

- [ ] **Step 3: Project the description in `ready`**

In `src/kraft/adapters/beads.py`, in the return of `ready`:

```python
    return [
        {
            "id": r.get("id"),
            "title": r.get("title"),
            "priority": r.get("priority"),
            "issue_type": r.get("issue_type"),
            # The bead's own brief. Auto-intake has no human to retype it, so a
            # description dropped here is a description lost.
            "description": r.get("description"),
        }
        for r in rows
        if isinstance(r, dict) and r.get("id") and r.get("title")
    ]
```

- [ ] **Step 4: Run to verify they pass**

Run: `just test tests/test_adapters_beads.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/adapters/beads.py tests/test_adapters_beads.py
git commit -m "feat: carry descriptions in and out of beads"
```

---

### Task 4: Auto-intake passes the description through

**Files:**
- Modify: `src/kraft/intake.py:133-145`
- Test: `tests/test_intake_poller.py`

**Interfaces:**
- Consumes: `beads.ready()` rows with `"description"` (Task 3), `executor.intake(description=...)` (Task 2).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_intake_poller.py`, following `test_starts_one_bead_when_enabled:173`:

```python
def test_auto_intake_carries_the_beads_description(tmp_path, monkeypatch):
    """The bead already carries the brief its author wrote. Auto-intake is the one
    path with no human present to notice it being dropped."""
    monkeypatch.setattr(
        intake_mod.beads,
        "ready",
        _ready([{"id": "B-1", "title": "pick me up", "priority": 3, "description": "the brief"}]),
    )

    async def body(app):
        started = await intake_mod.tick(app)
        assert len(started) == 1
        assert [r["description"] for r in _work_items(app)] == ["the brief"]

    _run(lambda: _stub(tmp_path), body)
```

Every other test in this file supplies rows with no `description` key, which is why Step 3 reads it with `.get` — those tests must keep passing untouched.

- [ ] **Step 2: Run to verify it fails**

Run: `just test tests/test_intake_poller.py -k description -v`
Expected: FAIL — `description` is `None`.

- [ ] **Step 3: Pass it through**

In `src/kraft/intake.py:133`:

```python
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=row["title"],
            description=row.get("description"),
            repo=repo["path"],
            template=template,
            bd_cwd=_bd_cwd(),
            bead_id=row["id"],
```

`executor.intake` will not call `beads.intake` here — `bead_id` is already set, so the bead is adopted, not created. The description flows bd → Kraft only, which is correct.

- [ ] **Step 4: Run to verify it passes**

Run: `just test tests/test_intake_poller.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/intake.py tests/test_intake_poller.py
git commit -m "feat: auto-intake adopts the bead's description"
```

---

### Task 5: API — accept a description at creation, return it on the board

**Files:**
- Modify: `src/kraft/api.py` (`NewWorkItem:455`, `create_work_item:490`, list payload `:696-710`)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `executor.intake(description=...)` (Task 2).
- Produces: `NewWorkItem.description: str = ""`; `GET /work-items` items carry `"description"`.

Note: the **detail** payload at `:790` already splats `{k: row[k] for k in row.keys()}`, so it returns `description` with no change once the column exists. Only the list payload enumerates its fields.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api.py`:

```python
def test_create_accepts_a_description_and_both_payloads_return_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        r = client.post(
            "/work-items",
            json={
                "title": "short label",
                "description": "the brief the spec is written from",
                "repo": str(repo),
                "autostart": False,
            },
        )
        assert r.status_code == 201
        wid = r.json()["id"]

        detail = client.get(f"/work-items/{wid}").json()
        assert detail["description"] == "the brief the spec is written from"

        listed = client.get("/work-items").json()["items"]
        assert [i["description"] for i in listed if i["id"] == wid] == [
            "the brief the spec is written from"
        ]


def test_create_without_a_description_returns_null(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
        ).json()["id"]
        assert client.get(f"/work-items/{wid}").json()["description"] is None
        listed = client.get("/work-items").json()["items"]
        assert [i["description"] for i in listed if i["id"] == wid] == [None]
```

- [ ] **Step 2: Run to verify it fails**

Run: `just test tests/test_api.py -k description -v`
Expected: FAIL with `KeyError: 'description'` on the list payload.

- [ ] **Step 3: Add the field and the payload key**

In `src/kraft/api.py`, on `NewWorkItem` (`:455`), after `title`:

```python
class NewWorkItem(BaseModel):
    title: str
    #: the brief — prose, and what the spec node writes a design from. A title is
    #: only a label.
    description: str = ""
    repo: str
```

In `create_work_item` (`:507`), pass it to intake:

```python
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=body.title,
            description=body.description,
            repo=body.repo,
```

In the list payload (`:698`), after `"title"`:

```python
            "title": r["title"],
            "description": r["description"],
```

- [ ] **Step 4: Run to verify it passes**

Run: `just test tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/api.py tests/test_api.py
git commit -m "feat: accept and return a work item description over the API"
```

---

### Task 6: API — edit the description

A deliberately narrow PATCH. It takes one named field and nothing else.

**Files:**
- Modify: `src/kraft/store.py` (new `set_description`, next to `set_steer:603`)
- Modify: `src/kraft/api.py` (new endpoint, beside the other `/work-items/{wid}` routes)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `_work_item_row(st, wid)` (`api.py:562`, 404s on unknown), `store` write pattern from `set_steer`.
- Produces: `store.set_description(conn, work_item_id: str, description: str) -> None`; `PATCH /work-items/{wid}` accepting `{"description": str}`, returning `{"id", "description"}`; event type `work_item_description_edited` with payload `{"description": str}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api.py`:

```python
def test_patch_updates_the_description_and_records_an_event(tmp_path, monkeypatch):
    """The description feeds every agent prompt, so an edit has to be answerable
    from the timeline: `events` is the authoritative log."""
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/work-items",
            json={"title": "t", "description": "first", "repo": str(repo), "autostart": False},
        ).json()["id"]
        before = client.get(f"/work-items/{wid}").json()["updated_at"]

        r = client.patch(f"/work-items/{wid}", json={"description": "second"})
        assert r.status_code == 200
        assert r.json()["description"] == "second"

        after = client.get(f"/work-items/{wid}").json()
        assert after["description"] == "second"
        assert after["updated_at"] >= before

        evs = client.get(f"/work-items/{wid}/events").json()
        edits = [e for e in evs if e["type"] == "work_item_description_edited"]
        assert [e["payload"]["description"] for e in edits] == ["second"]


def test_patch_404s_on_an_unknown_work_item(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        assert client.patch("/work-items/nope", json={"description": "x"}).status_code == 404


def test_patch_can_clear_the_description(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/work-items",
            json={"title": "t", "description": "first", "repo": str(repo), "autostart": False},
        ).json()["id"]
        assert client.patch(f"/work-items/{wid}", json={"description": ""}).status_code == 200
        assert client.get(f"/work-items/{wid}").json()["description"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `just test tests/test_api.py -k patch -v`
Expected: FAIL with 405 Method Not Allowed — the route does not exist.

- [ ] **Step 3: Add the store function**

In `src/kraft/store.py`, immediately after `set_steer` (`:603-608`):

```python
def set_description(conn: sqlite3.Connection, work_item_id: str, description: str) -> None:
    """Replace the brief. Emits its own event: the description is prepended to
    every agent instruction, so an edit changes what later nodes are told, and
    `events` is where that has to be answerable from.
    """
    conn.execute(
        "UPDATE work_items SET description = ?, updated_at = ? WHERE id = ?",
        (description or None, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "work_item_description_edited", {"description": description})
```

- [ ] **Step 4: Add the endpoint**

In `src/kraft/api.py`, beside the other single-work-item routes:

```python
class WorkItemPatch(BaseModel):
    #: The only editable field. This route is not a general work item update —
    #: a body carrying anything else is ignored, not applied.
    description: str


@app.patch("/work-items/{wid}")
async def update_work_item(wid: str, body: WorkItemPatch, request: Request):
    st = request.app.state
    _work_item_row(st, wid)  # 404s on an unknown work item
    await st.db.write(lambda c: store.set_description(c, wid, body.description))
    return {"id": wid, "description": body.description}
```

The CSRF/origin middleware (`api.py:444`) already covers every non-GET method, so PATCH is protected with no change there.

- [ ] **Step 5: Run to verify it passes**

Run: `just test tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/api.py src/kraft/store.py tests/test_api.py
git commit -m "feat: PATCH /work-items/{id} to edit the description"
```

---

### Task 7: `render.kv` survives a multi-line value

A pure-function fix. `kv` is one line per pair; the description is the first field that can legitimately contain a newline, and today the second line lands at column zero and breaks the aligned block.

**Files:**
- Modify: `src/kraft/render.py:126-129`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `render.kv` unchanged in signature; multi-line values indent to the value column.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_render.py`, beside `test_kv_aligns_labels:64`:

```python
def test_kv_indents_the_continuation_of_a_multiline_value():
    """A description is prose and may contain newlines. Without this, every line
    after the first starts at column zero and the block stops being a block."""
    out = render.kv([("id", "Kraft-a"), ("description", "line one\nline two")]).splitlines()
    assert out[1].index("line one") == out[2].index("line two")
    assert out[2].startswith(" ")
```

- [ ] **Step 2: Run to verify it fails**

Run: `just test tests/test_render.py -k multiline -v`
Expected: FAIL — `out[2]` is `"line two"`, starting at column 0, so `.index("line two") == 0` while `out[1].index("line one")` is the value column.

- [ ] **Step 3: Indent continuation lines**

In `src/kraft/render.py`:

```python
def kv(pairs: list[tuple[str, str]]) -> str:
    """A detail block: labels right-padded to a common width.

    A value spanning several lines keeps the block's shape — every line after
    the first is indented to the value column, so a multi-line description reads
    as one field rather than running back to the margin.
    """
    label_width = max((len(label) for label, _value in pairs), default=0)
    indent = " " * (label_width + 2)
    return "\n".join(
        f"{label.ljust(label_width)}  {value.replace(chr(10), chr(10) + indent)}"
        for label, value in pairs
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `just test tests/test_render.py tests/test_render_widths.py -v`
Expected: PASS, including `test_kv_aligns_labels` — a value with no newline is untouched.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/render.py tests/test_render.py
git commit -m "fix: kv keeps its shape when a value spans lines"
```

---

### Task 8: CLI and client

**Files:**
- Modify: `src/kraft/client.py:448-473` (`create_work_item`)
- Modify: `src/kraft/cli.py:322-327` (`_cmd_create`), `:586-590` (parser)
- Test: `tests/test_cli_verbs.py`, `tests/test_client_write.py`

Note: `tests/test_cli.py` covers home resolution and first-run seeding. The CLI *verb* tests live in `tests/test_cli_verbs.py` — that is the file to add to.

**Interfaces:**
- Consumes: `POST /work-items` with `description` (Task 5).
- Produces: `client.create_work_item(title, repo=None, chain_template="quick-task", description=None) -> dict`; `kraft item create TITLE [--description TEXT]`.

`kraft view show` needs no change: `_cmd_show` renders through `_render_show` (`cli.py:276`), which iterates `item.items()` generically and picks the new key up on its own. Task 7 made that output survive a multi-line value.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_client_write.py` — the `wired` fixture mounts the real ASGI app, so this asserts a genuine round trip rather than a captured body:

```python
def test_create_work_item_sends_the_description(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item(
            "short label", repo=str(repo), description="the brief"
        )
        return await client.get_work_item(created["id"])

    assert run_with_app(wired, scenario)["description"] == "the brief"


def test_create_work_item_without_a_description_stores_none(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("short label", repo=str(repo))
        return await client.get_work_item(created["id"])

    assert run_with_app(wired, scenario)["description"] is None
```

Add to `tests/test_cli_verbs.py`, following `test_create_uses_the_cwd_repo_and_lands_paused:168`:

```python
def test_create_carries_the_description(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    _connect(repo)
    monkeypatch.chdir(repo)
    cli.main(["item", "create", "short label", "--description", "the brief", "--json"])
    created = json.loads(capsys.readouterr().out)

    cli.main(["view", "show", created["id"], "--json"])
    assert json.loads(capsys.readouterr().out)["description"] == "the brief"
```

- [ ] **Step 2: Run to verify they fail**

Run: `just test tests/test_cli_verbs.py tests/test_client_write.py -k description -v`
Expected: FAIL with `TypeError: create_work_item() got an unexpected keyword argument 'description'`, and `unrecognized arguments: --description` from the CLI test.

- [ ] **Step 3: Add the parameter to the client**

In `src/kraft/client.py:448`:

```python
async def create_work_item(
    title: str,
    repo: str | None = None,
    chain_template: str = "quick-task",
    description: str | None = None,
) -> dict:
```

and in the POST body (`:467`):

```python
    status, body = await _post(
        "/work-items",
        {
            "title": title,
            "repo": repo,
            "chain_template": chain_template,
            "autostart": False,
            **({"description": description} if description else {}),
        },
    )
```

The key is omitted rather than sent as `null`: `NewWorkItem.description` is a `str` with a default, and an explicit `null` would fail validation.

- [ ] **Step 4: Add the CLI flag**

In `src/kraft/cli.py:586`:

```python
    create = subs.add_parser("create", parents=[common], help="file a work item (starts paused)")
    create.add_argument("title")
    create.add_argument(
        "--description",
        help="the brief: what the work actually is, which the spec is written from",
    )
    create.add_argument("--repo", help="default: the repo you are standing in")
```

and in `_cmd_create` (`:322`):

```python
def _cmd_create(ns: argparse.Namespace) -> None:
    emit(
        asyncio.run(
            client.create_work_item(ns.title, _repo_scope(ns), ns.chain, ns.description)
        ),
        _render_action,
        ns.json,
    )
```

- [ ] **Step 5: Run to verify they pass**

Run: `just test tests/test_cli_verbs.py tests/test_client_write.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/cli.py src/kraft/client.py tests/test_cli_verbs.py tests/test_client_write.py
git commit -m "feat: kraft item create --description"
```

---

### Task 9: MCP tool

**Files:**
- Modify: `src/kraft/mcp.py:50-57`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Consumes: `client.create_work_item(..., description=...)` (Task 8).
- Produces: MCP tool `create_work_item(title, repo=None, chain_template="quick-task", description=None)`.

- [ ] **Step 1: Write the failing test**

`tests/test_mcp.py` inspects the registered tool schema rather than invoking tools. Add, beside `test_create_work_item_tells_the_agent_it_will_not_run:44`:

```python
def test_create_work_item_offers_a_description_and_says_what_it_is_for():
    """An agent that cannot see the parameter keeps packing intent into the title,
    which is the behavior this field exists to end."""
    create = next(t for t in _tools() if t.name == "create_work_item")
    assert "description" in create.inputSchema["properties"]
    assert "brief" in create.description.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `just test tests/test_mcp.py -k description -v`
Expected: FAIL — `description` is not in the tool's `inputSchema["properties"]`.

- [ ] **Step 3: Add the parameter**

In `src/kraft/mcp.py:50`:

```python
    async def create_work_item(
        title: str,
        repo: str | None = None,
        chain_template: str = "quick-task",
        description: str | None = None,
    ) -> dict:
        """File a new Kraft work item. It is created **paused** and does not run:
        a human starts it from the board. Use this to hand finished work off to
        Kraft rather than doing it in this session. `repo` defaults to the repo
        of the work item this session is standing in.

        `description` is the brief — what the work actually is, in prose. The
        title is only a label; the spec node writes its design from the
        description, so put the intent there rather than packing it into the
        title."""
        return await client.create_work_item(title, repo, chain_template, description)
```

- [ ] **Step 4: Run to verify it passes**

Run: `just test tests/test_mcp.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/mcp.py tests/test_mcp.py
git commit -m "feat: description on the create_work_item MCP tool"
```

---

### Task 10: UI — types, API client, and the intake form

**Files:**
- Modify: `frontend/src/types.ts:27-48` (`WorkItem`)
- Modify: `frontend/src/api.ts:68-75`
- Modify: `frontend/src/components/IntakeModal.tsx` (state `:43`, submit `:131`, JSX after the Title field `:175-186`)
- Test: `frontend/src/components/IntakeModal.test.tsx`

**Interfaces:**
- Consumes: `POST /work-items` with `description` (Task 5), `PATCH /work-items/{id}` (Task 6).
- Produces: `WorkItem.description?: string | null`; `api.updateWorkItem(id: string, description: string): Promise<{ id: string; description: string }>`.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/components/IntakeModal.test.tsx`, following the existing "closes and navigates on success" test:

```tsx
it("submits the description with the new work item", async () => {
  vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task", nodes: [], gates: 0 }]);
  const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w9" });
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/" element={<IntakeModal onClose={() => {}} />} />
        <Route path="/work-items/:id" element={<p>detail for w9</p>} />
      </Routes>
    </MemoryRouter>,
  );
  await userEvent.type(screen.getByLabelText("repo"), "/r");
  await userEvent.type(screen.getByLabelText("title"), "short label");
  await userEvent.type(screen.getByLabelText("description"), "the brief");
  await userEvent.click(screen.getByRole("button", { name: /create/i }));

  await waitFor(() =>
    expect(create).toHaveBeenCalledWith({
      repo: "/r",
      title: "short label",
      description: "the brief",
    }),
  );
});
```

The "omits it when blank" case needs no new test: the existing "closes and navigates on success" test already asserts `createWorkItem` is called with exactly `{ repo: "/r", title: "do a thing" }`. That assertion is the reason Step 4 spreads the key in conditionally instead of always sending it — if that existing test goes red, the implementation is wrong, not the test.

- [ ] **Step 2: Run to verify it fails**

Run: `just test-ui`
Expected: FAIL — no element labeled `description`.

- [ ] **Step 3: Add the type and the API calls**

In `frontend/src/types.ts`, on `WorkItem` after `title`:

```ts
export interface WorkItem {
  id: string;
  title: string;
  /** The brief, in prose. Prepended to every agent's task instruction; the
   *  title alone is only a label. */
  description?: string | null;
  repo: string;
```

In `frontend/src/api.ts:68`:

```ts
export const createWorkItem = (body: {
  repo: string;
  title: string;
  description?: string;
  chain_template?: string;
  submodules?: string[];
  root_merge_policy?: string;
  attachments?: { kind: "spec" | "plan"; path: string }[];
}) => req<{ id: string }>("/work-items", json("POST", body));

export const updateWorkItem = (id: string, description: string) =>
  req<{ id: string; description: string }>(
    `/work-items/${id}`,
    json("PATCH", { description }),
  );
```

Check that the `json` helper in `api.ts` passes its method through verbatim; if it is hardcoded to POST/PUT, extend it rather than hand-rolling a fetch here.

- [ ] **Step 4: Add the field to the intake form**

In `frontend/src/components/IntakeModal.tsx`, add state beside `title` (`:43`):

```tsx
  const [description, setDescription] = useState("");
```

Include it in the submit body (`:131`), omitted when blank so the server stores `NULL`:

```tsx
      const { id } = await api.createWorkItem({
        repo,
        title,
        ...(description.trim() ? { description } : {}),
        ...(tpl === "quick-task" ? {} : { chain_template: tpl }),
        ...(picked.length ? { submodules: picked, root_merge_policy: mergePolicy } : {}),
        ...(attachments.length ? { attachments } : {}),
      });
```

And the field itself, directly after the Title `div.field` block (which ends at `:186`):

```tsx
        <div className="field">
          <label htmlFor="intake-description">
            Description{" "}
            <span className="field-hint">· the brief the spec is written from</span>
          </label>
          <textarea
            id="intake-description"
            className="input"
            aria-label="description"
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
```

No `required` — the field is optional and submission must work without it.

- [ ] **Step 5: Run to verify it passes**

Run: `just test-ui`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/components/IntakeModal.tsx frontend/src/components/IntakeModal.test.tsx
git commit -m "feat: description field on the new work item form"
```

---

### Task 11: UI — show and edit the description on the detail view

**Files:**
- Modify: `frontend/src/views/WorkItemDetail.tsx` (after `<h2 className="detail-title">` `:212`)
- Modify: `frontend/src/styles.css` (one rule)
- Test: `frontend/src/views/WorkItemDetail.test.tsx`

**Interfaces:**
- Consumes: `api.updateWorkItem` (Task 10), `WorkItem.description` (Task 10), `useStore(s => s.hydrateItem)` (already imported at `:127`).
- Produces: nothing downstream.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/views/WorkItemDetail.test.tsx`, using its existing `setup(over)` / `renderDetail()` helpers. Add `waitFor` to the `@testing-library/react` import at the top of the file — it currently imports only `render, screen, within`:

```tsx
it("shows the description under the title", async () => {
  setup({ description: "the brief" });
  renderDetail();
  expect(await screen.findByTestId("item-description")).toHaveTextContent("the brief");
});

it("offers to add one when there is no description", () => {
  setup({ description: null });
  renderDetail();
  expect(screen.queryByTestId("item-description")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /add a description/i })).toBeInTheDocument();
});

it("saves an edited description and rehydrates the item", async () => {
  const update = vi
    .spyOn(api, "updateWorkItem")
    .mockResolvedValue({ id: "w1", description: "the revised brief" });
  setup({ description: "the brief" });
  renderDetail();

  await userEvent.click(
    within(screen.getByTestId("item-description")).getByRole("button", { name: /edit/i }),
  );
  const box = screen.getByLabelText("description");
  await userEvent.clear(box);
  await userEvent.type(box, "the revised brief");
  await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

  await waitFor(() => expect(update).toHaveBeenCalledWith("w1", "the revised brief"));
});
```

The Edit button is scoped with `within(...)` because the detail view has other buttons; if `/^save$/i` also proves ambiguous when you run it, scope that the same way rather than loosening the assertion.

- [ ] **Step 2: Run to verify it fails**

Run: `just test-ui`
Expected: FAIL — no such text, no such testid.

- [ ] **Step 3: Add the style**

In `frontend/src/styles.css`, beside the other `pre-wrap` rules (`:568`):

```css
.detail-description { white-space: pre-wrap; overflow-wrap: anywhere; }
```

- [ ] **Step 4: Add display and edit**

In `frontend/src/views/WorkItemDetail.tsx`, add a small component above the main view component (near the needs-context card it borrows its shape from, `:76-109`):

```tsx
/** The brief. Read-only until asked, because editing it changes what every
 *  later node is told — the same reason the edit is recorded as an event. */
function Description({ item }: { item: WorkItem }) {
  const hydrateItem = useStore((s) => s.hydrateItem);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.description ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.updateWorkItem(item.id, draft);
      await hydrateItem(item.id);
      setEditing(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    return (
      <div className="field">
        <label htmlFor="item-description-edit">Description</label>
        <textarea
          id="item-description-edit"
          className="input"
          aria-label="description"
          rows={4}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <div className="gate-actions capped-actions">
          <button className="btn btn-primary" disabled={busy} onClick={save}>
            Save
          </button>
          <button className="btn" disabled={busy} onClick={() => setEditing(false)}>
            Cancel
          </button>
        </div>
        {err && <p className="form-error">{err}</p>}
      </div>
    );
  }

  if (!item.description) {
    return (
      <button className="btn btn-quiet" onClick={() => setEditing(true)}>
        Add a description
      </button>
    );
  }

  return (
    <p className="detail-description" data-testid="item-description">
      {item.description}
      <button
        className="btn btn-quiet"
        onClick={() => {
          setDraft(item.description ?? "");
          setEditing(true);
        }}
      >
        Edit
      </button>
    </p>
  );
}
```

Render it directly under the title (`:212`):

```tsx
        <h2 className="detail-title">{item.title}</h2>
        <Description item={item} />
```

If `btn-quiet` is not an existing class in `styles.css`, use whichever secondary-button class the file already uses — do not invent a new one.

- [ ] **Step 5: Run to verify it passes**

Run: `just test-ui`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/WorkItemDetail.tsx frontend/src/views/WorkItemDetail.test.tsx frontend/src/styles.css
git commit -m "feat: show and edit a work item's description"
```

---

### Task 12: Skills and docs

The skills that tell agents what their input is, and the docs that describe the CLI and the schema. Both skill sets ship in the package.

**Files:**
- Modify: `src/kraft/skills/spec/SKILL.md:3`
- Modify: `src/kraft/skills/review-brief/SKILL.md:26`
- Modify: `src/kraft/init.py` (`SKILLS["handoff"]`, `:39` and `:52`)
- Modify: `CLAUDE.md:96`, `AGENTS.md:164`, `README.md:104`
- Modify: `docs/consolidated/02_orchestrator_core.md` §4.1 table

**Interfaces:**
- Consumes: everything above.
- Produces: nothing in code.

- [ ] **Step 1: Update the spec skill's statement of its own input**

`src/kraft/skills/spec/SKILL.md:3` currently reads:

```
description: Turn a work item title into a design a human can approve or reject in one read.
```

Change to:

```
description: Turn a work item's brief into a design a human can approve or reject in one read.
```

And in the body, under "Before you write", add a line so the agent knows where its brief comes from:

```markdown
Your task instruction is the work item's title followed by its description. The
description is the brief — treat it as the requirement, not as a hint. If it is
empty, the title is all you have, and a title is a label: lean harder on reading
the code before deciding what the change is.
```

- [ ] **Step 2: Update the review-brief skill**

`src/kraft/skills/review-brief/SKILL.md:26` says a finding must not be "a restatement of the work item title". Change to "a restatement of the work item's title or description" — the description is now the larger restatement risk.

- [ ] **Step 3: Update the handoff skill in `init.py`**

In `src/kraft/init.py`, in `SKILLS["handoff"]`, line `:39`:

```
2. `create_work_item(title, description=...)` - files the work. The title is a
   label; the description is the brief, and it is what the spec node writes its
   design from. Put the intent in the description rather than packing it into
   the title.
```

And at `:52`, the trailing clause "follow the documents rather than guess from the title" becomes "follow the documents rather than guess".

- [ ] **Step 4: Update the CLI docs**

`CLAUDE.md:96` and `AGENTS.md:164`:

```
kraft item create "title" [--description "..."]   # files it paused; a human starts it
```

`README.md:104`:

```
kraft item create "fix the flaky test" --description "..."   # files it paused; a human starts it
```

- [ ] **Step 5: Update the schema doc**

In `docs/consolidated/02_orchestrator_core.md` §4.1, add a row to the `work_items` table after the `id`/`repo` rows:

```markdown
| `description` | text, nullable — the work item's brief. Prepended to the task instruction of every agent node, ahead of the attachment note (`executor._brief`). A title is a label; this is the requirement, and the spec node writes its design from it. Mirrored into the bead's own description at intake. |
```

- [ ] **Step 6: Verify the docs match reality**

Run: `just lint`
Expected: PASS.

Then confirm the CLI help actually says what the docs claim:

```bash
uv run kraft item create --help
```

Expected: `--description` is listed with its help text.

- [ ] **Step 7: Commit**

```bash
git add src/kraft/skills src/kraft/init.py CLAUDE.md AGENTS.md README.md docs/consolidated/02_orchestrator_core.md
git commit -m "docs: describe the work item description in skills and docs"
```

---

### Task 13: Full verification

**Files:** none — this task only runs things.

- [ ] **Step 1: Run the full backend suite**

Run: `just test`
Expected: PASS. This is the ~14-minute run; it happens once, here, not per task.

- [ ] **Step 2: Run the frontend suites**

Run: `just test-ui`
Expected: PASS.

- [ ] **Step 3: Lint**

Run: `just lint`
Expected: PASS.

- [ ] **Step 4: Exercise it end to end against a dev instance**

```bash
just dev-reset && just dev
```

In another shell:

```bash
kraft item create "try the description" --description "the brief, in prose, over
multiple lines"
kraft view list
kraft view show
```

Expected: `kraft view show` prints the description as an aligned multi-line block (Task 7), and the UI at `:5173` shows it under the title on the detail view with an Edit button.

- [ ] **Step 5: Close the bead**

```bash
bd close Kraft-e821
```

- [ ] **Step 6: Post-merge note for the operator**

The agent-facing plugin under `~/.claude/skills/kraft/` is generated from `src/kraft/init.py`. After this merges and a new `kraft` is installed, re-run:

```bash
kraft admin init
```

to refresh the installed `handoff` skill with the description guidance. Nothing in this plan does that for you — it rewrites files outside the repo.

## Notes for the implementer

**Verified as unaffected — do not "fix" these:**
- The document index (`src/kraft/index/`) indexes repo markdown, not work items. `kraft view search` and the search overlay hit that index. No FTS or ingest change belongs in this work.
- `analytics.py:90` selects its work_items columns explicitly and does not need the description.
- `dev/seed.py` steers its fake agents with `KRAFT_FAIL` / `KRAFT_SLOW` substrings in the title. `_brief` only appends, so those still match.
- `Board.tsx` stays title-only. A board is a scan surface.
- `executor.py:312` and `:348` pass `work_item_row["title"]` as a session display title and a merge request title. Both stay short. Do not swap in `_brief`.

**Out of scope:** markdown rendering of the description, edit history beyond the single event, a general-purpose PATCH, editing the title, backfilling descriptions onto existing items.
