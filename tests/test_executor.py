import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, store
from kraft.adapters import beads
from kraft.paths import RunDirs
from kraft.templates import Registry, Template, load_registry, load_templates

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _quick_task() -> Template:
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["quick-task"]


def _bd_status(repo, bead_id):
    out = subprocess.run(
        ["bd", "show", bead_id, "--json"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)[0]["status"]


def test_intake_creates_bead_and_row(tmp_path):
    tracker = isolated_bd(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo="/some/repo",
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["status"] == "active"
            assert row["bead_id"]
            assert row["chain_template"] == "quick-task"
            chain = json.loads(row["chain_definition"])
            assert [n["id"] for n in chain["nodes"]] == [
                "env_setup",
                "implementation",
                "verify",
            ]
            assert row["current_node_id"] is None
            assert _bd_status(tracker, row["bead_id"]) in ("open", "in_progress")
        finally:
            await database.close()

    asyncio.run(scenario())


def test_intake_bead_failure_writes_no_row(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            with pytest.raises(subprocess.CalledProcessError):
                await executor.intake(
                    database,
                    rd,
                    title="x",
                    repo="/r",
                    template=_quick_task(),
                    bd_cwd=str(bare),
                )
            count = database.read(
                lambda c: c.execute("SELECT count(*) FROM work_items").fetchone()[0]
            )
            assert count == 0
        finally:
            await database.close()

    asyncio.run(scenario())


def test_intake_adopts_a_given_bead_instead_of_filing_a_new_one(tmp_path, monkeypatch):
    """Auto-intake starts a bead that already exists. Filing a duplicate of it on
    every pickup is the failure this parameter exists to prevent."""
    called = False

    async def boom(*a, **kw):
        nonlocal called
        called = True
        raise AssertionError("bd create must not run when a bead_id is given")

    monkeypatch.setattr("kraft.executor.beads.intake", boom)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="adopted work",
                repo="/some/repo",
                template=_quick_task(),
                bead_id="TEST-abc",
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["bead_id"] == "TEST-abc"
        finally:
            await database.close()

    asyncio.run(scenario())
    assert called is False


def _events(database, wid):
    return [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]


def test_run_happy_path_completes_and_closes_bead(tmp_path, monkeypatch):
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "completed"

            worktree = rd.worktrees / wid
            assert "a + b" in (worktree / "calc.py").read_text()

            verify = subprocess.run(
                ["python", "-m", "pytest", "-q"], cwd=worktree, capture_output=True, text=True
            )
            assert verify.returncode == 0

            row = database.read(
                lambda c: c.execute(
                    "SELECT status, bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "completed"
            assert _bd_status(tracker, row["bead_id"]) == "closed"

            types = _events(database, wid)
            assert types[0] == "work_item_created"
            assert types[1] == "chain_loaded"
            assert types[-1] == "work_item_completed"
            assert types.count("node_started") == 3
            assert types.count("node_completed") == 3
        finally:
            await database.close()

    asyncio.run(scenario())


def test_done_with_concerns_advances_the_chain(tmp_path, monkeypatch):
    """An agent reporting `done_with_concerns` is not a failure: the chain keeps
    walking exactly as it would for `done`."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "done_with_concerns")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_CONCERNS", "tests pass but the API contract feels off")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "completed"

            row = database.read(
                lambda c: c.execute(
                    "SELECT status, bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "completed"

            impl_session = database.read(
                lambda c: c.execute(
                    "SELECT status FROM worker_sessions "
                    "WHERE work_item_id = ? AND node_id = 'implementation'",
                    (wid,),
                ).fetchone()
            )
            assert impl_session["status"] == "done_with_concerns"

            types = _events(database, wid)
            assert types[-1] == "work_item_completed"
            assert types.count("node_completed") == 3
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reconcile_accepts_a_done_with_concerns_session(tmp_path):
    """A resume over a node whose only session ended `done_with_concerns` must
    advance the chain, not report 'did not resolve cleanly'."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            from kraft.templates import Registry

            registry = Registry(
                hooks={
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.implementation.start": {"kind": "agent", "command": "unused"},
                    "on.test.run": {"kind": "subprocess", "command": ["true"]},
                }
            )
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            (rd.worktrees / wid).mkdir(parents=True, exist_ok=True)
            await database.write(lambda c: store.load_chain(c, wid, "implementation"))
            await database.write(lambda c: store.enter_node(c, wid, "implementation"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-impl",
                    work_item_id=wid,
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-impl", "done_with_concerns"))
            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                adopted={},
                bd_cwd=str(tracker),
            )
            assert result == "completed"
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["status"] == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_verify_failure_stops_at_verify(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")  # leave the bug in place
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "needs_human"

            row = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id, bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "needs_human"
            assert row["current_node_id"] == "verify"

            types = _events(database, wid)
            assert "work_item_needs_human" in types
            assert "work_item_completed" not in types
            # verify started but never completed
            assert types.count("node_started") == 3
            assert types.count("node_completed") == 2

            assert _bd_status(tracker, row["bead_id"]) in ("open", "in_progress")
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_gathers_multi_task_node(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            from kraft.templates import Registry

            registry = Registry(
                hooks={
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.a": {"kind": "subprocess", "command": ["true"]},
                    "on.b": {"kind": "subprocess", "command": ["true"]},
                }
            )
            tmpl = Template(
                id="fan",
                nodes=[
                    {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
                    {"id": "work", "tasks": ["on.a", "on.b"], "gate_after": None},
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "completed"
            sessions = database.read(
                lambda c: c.execute(
                    "SELECT node_id FROM worker_sessions WHERE work_item_id = ?", (wid,)
                ).fetchall()
            )
            work_sessions = [s for s in sessions if s["node_id"] == "work"]
            assert len(work_sessions) == 2
            types = _events(database, wid)
            assert types.count("node_completed") == 2
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_unknown_hook_in_registry_is_needs_human(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            from kraft.templates import Registry

            # env_setup resolves; the second node references a hook the registry lacks.
            registry = Registry(
                hooks={
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                }
            )
            tmpl = Template(
                id="bogus",
                nodes=[
                    {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
                    {"id": "work", "tasks": ["on.bogus"], "gate_after": None},
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "needs_human"

            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["status"] == "needs_human"

            evts = database.read(lambda c: events.read_after(c, 0, wid))
            types = [e["type"] for e in evts]
            assert "work_item_needs_human" in types
            assert "work_item_completed" not in types
            # the raised exception's repr is preserved in the needs_human reason
            reason = next(
                e["payload"]["reason"] for e in evts if e["type"] == "work_item_needs_human"
            )
            assert "on.bogus" in reason and "KeyError" in reason
        finally:
            await database.close()

    asyncio.run(scenario())


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
    assert executor._attachments(Row(attachments='[{"kind": "plan", "path": "p.md"}]')) == [
        {"kind": "plan", "path": "p.md"}
    ]


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


def test_dispatch_puts_the_attachment_note_after_the_title(tmp_path, monkeypatch):
    """Unit tests on _attachment_note/_attachments alone don't prove _dispatch
    composes them correctly (wrong order, or dropping the note entirely, would
    still pass those). This drives a real agent launch and reads back the exact
    prompt sent, the way test_fix_loop asserts steer-note ordering."""
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
                attachments=[{"kind": "spec", "path": ".engineering/specs/a.md"}],
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())

    sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
    # quick-task's only agent dispatch is the implementation node.
    assert len(sent) == 1
    prompt = sent[0]
    assert prompt.startswith(title)
    assert prompt.index("Spec: .engineering/specs/a.md") > prompt.index(title)
    assert "Do not re-plan." in prompt
    # The bead note (Kraft-a03) is appended after everything else, including
    # the attachment note.
    assert prompt.index("Do not re-plan.") < prompt.index("Do not run `bd close`")
    assert prompt.rstrip().endswith(executor._BEAD_NOTE.strip())


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
    assert sent[0].strip() == title + executor._BEAD_NOTE


def test_agent_instruction_tells_the_worker_not_to_close_beads(tmp_path, monkeypatch):
    """A worker at implementation time has verify, review and merge still
    ahead of it; closing the work item's own tracking bead there says the
    work is done before it is (Kraft-a03). Kraft closes it itself at chain
    completion (`beads.complete` in `_walk_node`) -- the instruction has to
    tell the worker to leave every bead, including its own, alone."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    prompts = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="t",
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
    assert "bd close" in sent[0]
    assert "Kraft closes it automatically" in sent[0]


# --- launch context: repo config reaches the agent launch -------------------


def _argv_lines(path: Path) -> list[list[str]]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def test_repo_default_model_reaches_the_agent_launch(tmp_path, monkeypatch):
    """`executor.run` -> `_walk_node` -> `_measure_node` -> `_dispatch` must carry
    the launch context all the way to `run_agent_task`, or a repo's configured
    default_model silently never reaches the agent."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            launch = executor.LaunchContext(
                repo_entry={"default_model": "haiku"}, steering_dir=None
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=launch,
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())

    argvs = _argv_lines(argv_log)
    assert len(argvs) == 1  # quick-task's only agent dispatch is implementation
    assert argvs[0][-2:] == ["--model", "haiku"]


def test_fix_cycle_dispatch_gets_the_same_launch_context(tmp_path, monkeypatch):
    """The fix cycle's `_dispatch` (executor.py's fix-cycle call site) is separate
    from the measuring `_dispatch` inside `_measure_node` — missing it means the
    fix agent silently runs on a different model than the one that measured."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))

    from kraft import policy
    from kraft.templates import Registry

    registry_base = fake_registry(sys.executable, _FAKE_AGENT)
    registry = Registry(
        hooks={
            **registry_base.hooks,
            # verify's own task fails every cycle without ever calling the fake
            # agent, so the only agent launch in this run is the fix cycle's.
            "on.test.run": {"kind": "subprocess", "command": [sys.executable, "-c", "exit(1)"]},
        }
    )
    tmpl = Template(
        id="fixloop",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
            {
                "id": "verify",
                "tasks": ["on.test.run"],
                "gate_after": None,
                "fix_loop": "verify_fix_loop",
            },
        ],
    )
    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  verify_fix_loop: { attempts: 1, wall_clock_s: 3600 }\n"
        "default: { attempts: 1, wall_clock_s: 3600 }\n"
    )
    pol = policy.load_policy(pol_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="fix cycle model",
                repo=str(repo),
                template=tmpl,
                bd_cwd=str(tracker),
            )
            launch = executor.LaunchContext(
                repo_entry={"default_model": "haiku"}, steering_dir=None
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=launch,
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    argvs = _argv_lines(argv_log)
    assert len(argvs) == 1  # only the fix cycle ever launches the fake agent
    assert argvs[0][-2:] == ["--model", "haiku"]


def test_run_closes_an_auto_intaken_bead_in_its_own_workspace(tmp_path, monkeypatch):
    """Auto-intake adopts a bead that already lives in its repo's own `.beads`
    workspace, not the instance-wide tracker `bd_cwd` points at. Closing it in
    `bd_cwd` fails: the id does not exist there (Kraft-8mu.5.2)."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    other = isolated_bd(tmp_path, name="other")
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            bead_id = await beads.intake("make the failing test pass", cwd=str(other))
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
                bead_id=bead_id,
                bead_cwd=str(other),
            )
            assert (
                await executor.run(
                    database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
                )
                == "completed"
            )
            assert _bd_status(other, bead_id) == "closed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_the_first_node_runs_before_env_setup_and_still_has_a_worktree(tmp_path):
    """default.yaml puts `spec` first and `env_setup` fourth, so the executor —
    not the env_setup node — is what guarantees the first task has a checkout
    to run in (Kraft-bmp)."""
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(hooks={"on.spec.requested": {"kind": "builtin", "handler": "noop"}})
            chain = {
                "template_id": "t",
                "nodes": [
                    {
                        "id": "spec",
                        "tasks": ["on.spec.requested"],
                        "gate_after": None,
                        "fix_loop": None,
                    }
                ],
            }
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B",
                    title="t",
                    repo=str(repo),
                    chain_template="t",
                    chain_definition=json.dumps(chain),
                )
            )
            await executor.run(database, rd, work_item_id="w1", registry=registry)
            assert (rd.worktrees / "w1").is_dir()
        finally:
            await database.close()

    asyncio.run(scenario())
