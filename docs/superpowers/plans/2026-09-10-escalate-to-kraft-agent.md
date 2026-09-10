# Escalate to Kraft-Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a human open a resumable, headless agent conversation on a work item stopped at `needs_human` — `kraft item escalate <id> --message "..."` — that carries context across turns and across separate escalations on the same item, and that the agent itself can resolve by running `kraft item retry`.

**Architecture:** Every escalation turn dispatches through the existing `_subprocess.run_task` engine (the same one every chain node uses), resumed via the `claude` CLI's own `--resume <session-id>` once a thread exists, with `--autocompact auto` so the CLI keeps that transcript bounded. The dispatch deliberately omits `KRAFT_WORK_ITEM_ID` from the child's env, so `client.resolve_context()` resolves it as a human's own session (not a worker's) and the existing self-action guard — unmodified — lets it call `kraft item retry` on the very item it is escalating.

**Tech Stack:** Python (FastAPI, sqlite3, asyncio), the `claude` CLI in `--print`/stream-json mode, pytest.

**Spec:** `docs/superpowers/specs/2026-09-10-escalate-to-kraft-agent-design.md`

## Global Constraints

- Escalate is reachable only when `work_items.status == 'needs_human'` (spec: Trigger & scope).
- Runs in the work item's existing worktree; repo-level `deny_tools`/steering still apply (via `resolve_invocation`), but no *hook*-level restriction narrows it further (spec: Trigger & scope).
- One escalation turn at a time per work item — a second `escalate` call while one is `pending`/`running` is refused with 409.
- The escalation agent is not a Kraft worker (`KRAFT_WORK_ITEM_ID` is not set in its env) so it can call `kraft item retry` on its own item — this is the mechanism the spec calls "self-resume via omitted env var", not a change to `client._forbid_self_action`.
- No bespoke summarize/compact code — `--autocompact auto` (native CLI flag) is the whole answer to "keep the session from expanding."

**Deviation from the approved spec, worth flagging on review:** the spec added a `reply` field to the result-file contract as the human-facing message channel. Building this plan, that turned out to be redundant: `--output-format stream-json --verbose` already streams the agent's text turns into the session log in real time, and `kraft view logs <id> -f` already tails any worker session's log by work item. A bespoke `reply` field would duplicate that channel and require touching the shared `read_result_fields` contract every other session type also uses, for a UI need `kraft view logs` already meets — and better, since it's live rather than end-of-turn. This plan drops it; existing `status`/`concerns`/`question` fields and `.engineering/sessions/{id}.md` cover the rest of the spec's needs unchanged.

---

### Task 1: Persist the escalation thread's identity

**Files:**
- Modify: `src/kraft/db.py` (`SCHEMA_SQL`'s `work_items` table, `SCHEMA_VERSION`, `_MIGRATIONS`)
- Modify: `src/kraft/store.py` (new `set_escalation_session`, near `set_base_ref` at `src/kraft/store.py:834`)
- Test: `tests/test_db.py`, `tests/test_store.py`

**Interfaces:**
- Produces: `store.set_escalation_session(conn: sqlite3.Connection, work_item_id: str, cli_session_id: str) -> None`. `work_items.escalation_session_id` column (`TEXT`, nullable), readable off any `work_items` row already selected with `SELECT *`.

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_db.py` (same shape as `test_migration_7_adds_attachments_column`, `src/kraft/db.py`'s migration-testing pattern already in that file):

```python
def test_migration_18_adds_escalation_session_id_column(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    conn.execute("PRAGMA user_version = 18")
    conn.execute("ALTER TABLE work_items DROP COLUMN escalation_session_id")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert "escalation_session_id" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_db.py::test_migration_18_adds_escalation_session_id_column -v`
Expected: FAIL — `sqlite3.OperationalError: no such column: "escalation_session_id"` (the `DROP COLUMN` line errors first, since the column doesn't exist yet at any version).

- [ ] **Step 3: Add the column — migration, `SCHEMA_VERSION`, and `SCHEMA_SQL`**

In `src/kraft/db.py`, bump line 16:

```python
SCHEMA_VERSION = 19
```

In the `work_items` block of `SCHEMA_SQL` (`src/kraft/db.py:19-65`), add the column right after `retry_at` (line 62):

```python
  retry_at         TEXT,
  -- the `claude` CLI's own session id for this item's escalation thread
  -- (distinct from any worker_sessions.id) -- see `escalate.py`. NULL until
  -- the first escalation turn.
  escalation_session_id TEXT,
  created_at       TEXT NOT NULL,
```

In `_MIGRATIONS` (`src/kraft/db.py:126`), add key `18` right before the closing `}` (line 391):

```python
    18: ["ALTER TABLE work_items ADD COLUMN escalation_session_id TEXT"],
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_db.py::test_migration_18_adds_escalation_session_id_column -v`
Expected: PASS

- [ ] **Step 5: Write the failing store test**

Add to `tests/test_store.py`, following `test_set_base_ref`'s shape:

```python
def test_set_escalation_session(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/r",
        chain_template="quick-task",
        chain_definition="{}",
    )
    row = conn.execute("SELECT escalation_session_id FROM work_items WHERE id='w1'").fetchone()
    assert row[0] is None
    store.set_escalation_session(conn, "w1", "cli-session-abc")
    row = conn.execute("SELECT escalation_session_id FROM work_items WHERE id='w1'").fetchone()
    assert row[0] == "cli-session-abc"
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest tests/test_store.py::test_set_escalation_session -v`
Expected: FAIL — `AttributeError: module 'kraft.store' has no attribute 'set_escalation_session'`

- [ ] **Step 7: Implement `set_escalation_session`**

In `src/kraft/store.py`, right after `set_base_ref` (`src/kraft/store.py:834-844`):

```python
def set_escalation_session(conn: sqlite3.Connection, work_item_id: str, cli_session_id: str) -> None:
    """Save the `claude` CLI's own session id for this item's escalation
    thread, so the next `escalate.dispatch` can `--resume` it.

    Overwritten on every turn with whatever the run just reported, rather
    than written once: `claude --resume` can "start a copy and say so"
    (`claude --help`) instead of truly resuming, and picking up whatever id
    the CLI actually used covers that without Kraft needing to detect it.
    """
    conn.execute(
        "UPDATE work_items SET escalation_session_id = ?, updated_at = ? WHERE id = ?",
        (cli_session_id, _now(), work_item_id),
    )
```

- [ ] **Step 8: Run it to verify it passes**

Run: `uv run pytest tests/test_store.py::test_set_escalation_session tests/test_db.py::test_migration_18_adds_escalation_session_id_column -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/kraft/db.py src/kraft/store.py tests/test_db.py tests/test_store.py
git commit -m "feat: persist an escalation thread's CLI session id"
```

---

### Task 2: Let `run_agent_task` resume a session and launch as a non-worker

**Files:**
- Modify: `src/kraft/adapters/agent.py` (`Profile` at `:83-106`, `PROFILES["claude"]` at `:108-149`, `run_agent_task` at `:284-403`)
- Test: `tests/test_adapters_agent.py`

**Interfaces:**
- Consumes: nothing new — this task only widens `adapters.agent`'s own public function.
- Produces: `run_agent_task(..., resume_session_id: str | None = None, autocompact: str | None = None, identify_as_worker: bool = True)`. Every existing call site (`executor._dispatch`) keeps its current behavior unchanged, since all three new parameters default to today's shape.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_adapters_agent.py`, next to `test_default_profile_reproduces_todays_command_line` and reusing that file's `_capture_cmd`/`_run` helpers (`tests/test_adapters_agent.py:386-403`):

```python
def test_resume_session_id_adds_resume_and_autocompact_flags(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run(resume_session_id="cli-session-abc", autocompact="auto")
    cmd = seen["cmd"]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "cli-session-abc"
    assert "--autocompact" in cmd
    assert cmd[cmd.index("--autocompact") + 1] == "auto"


def test_no_resume_session_id_omits_resume_and_autocompact_flags():
    seen = {}

    async def fake_run_task(db, run_dirs, *, cmd, **kw):
        seen["cmd"] = cmd
        return "done"

    import kraft.adapters.agent as agent_mod

    agent_mod._subprocess.run_task = fake_run_task
    try:
        _run()
    finally:
        del agent_mod._subprocess.run_task
    assert "--resume" not in seen["cmd"]
    assert "--autocompact" not in seen["cmd"]


def test_identify_as_worker_false_omits_the_work_item_env_var(monkeypatch):
    seen = {}

    async def fake_run_task(db, run_dirs, *, env, **kw):
        seen["env"] = env
        return "done"

    monkeypatch.setattr("kraft.adapters.agent._subprocess.run_task", fake_run_task)
    _run(identify_as_worker=False)
    assert "KRAFT_WORK_ITEM_ID" not in seen["env"]
    assert seen["env"]["KRAFT_SESSION_ID"] == "s1"


def test_identify_as_worker_defaults_true(monkeypatch):
    seen = {}

    async def fake_run_task(db, run_dirs, *, env, **kw):
        seen["env"] = env
        return "done"

    monkeypatch.setattr("kraft.adapters.agent._subprocess.run_task", fake_run_task)
    _run()
    assert seen["env"]["KRAFT_WORK_ITEM_ID"] == "w1"
```

(The second test restores `_subprocess.run_task` by hand instead of `monkeypatch` deliberately, only to prove the point in one test without depending on the others — drop it if it reads as redundant with the third test at review time and keep `monkeypatch` throughout instead; either is fine, the assertions are what matter.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_adapters_agent.py -k "resume_session_id or identify_as_worker" -v`
Expected: FAIL — `TypeError: run_agent_task() got an unexpected keyword argument 'resume_session_id'`

- [ ] **Step 3: Add the two `Profile` fields**

In `src/kraft/adapters/agent.py`, add to the `Profile` `NamedTuple` (`:83-106`), after `permission_prompt_tool`:

```python
    permission_prompt_tool: tuple[str, ...]
    #: How this CLI spells "resume that prior session" and "keep the resumed
    #: transcript bounded on your own" — the two flags an escalation turn adds
    #: that no chain dispatch ever needs.
    resume: tuple[str, ...] = ()
    autocompact: tuple[str, ...] = ()
```

And to `PROFILES["claude"]` (`:108-149`), after `permission_prompt_tool=(...)`:

```python
        permission_prompt_tool=("--permission-prompt-tool",),
        resume=("--resume",),
        autocompact=("--autocompact",),
    ),
}
```

- [ ] **Step 4: Add the three `run_agent_task` parameters**

In `src/kraft/adapters/agent.py`, add to `run_agent_task`'s signature (`:284-308`), after `method_text: str | None = None,`:

```python
    method_text: str | None = None,
    #: `--resume <id>` when set — an escalation turn continuing its item's
    #: existing thread. `None` (every chain dispatch) omits the flag entirely,
    #: same as today.
    resume_session_id: str | None = None,
    #: `--autocompact <value>` when set. Paired with `resume_session_id` by
    #: `escalate.dispatch`; no chain dispatch sets it.
    autocompact: str | None = None,
    #: `False` only for an escalation turn: the child then gets no
    #: `KRAFT_WORK_ITEM_ID`, so `client.resolve_context()` resolves it as a
    #: human's own session rather than a worker's, and the existing
    #: self-action guard (`client._forbid_self_action`) lets it act on the
    #: very item it is escalating — see spec "The self-resume trick". Every
    #: existing caller keeps today's behavior by leaving this `True`.
    identify_as_worker: bool = True,
) -> str:
```

Then in the `cmd` list (`:357-373`), after the `permission_prompt_tool` line and before `if model:`:

```python
        *prof.permission_prompt_tool,
        PERMISSION_TOOL,
    ]
    if resume_session_id:
        cmd += [*prof.resume, resume_session_id]
    if autocompact:
        cmd += [*prof.autocompact, autocompact]
    if model:
```

And in the `env=` dict passed to `_subprocess.run_task` (`:396-400`):

```python
        env={
            **({"KRAFT_WORK_ITEM_ID": work_item_id} if identify_as_worker else {}),
            "KRAFT_SESSION_ID": session_id,
            **({"KRAFT_REVIEW_PACKAGE": review_package} if review_package else {}),
        },
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_adapters_agent.py -v`
Expected: PASS (the whole file — this confirms nothing about the default path moved)

- [ ] **Step 6: Commit**

```bash
git add src/kraft/adapters/agent.py tests/test_adapters_agent.py
git commit -m "feat: run_agent_task can resume a session and launch as a non-worker"
```

---

### Task 3: `escalate.dispatch` — one turn, end to end

**Files:**
- Create: `src/kraft/escalate.py`
- Test: `tests/test_escalate.py`

**Interfaces:**
- Consumes: `store.set_escalation_session` (Task 1), `_agent.run_agent_task(..., resume_session_id, autocompact, identify_as_worker)` (Task 2), `_agent.resolve_invocation` (existing, `src/kraft/adapters/agent.py:168`), `executor.LaunchContext` (existing, `src/kraft/executor.py:134-151`).
- Produces: `async def dispatch(db, run_dirs, *, work_item_id: str, message: str, launch: LaunchContext) -> str` — returns the same status vocabulary `_subprocess.run_task` does (`"done"`, `"failed"`, ...). Assumes its caller (Task 4) already checked the item is `needs_human` and that no escalation turn is currently running.

- [ ] **Step 1: Write the failing test**

Create `tests/test_escalate.py`:

```python
"""One escalation turn, driven directly against a real DB — no subprocess,
no chain. `escalate.dispatch`'s only external dependency is
`_agent.run_agent_task`, monkeypatched the same way
`test_adapters_agent.py::_capture_cmd` does, plus a fake log file so
`_extract_cli_session_id` has something to read.
"""

from __future__ import annotations

import json

from kraft import db, escalate, executor, store
from kraft.db import Database
from kraft.paths import RunDirs


async def _seed_needs_human(database, rd, wid: str) -> None:
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="the thing that broke",
            description="fix the widget so it stops throwing",
            repo=str(rd.base),
            chain_template="quick-task",
            chain_definition="{}",
        )
    )
    await database.write(lambda c: store.enter_node(c, wid, "implementation"))
    await database.write(
        lambda c: store.mark_needs_human(c, wid, "implementation", "task failed in node implementation")
    )


def test_dispatch_sends_the_message_and_state_and_marks_the_session_non_worker(tmp_path, monkeypatch):
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, cwd, task_instruction, **kw):
        seen["kwargs"] = kw
        seen["task_instruction"] = task_instruction
        seen["cwd"] = cwd
        log_path = run_dirs.logs / f"{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps({"type": "system", "subtype": "init", "session_id": "cli-abc"}) + "\n")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_needs_human(database, rd, wid)
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            status = await escalate.dispatch(
                database, rd, work_item_id=wid, message="the widget is at src/widget.py", launch=launch
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT escalation_session_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            return status, row[0]
        finally:
            await database.close()

    import asyncio

    status, cli_session_id = asyncio.run(scenario())
    assert status == "done"
    assert cli_session_id == "cli-abc"
    assert seen["kwargs"]["identify_as_worker"] is False
    assert seen["kwargs"]["resume_session_id"] is None  # first turn
    assert "the widget is at src/widget.py" in seen["task_instruction"]
    assert "task failed in node implementation" in seen["task_instruction"]


def test_dispatch_resumes_an_existing_thread(tmp_path, monkeypatch):
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, resume_session_id, **kw):
        seen["resume_session_id"] = resume_session_id
        log_path = run_dirs.logs / f"{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_needs_human(database, rd, wid)
            await database.write(lambda c: store.set_escalation_session(c, wid, "cli-existing"))
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            await escalate.dispatch(database, rd, work_item_id=wid, message="try again", launch=launch)
        finally:
            await database.close()

    import asyncio

    asyncio.run(scenario())
    assert seen["resume_session_id"] == "cli-existing"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_escalate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.escalate'`

- [ ] **Step 3: Implement `src/kraft/escalate.py`**

```python
"""Escalate to Kraft-Agent (docs/superpowers/specs/2026-09-10-escalate-to-kraft-agent-design.md).

One escalation turn dispatches through the same `_subprocess.run_task`
engine every chain node uses, `--resume`d once a thread exists. The
dispatch is deliberately not a Kraft worker (`identify_as_worker=False`):
`client.resolve_context()` then resolves it the same as a human's own
session standing in the worktree, which is what lets it call
`kraft item retry` on the very item it is escalating without any change to
`client._forbid_self_action`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from kraft import events, store
from kraft.adapters import agent as _agent
from kraft.executor import LaunchContext

#: Prepended to every turn's prompt, regenerated fresh each call rather than
#: diffed against a prior turn -- the live state is always correct to hand
#: over, which is the simplest way to guarantee an agent resuming a thread
#: from an earlier episode is never told something stale.
_STATE = (
    "This is an escalation: a human is asking you to help resolve a Kraft "
    "work item that is stopped and waiting on a person. The state below is "
    "current as of right now -- if it differs from what you remember from an "
    "earlier turn on this same item, trust this over your memory.\n"
    "Status: needs_human\n"
    "Current node: {node_id}\n"
    "Why it is stopped: {reason}\n"
    "{description_line}"
    "\n"
    "You are not a Kraft worker session for this launch: if you resolve the "
    "problem, you may run `kraft item retry` yourself to resume the chain. "
    "If you are not confident it is fixed, say so and stop instead -- a "
    "human decides from there.\n"
    "\n"
    "The human's message:\n{message}"
)


def _description_line(row) -> str:
    return f"Description: {row['description']}\n" if row["description"] else ""


def _reason(db, work_item_id: str) -> str:
    """The most recent `work_item_needs_human` event's reason -- the live
    answer to "why is this stopped", same reverse-scan idiom
    `executor._last_measurement`/`_needs_context_question` already use."""
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] == "work_item_needs_human":
            return e["payload"].get("reason") or "(no reason recorded)"
    return "(no reason recorded)"


def _extract_cli_session_id(log_path: Path) -> str | None:
    """The `claude` CLI's own session id, off the `system`/`init` line every
    `--output-format stream-json` run starts with -- the identity `--resume`
    takes, distinct from Kraft's own `worker_sessions.id`.

    Scanned across every line rather than assumed to be the first, the same
    defensive shape `adapters.subprocess._rate_limit_rejection` uses reading
    this same log: a line Kraft cannot parse yet must not crash a session
    that otherwise ran fine.
    """
    try:
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "system" and obj.get("subtype") == "init":
            sid = obj.get("session_id")
            if isinstance(sid, str) and sid:
                return sid
    return None


async def dispatch(
    db,
    run_dirs,
    *,
    work_item_id: str,
    message: str,
    launch: LaunchContext,
) -> str:
    """One escalation turn: send `message` into `work_item_id`'s escalation
    thread, resuming it if `work_items.escalation_session_id` is already set.

    Assumes its caller already checked the item is `needs_human` and that no
    escalation turn is currently running for it -- api.py's job, the same
    separation `executor.run`/`resume` keep from their own preconditions.
    """
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")

    session_id = uuid.uuid4().hex
    worktree = run_dirs.worktrees / work_item_id

    task_instruction = _STATE.format(
        node_id=row["current_node_id"],
        reason=_reason(db, work_item_id),
        description_line=_description_line(row),
        message=message,
    )

    # A minimal binding, no hook: `resolve_invocation` still folds in repo-level
    # deny_tools/steering/default_model, which is all "full tools" means here --
    # repo policy still applies, only a hook's own narrowing is absent because
    # there is no hook. Its own `escalate:` kwarg (left at the `False` default)
    # is the fix loop's unrelated "buy a stronger model" bump -- same word,
    # different feature; not to be confused with this module.
    inv = _agent.resolve_invocation(
        {"command": "claude"}, launch.repo_entry, launch.steering_dir, skills_dir=launch.skills_dir
    )
    status = await _agent.run_agent_task(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=row["current_node_id"],
        hook_point="escalation",
        command=inv.command,
        profile=inv.profile,
        model=inv.model,
        deny_tools=inv.deny_tools,
        steering_texts=inv.steering_texts,
        task_instruction=task_instruction,
        title=row["title"],
        repo_path=row["repo"],
        cwd=worktree,
        resume_session_id=row["escalation_session_id"],
        autocompact="auto",
        identify_as_worker=False,
    )

    cli_session_id = _extract_cli_session_id(run_dirs.logs / f"{session_id}.log")
    if cli_session_id:
        await db.write(lambda c: store.set_escalation_session(c, work_item_id, cli_session_id))
    return status
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_escalate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/kraft/escalate.py tests/test_escalate.py
git commit -m "feat: escalate.dispatch runs one resumable escalation turn"
```

---

### Task 4: `POST /work-items/{wid}/escalate`

**Files:**
- Modify: `src/kraft/api.py` (imports at `:29`, new `Escalate` model near `:855-869`, new route near `:1644-1706`)
- Create: `tests/test_escalate_api.py` (this codebase's established naming for an endpoint-focused test file — see `test_mr_labels_api.py`, `test_diff_api.py`, `test_artifact_api.py`)

**Interfaces:**
- Consumes: `escalate.dispatch` (Task 3), `_work_item_row`, `_spawn`, `_guard`, `_launch` (all existing, same file).
- Produces: `POST /api/work-items/{wid}/escalate` — body `{"message": str}` → `200 {"id": wid, "status": "escalating"}`, or `409` if the item is not `needs_human` or a turn is already `pending`/`running`, or `400` for an empty message.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_escalate_api.py`. `_client` and `_needs_human_item` below copy `tests/test_api_retry_open_log.py`'s own `_client` helper and its `test_retry_restarts_a_stopped_node_that_has_no_fix_loop`'s `KRAFT_FAIL`-title recipe verbatim — the established way this codebase drives a real item to `needs_human` under a `TestClient`. The concurrency test inserts a `worker_sessions` row through a second raw `sqlite3` connection to the same file, the same way `tests/test_api_retry_open_log.py::test_log_follow_keeps_streaming_a_pending_session` does — `app.state.db`'s own writer is bound to the `TestClient`'s internal event loop and is not safely reachable from a plain sync test function.

```python
"""POST /work-items/{wid}/escalate (spec:
docs/superpowers/specs/2026-09-10-escalate-to-kraft-agent-design.md).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    return TestClient(api.app, client=("127.0.0.1", 54321))


def _needs_human_item(client, repo, title="KRAFT_FAIL once"):
    """A real item driven to `needs_human` by the fake agent's own KRAFT_FAIL
    marker -- same recipe as
    test_api_retry_open_log.py::test_retry_restarts_a_stopped_node_that_has_no_fix_loop.
    """
    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": title, "chain_template": "quick-task"},
    ).json()["id"]
    deadline = time.monotonic() + 120
    item = None
    while time.monotonic() < deadline:
        item = client.get(f"/api/work-items/{wid}").json()
        if item["status"] == "needs_human":
            return wid
        time.sleep(0.2)
    raise AssertionError(f"work item never reached needs_human: {item}")


def test_escalate_refuses_an_item_that_is_not_needs_human(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "fine so far", "chain_template": "quick-task"},
        ).json()["id"]
        r = client.post(f"/api/work-items/{wid}/escalate", json={"message": "help"})
        assert r.status_code == 409


def test_escalate_requires_a_nonempty_message(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _needs_human_item(client, repo)
        r = client.post(f"/api/work-items/{wid}/escalate", json={"message": "   "})
        assert r.status_code == 400


def test_escalate_refuses_a_second_call_while_one_is_running(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _needs_human_item(client, repo)
        node_id = client.get(f"/api/work-items/{wid}").json()["current_node_id"]

        conn = sqlite3.connect(tmp_path / "run" / "orchestrator.db")
        conn.execute(
            "INSERT INTO worker_sessions "
            "(id, work_item_id, node_id, hook_point, log_path, result_path, status, created_at) "
            "VALUES ('running-turn', ?, ?, 'escalation', 'l', 'r', 'pending', 'now')",
            (wid, node_id),
        )
        conn.commit()
        conn.close()

        r = client.post(f"/api/work-items/{wid}/escalate", json={"message": "help"})
        assert r.status_code == 409
        assert "running-turn" in r.json()["detail"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_escalate_api.py -v`
Expected: FAIL — 404 (no such route) on all three.

- [ ] **Step 3: Implement the route**

In `src/kraft/api.py`, add `escalate` to the import line (`:29`, alphabetically between `events` and `findings`... actually `escalate` sorts before `events`):

```python
from kraft import escalate, events, executor, findings, rate_limit_retry, reattach, review, store
```

Add the model next to `Retry`/`Resume` (`:855-869`):

```python
class Escalate(BaseModel):
    message: str
```

Add the route after `retry_work_item` (`:1644-1706`):

```python
@api_router.post("/work-items/{wid}/escalate")
async def escalate_work_item(wid: str, body: Escalate, request: Request):
    """Send a message into this item's escalation thread, starting one if
    none exists yet. Only door onto a `needs_human` stop meant for
    back-and-forth with an agent rather than a one-shot retry (spec:
    docs/superpowers/specs/2026-09-10-escalate-to-kraft-agent-design.md).
    """
    st = request.app.state
    row = _work_item_row(st, wid)
    if row["status"] != "needs_human":
        raise HTTPException(409, "work item is not needs_human")
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "message is required")
    running = st.db.read(
        lambda c: c.execute(
            "SELECT id FROM worker_sessions WHERE work_item_id = ? AND hook_point = 'escalation' "
            "AND status IN ('pending', 'running') LIMIT 1",
            (wid,),
        ).fetchone()
    )
    if running is not None:
        raise HTTPException(409, f"an escalation turn ({running['id']}) is already running")
    _spawn(
        request.app,
        f"{wid}:escalate",
        _guard(
            st.db,
            wid,
            escalate.dispatch(
                st.db, st.run_dirs, work_item_id=wid, message=message, launch=_launch(st, row["repo"])
            ),
        ),
    )
    return {"id": wid, "status": "escalating"}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_escalate_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/kraft/api.py tests/test_escalate_api.py
git commit -m "feat: POST /work-items/{wid}/escalate"
```

---

### Task 5: `client.escalate`

**Files:**
- Modify: `src/kraft/client.py` (new `escalate`, after `retry` at `:696-`)
- Test: `tests/test_client_act.py`

**Interfaces:**
- Consumes: `_forbid_self_action`, `_act` (both existing, same file), the route from Task 4.
- Produces: `async def escalate(message: str, work_item_id: str | None = None) -> dict`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_client_act.py`, following `test_pause_refuses_an_item_that_is_not_running`'s shape. This file's `wired`+`run_with_app` harness runs `scenario()` itself inside the app's lifespan on one `asyncio.run()` (`test_client_read.py::run_with_app`), so — unlike Task 4's `TestClient`, which owns its own internal loop — `await api.app.state.db.write(...)` called from inside `scenario()` shares that same loop and is safe:

```python
def test_escalate_refuses_an_item_that_is_not_needs_human(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("not stuck", repo=str(repo))
        return await client.escalate("help", work_item_id=created["id"])

    with pytest.raises(ValueError, match="409"):
        run_with_app(wired, scenario)


def test_escalate_on_a_needs_human_item_schedules_a_turn(wired, tmp_path):
    from kraft import store

    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("stuck", repo=str(repo))
        wid = created["id"]
        import kraft.api as api

        await api.app.state.db.write(lambda c: store.enter_node(c, wid, "implementation"))
        await api.app.state.db.write(
            lambda c: store.mark_needs_human(c, wid, "implementation", "task failed in node implementation")
        )
        return await client.escalate("please look at this", work_item_id=wid)

    result = run_with_app(wired, scenario)
    assert result["status"] == "escalating"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_client_act.py -k escalate -v`
Expected: FAIL — `AttributeError: module 'kraft.client' has no attribute 'escalate'`

- [ ] **Step 3: Implement `client.escalate`**

In `src/kraft/client.py`, after `retry` (`:696-` — the function ends where the next top-level `def`/`async def` starts):

```python
async def escalate(message: str, work_item_id: str | None = None) -> dict:
    """Send `message` into a work item's escalation thread, starting one if
    none exists yet. Only a `needs_human` item has this door — retry/resume
    cover every other stop.

    Resumes the same underlying agent session on every later call for the
    same item, so whatever it already tried carries forward, kept bounded by
    the CLI's own `--autocompact` rather than anything Kraft does.
    """
    target = _forbid_self_action(work_item_id)
    return await _act(f"/work-items/{target}/escalate", {"message": message})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_client_act.py -k escalate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/kraft/client.py tests/test_client_act.py
git commit -m "feat: client.escalate"
```

---

### Task 6: `kraft item escalate` and the MCP tool

**Files:**
- Modify: `src/kraft/cli.py` (`_cmd_escalate` near `:480`, subparser in `_add_item` near `:766-771`)
- Modify: `src/kraft/mcp.py` (`escalate_work_item` tool, after `retry_work_item` at `:120-125`)
- Test: `tests/test_cli_verbs.py`, `tests/test_mcp.py`

**Interfaces:**
- Consumes: `client.escalate` (Task 5).
- Produces: `kraft item escalate [ID] --message "..."` (CLI); MCP tool `escalate_work_item(message: str, work_item_id: str | None = None) -> dict`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_verbs.py`, copying `test_retry_passes_the_id_and_steer_through`'s exact shape (same file, using its `app` fixture):

```python
def test_escalate_passes_the_id_and_message_through(app, monkeypatch, capsys):
    seen = {}

    async def fake_escalate(message=None, work_item_id=None):
        seen.update(message=message, work_item_id=work_item_id)
        return {"id": work_item_id, "status": "escalating"}

    monkeypatch.setattr(client, "escalate", fake_escalate)
    cli.main(["item", "escalate", "w1", "--message", "please look at this"])
    assert seen == {"message": "please look at this", "work_item_id": "w1"}
    assert "w1" in capsys.readouterr().out
```

Add `"escalate_work_item"` to the registered-tools set in `tests/test_mcp.py::test_the_tools_are_registered` — the file's own generic `test_every_tool_has_a_description_an_agent_can_act_on` then covers the new tool's docstring for free, no separate test needed:

```python
def test_the_tools_are_registered():
    assert {t.name for t in _tools()} == {
        "list_work_items",
        "get_work_item",
        "get_gate_artifact",
        "search",
        "create_work_item",
        "ensure_repo",
        "approve_gate",
        "reject_gate",
        "pause_work_item",
        "resume_work_item",
        "retry_work_item",
        "escalate_work_item",
        "set_mr_labels",
        "permission_request",
    }
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli_verbs.py -k escalate tests/test_mcp.py::test_the_tools_are_registered -v`
Expected: FAIL — unknown subcommand `escalate` on the first; the tool set doesn't match on the second.

- [ ] **Step 3: Implement the CLI verb**

In `src/kraft/cli.py`, add `_cmd_escalate` after `_cmd_retry` (`:480-481`):

```python
def _cmd_escalate(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.escalate(ns.message, ns.id)), _render_action, ns.json)
```

Add the subparser in `_add_item` after the `retry` block (`:766-771`):

```python
    escalate = subs.add_parser(
        "escalate", parents=[common], help="ask an agent to help resolve a needs_human stop"
    )
    escalate.add_argument("id", nargs="?")
    escalate.add_argument("--message", required=True, help="what to tell the agent")
    escalate.set_defaults(func=_cmd_escalate)
```

- [ ] **Step 4: Implement the MCP tool**

In `src/kraft/mcp.py`, after `retry_work_item` (`:120-125`):

```python
    @server.tool()
    async def escalate_work_item(message: str, work_item_id: str | None = None) -> dict:
        """Send a message into a Kraft work item's escalation thread — the
        door onto a needs_human stop that wants back-and-forth with an agent
        rather than a one-shot retry. Resumes the same thread on every later
        call for the same item, so whatever was already tried carries
        forward."""
        return await client.escalate(message, work_item_id)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli_verbs.py -k escalate tests/test_mcp.py::test_the_tools_are_registered -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/kraft/cli.py src/kraft/mcp.py tests/test_cli_verbs.py tests/test_mcp.py
git commit -m "feat: kraft item escalate, and the escalate_work_item MCP tool"
```

---

## After all six tasks

Run the full targeted set once end to end:

```bash
uv run pytest tests/test_db.py tests/test_store.py tests/test_adapters_agent.py tests/test_escalate.py tests/test_escalate_api.py tests/test_client_act.py tests/test_cli_verbs.py tests/test_mcp.py -v
```

Then `just lint`. Do not run the full suite (`just test` with no `-k`) — it takes ~14 minutes; the targeted set above covers everything this plan touched.
