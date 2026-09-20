import asyncio
from pathlib import Path

from support.harness import _git, isolated_bd, make_repo

from kraft import db, executor, policy, store
from kraft.config import git_read
from kraft.paths import RunDirs
from kraft.templates import Registry, Template, load_registry, load_templates

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: A repo that deliberately needs no preparation. Most tests here are about
#: resuming a chain, not environments.
NO_SETUP = {"setup_command": ""}


def _quick_task() -> Template:
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["quick-task"]


def test_reconcile_accepts_a_done_with_concerns_session(tmp_path):
    """A resume over a node whose only session ended `done_with_concerns` must
    advance the chain, not report 'did not resolve cleanly'."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
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
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "completed"
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["status"] == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reconcile_reuses_a_done_measuring_session_after_a_crash(tmp_path):
    """Kraft-gl9d, the story from the spec's Why section: a crash mid-`verify`
    that already saw one measuring task exit 'done' must not spend a second
    agent session re-confirming it on reconciliation -- only the failed task
    is redispatched. The fix loop's cap (attempts=0) trips on the very first
    bump, so this proves the point without needing a working fix agent."""
    tracker = isolated_bd(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.test.run": {"kind": "subprocess", "command": ["false"]},
                    "on.review.local.run": {"kind": "subprocess", "command": ["true"]},
                    "on.implementation.start": {"kind": "agent", "command": "unused"},
                }
            )
            template = Template(
                id="t",
                nodes=[
                    {
                        "id": "env_setup",
                        "tasks": ["on.env.prepare"],
                        "gate_after": None,
                        "fix_loop": None,
                    },
                    {
                        "id": "verify",
                        "tasks": ["on.test.run", "on.review.local.run"],
                        "gate_after": None,
                        "fix_loop": "verify_fix_loop",
                    },
                ],
            )
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(make_repo(tmp_path)),
                template=template,
                bd_cwd=str(tracker),
            )
            # Build the worktree by hand, bypassing env_setup, the same way
            # test_reconcile_accepts_a_done_with_concerns_session does above --
            # `ensure_worktree` is idempotent on an existing directory, so
            # `executor.resume`'s own call to it just returns this untouched.
            worktree = rd.worktrees / wid
            worktree.mkdir(parents=True, exist_ok=True)
            _git(worktree, "init", "-q", "-b", "main")
            (worktree / "f.txt").write_text("x")
            _git(worktree, "add", "-A")
            _git(worktree, "commit", "-q", "-m", "init")
            head = git_read(worktree, "rev-parse", "HEAD")

            await database.write(lambda c: store.load_chain(c, wid, "verify"))
            await database.write(lambda c: store.enter_node(c, wid, "verify"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-done",
                    work_item_id=wid,
                    node_id="verify",
                    hook_point="on.review.local.run",
                    log_path="/l1",
                    result_path="/r1",
                    round=0,
                    head_sha=head,
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-done", "done"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-failed",
                    work_item_id=wid,
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="/l2",
                    result_path="/r2",
                    round=0,
                    head_sha=head,
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-failed", "failed"))

            # A zero cap cannot be built through the constructor (nor written in
            # a policy.yaml); force one so the first bump breaches.
            zero = policy.Cap(1, 3600)
            object.__setattr__(zero, "attempts", 0)
            pol = policy.Policy(
                loops={"verify_fix_loop": zero},
                default=zero,
                auto_escalate_stuck=False,
            )
            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                adopted={},
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            rows = database.read(
                lambda c: c.execute(
                    "SELECT hook_point FROM worker_sessions WHERE work_item_id = ? "
                    "AND node_id = 'verify'",
                    (wid,),
                ).fetchall()
            )
            return result, [r["hook_point"] for r in rows]
        finally:
            await database.close()

    result, hooks = asyncio.run(scenario())
    assert result == "needs_human"  # verify_fix_loop's attempts=0 cap breaches on the first bump
    # the done review was never redispatched; the failed test was
    assert hooks.count("on.review.local.run") == 1
    assert hooks.count("on.test.run") == 2


def test_reconcile_reproduces_kraft_s15p0s_discarded_plan_session(tmp_path):
    """The exact shape Kraft-s15p0 recorded: a single-task node whose first
    attempt failed alongside a failed escalation, and whose second attempt's
    task succeeded alongside a done escalation. `len(final) == len(tasks)`
    must be checked against attempt 2's own task row only -- 4 worker_sessions
    rows exist in total, but only 1 is the node's own current-attempt task."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.plan.requested": {"kind": "agent", "command": "unused"},
                }
            )
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                template=Template(
                    id="t",
                    nodes=[
                        {
                            "id": "plan",
                            "tasks": ["on.plan.requested"],
                            "gate_after": None,
                            "fix_loop": None,
                        }
                    ],
                ),
                bd_cwd=str(tracker),
            )
            (rd.worktrees / wid).mkdir(parents=True, exist_ok=True)
            await database.write(lambda c: store.load_chain(c, wid, "plan"))

            # Attempt 1: the task fails, an escalation over it also fails.
            await database.write(lambda c: store.enter_node(c, wid, "plan"))
            for sid, hook, status in (
                ("s-task-1", "on.plan.requested", "failed"),
                ("s-esc-1", "escalation", "failed"),
            ):
                await database.write(
                    lambda c, sid=sid, hook=hook: store.create_session(
                        c,
                        id=sid,
                        work_item_id=wid,
                        node_id="plan",
                        hook_point=hook,
                        log_path=f"/{sid}-l",
                        result_path=f"/{sid}-r",
                    )
                )
                await database.write(
                    lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                )

            # Attempt 2 (a retry): the escalation succeeds, then the task
            # itself succeeds -- the server restarts here, mid-session, in
            # Kraft-s15p0's own story, and `session_reattached` adopts it.
            await database.write(lambda c: store.enter_node(c, wid, "plan"))
            for sid, hook, status in (
                ("s-esc-2", "escalation", "done"),
                ("s-task-2", "on.plan.requested", "done"),
            ):
                await database.write(
                    lambda c, sid=sid, hook=hook: store.create_session(
                        c,
                        id=sid,
                        work_item_id=wid,
                        node_id="plan",
                        hook_point=hook,
                        log_path=f"/{sid}-l",
                        result_path=f"/{sid}-r",
                    )
                )
                await database.write(
                    lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                )

            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                adopted={},
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            return result, row["status"]
        finally:
            await database.close()

    result, status = asyncio.run(scenario())
    assert result == "completed"
    assert status == "completed"


def test_reconcile_still_needs_human_when_latest_attempt_failed(tmp_path):
    """Only the latest attempt's status is authoritative — an earlier done
    attempt does not rescue a task whose most recent attempt failed."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
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
                    id="s-impl-1",
                    work_item_id=wid,
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path="/l1",
                    result_path="/r1",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-impl-1", "done"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-impl-2",
                    work_item_id=wid,
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path="/l2",
                    result_path="/r2",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-impl-2", "failed"))
            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                adopted={},
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "needs_human"
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["status"] == "needs_human"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resume_threads_local_files_from_the_launch_context(tmp_path):
    """Kraft-gxcmy's production wiring, the resume-side copy of the check in
    `test_executor_walk.py::test_run_once_threads_local_files_from_the_launch_context`.
    `resume_once` (`resuming.py:167`) is a copy-paste of `run_once`'s
    `local_files=` line -- a copy-paste that could just as easily have been
    dropped. Resuming a freshly-intaken item (crash before the first
    `load_chain`, `current_node_id` still NULL) takes `resume`'s own
    worktree-creation path rather than reusing one `run_once` already made, so
    this actually exercises `_carry_local_files`, not just an early return."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text(".python-version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")

    tmpl = Template(
        id="env-only",
        nodes=[{"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None}],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={"on.env.prepare": {"kind": "builtin", "handler": "env_setup"}}
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            launch = executor.LaunchContext(
                repo_entry={"local_files": [".python-version"], "setup_command": ""},
                steering_dir=None,
            )
            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                adopted={},
                bd_cwd=str(tracker),
                launch=launch,
            )
            assert result == "completed"
            assert (rd.worktrees / wid / ".python-version").read_text() == "3.11\n"
        finally:
            await database.close()

    asyncio.run(scenario())
