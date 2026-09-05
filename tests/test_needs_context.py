"""`needs_context` stops the item (both `_walk_node` branches) and can be
answered via /steer + /resume — the feature this sub-project turns on.

Executor-level scenarios follow test_fix_loop.py's shape (no fixtures, plain
`async def scenario(): ...` under `asyncio.run`). API-level scenarios follow
test_pause_resume.py's shape (a `TestClient` over the real HTTP surface).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from support.harness import fake_registry, fake_templates_dir, isolated_bd, make_repo

from kraft import db, events, executor, policy, store
from kraft.paths import RunDirs
from kraft.templates import Registry, Template, load_registry, load_templates

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _quick_task() -> Template:
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["quick-task"]


def _events(database, wid):
    return database.read(lambda c: events.read_after(c, 0, wid))


def _reason(database, wid) -> str | None:
    for e in reversed(_events(database, wid)):
        if e["type"] == "work_item_needs_human":
            return e["payload"]["reason"]
    return None


# ---------------------------------------------------------------------------
# Plain branch (`if not key:`) — on.implementation.start as a non-loop node,
# exactly as it ships in both default.yaml and quick-task.yaml.
# ---------------------------------------------------------------------------


def test_needs_context_stops_the_item_with_the_question_in_the_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_QUESTION", "which database should this target?")
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
                title="needs a decision",
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
                    "SELECT status, current_node_id FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "needs_human"
            # the plain implementation node, not the fix loop: quick-task has none
            assert row["current_node_id"] == "implementation"
            assert _reason(database, wid) == "needs_context: which database should this target?"
        finally:
            await database.close()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Fix-loop branch — a measuring task's needs_context must not consume a cycle,
# checked across two separate entries into the node (a single call passes
# trivially: bump_counter simply hasn't run yet the first time through).
# ---------------------------------------------------------------------------


def _fixloop_template() -> Template:
    return Template(
        id="fixloop-nc",
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


def _agent_measuring_registry() -> Registry:
    """`on.test.run` becomes an agent task (the fake agent) so a *measuring*
    task can report needs_context — pytest, the real on.test.run, can't."""
    base = fake_registry(sys.executable, _FAKE_AGENT)
    hooks = dict(base.hooks)
    hooks["on.test.run"] = {"kind": "agent", "command": f"{sys.executable} {_FAKE_AGENT}"}
    return Registry(hooks=hooks)


def _make_policy(tmp_path, *, attempts=3, wall_clock_s=3600) -> policy.Policy:
    p = tmp_path / "policy.yaml"
    p.write_text(
        f"loops:\n  verify_fix_loop: {{ attempts: {attempts}, wall_clock_s: {wall_clock_s} }}\n"
        f"default: {{ attempts: {attempts}, wall_clock_s: {wall_clock_s} }}\n"
    )
    return policy.load_policy(p)


def test_needs_context_from_a_measuring_task_does_not_consume_a_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_QUESTION", "which branch is the target?")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _agent_measuring_registry()
            pol = _make_policy(tmp_path)
            wid = await executor.intake(
                database,
                rd,
                title="needs a decision",
                repo=str(repo),
                template=_fixloop_template(),
                bd_cwd=str(tracker),
            )

            # cycle 1: the measuring task itself asks a question.
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
            )
            assert result == "needs_human"
            assert _reason(database, wid) == "needs_context: which branch is the target?"
            assert database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop")) is None

            # A human answers via the same mechanism /resume uses (set + take the
            # steer, relaunch from the current node) and the agent still can't
            # proceed — a second cycle, and the cap must still be untouched.
            await database.write(lambda c: store.set_steer(c, wid, "still can't tell"))
            steer = await database.write(lambda c: store.take_steer(c, wid))
            await database.write(lambda c: store.resume_work_item(c, wid, steer))
            result2 = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                start_index=1,  # "verify"
                steer=steer,
            )
            assert result2 == "needs_human"
            assert database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop")) is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_needs_context_ignores_a_stale_row_from_an_earlier_pass(tmp_path, monkeypatch):
    """A first-match scan over `sessions_for_round` (instead of latest-row-per-
    hook-point) would find pass 1's stale needs_context row forever, even after
    pass 2 reports something else entirely. `test_..._does_not_consume_a_cycle`
    above cannot catch this: it never varies the outcome between passes, so it
    passes identically under a first-match implementation. This one does vary
    it — attempts=1 so a real, distinct second-pass failure runs the fix loop to
    a cap breach in one extra cycle, cheaply."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_QUESTION", "which branch is the target?")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _agent_measuring_registry()
            pol = _make_policy(tmp_path, attempts=1)
            wid = await executor.intake(
                database,
                rd,
                title="needs a decision",
                repo=str(repo),
                template=_fixloop_template(),
                bd_cwd=str(tracker),
            )

            # pass 1: the measuring task asks a question, round 0.
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
            )
            assert result == "needs_human"
            assert _reason(database, wid) == "needs_context: which branch is the target?"

            # answered; pass 2 re-enters at round 0 too, and this time the
            # measuring task genuinely fails — no question at all. The stale
            # needs_context row from pass 1 is still sitting in worker_sessions
            # at round 0, right alongside this new one.
            monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "failed")
            await database.write(lambda c: store.set_steer(c, wid, "use main"))
            steer = await database.write(lambda c: store.take_steer(c, wid))
            await database.write(lambda c: store.resume_work_item(c, wid, steer))
            result2 = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                start_index=1,  # "verify"
                steer=steer,
            )
            # A first-match scan would see the stale round-0 needs_context row
            # and stop again with the *same* question, forever. The real
            # outcome here is a cap breach (attempts=1, the agent never fixes
            # anything under status=failed) — a different needs_human, and
            # never needs_context.
            assert result2 == "needs_human"
            reason2 = _reason(database, wid)
            assert reason2 is not None
            assert not reason2.startswith("needs_context:")
            assert "exhausted" in reason2
        finally:
            await database.close()

    asyncio.run(scenario())


def test_the_answer_reaches_the_next_launch(tmp_path, monkeypatch):
    """The steer text a human gives in answer to a needs_context stop leads the
    next agent launch's prompt, visible on the raw argv the child received."""
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_QUESTION", "which database should this target?")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    answer = "use postgres, not sqlite"

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="needs a decision",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "needs_human"
            assert _reason(database, wid) == "needs_context: which database should this target?"

            # the human answers; the next launch should complete normally.
            monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "done")
            await database.write(lambda c: store.set_steer(c, wid, answer))
            steer = await database.write(lambda c: store.take_steer(c, wid))
            await database.write(lambda c: store.resume_work_item(c, wid, steer))
            result2 = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                start_index=1,  # "implementation"
                steer=steer,
            )
            assert result2 == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())

    lines = argv_log.read_text().splitlines()
    assert any(answer in line for line in lines)


# ---------------------------------------------------------------------------
# API surface — /steer and /resume must accept a needs_human item stopped by
# needs_context, and keep 409ing everything else (a running item above all).
# ---------------------------------------------------------------------------


def _client(tmp_path, monkeypatch, *, templates_dir=None):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv(
        "KRAFT_TEMPLATES_DIR",
        str(templates_dir or fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))),
    )
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    return TestClient(api.app)


def _wait(fn, what, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = fn()
        if got:
            return got
        time.sleep(0.15)
    raise AssertionError(f"timed out waiting for {what}")


def _wait_for_status(client, wid, status, timeout=30):
    return _wait(
        lambda: (lambda b: b if b["status"] == status else None)(
            client.get(f"/work-items/{wid}").json()
        ),
        f"status == {status!r}",
        timeout=timeout,
    )


def test_steer_accepts_a_needs_context_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_QUESTION", "which repo does this target?")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "needs a decision", "chain_template": "quick-task"},
        ).json()["id"]
        _wait_for_status(client, wid, "needs_human")

        r = client.post(f"/work-items/{wid}/steer", json={"text": "use the fork"})
        assert r.status_code == 200
        assert r.json()["steer"] == "use the fork"


def test_resume_accepts_a_needs_context_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_QUESTION", "which repo does this target?")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "needs a decision", "chain_template": "quick-task"},
        ).json()["id"]
        _wait_for_status(client, wid, "needs_human")

        r = client.post(f"/work-items/{wid}/resume", json={"steer": "use the fork"})
        assert r.status_code == 200
        assert r.json()["steer"] == "use the fork"


def test_steer_still_409s_on_a_running_item(tmp_path, monkeypatch):
    """The guard widened, it did not disappear: an item mid-run is not paused
    and did not stop for needs_context, so it stays refused."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "10")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "busy", "chain_template": "quick-task"},
        ).json()["id"]
        _wait(
            lambda: next(
                (
                    s
                    for s in client.get(f"/work-items/{wid}").json()["worker_sessions"]
                    if s["hook_point"] == "on.implementation.start" and s["status"] == "running"
                ),
                None,
            ),
            "a running agent session",
        )
        assert client.post(f"/work-items/{wid}/steer", json={"text": "x"}).status_code == 409
        assert client.post(f"/work-items/{wid}/resume", json={}).status_code == 409
        client.post(f"/work-items/{wid}/pause", json={})


def test_steer_and_resume_409_on_a_needs_human_stop_that_is_not_needs_context(
    tmp_path, monkeypatch
):
    """The widened guard discriminates *within* needs_human by stop reason —
    only a needs_context stop may pass, everything else (a plain task failure
    here) still 409s. Also pins the sequence a reviewer traced by hand: stopped
    for needs_context -> answered -> ran on -> later stopped for a different
    reason. `_needs_context_stop` walks events newest-first, so it must find
    the later, non-needs_context stop and refuse — not the earlier one."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_QUESTION", "which repo does this target?")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "noop")  # leaves calc.py's bug in place throughout
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "needs a decision", "chain_template": "quick-task"},
        ).json()["id"]
        _wait_for_status(client, wid, "needs_human")
        assert client.get(f"/work-items/{wid}").json()["current_node_id"] == "implementation"

        # answered — resumes past the needs_context stop.
        monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "done")
        assert (
            client.post(f"/work-items/{wid}/resume", json={"steer": "use the fork"}).status_code
            == 200
        )

        # verify's real pytest run genuinely fails (calc.py's bug was never
        # touched — KRAFT_FAKE_CLAUDE=noop) — a different needs_human stop.
        _wait(
            lambda: (
                lambda b: (
                    b if b["status"] == "needs_human" and b["current_node_id"] == "verify" else None
                )
            )(client.get(f"/work-items/{wid}").json()),
            "verify to fail for real",
        )

        assert client.post(f"/work-items/{wid}/steer", json={"text": "x"}).status_code == 409
        assert client.post(f"/work-items/{wid}/resume", json={}).status_code == 409


def _await_gate(client, wid, gate, timeout=60):
    return _wait(
        lambda: client.get(f"/work-items/{wid}").json()["pending_gate"] == gate,
        f"pending gate {gate!r}",
        timeout=timeout,
    )


def test_a_gate_after_an_answered_needs_context_is_not_a_needs_context_stop(tmp_path, monkeypatch):
    """A pending gate also sets status 'needs_human' and appends no
    `work_item_needs_human`, so without an end boundary the answered-and-resumed
    needs_context stop still reads as live: the question keeps rendering (hiding
    the gate card, which for `human_review_approval` is the only approve
    affordance) and /steer + /resume answer a stop that is long over — /resume
    re-walking the gated node."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_QUESTION", "which database should this target?")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "needs a decision", "chain_template": "default"},
        ).json()["id"]
        for gate in ("spec_approval", "plan_approval", "chain_finalized"):
            _await_gate(client, wid, gate)
            assert client.post(f"/work-items/{wid}/gates/{gate}/approve").status_code == 200

        # implementation asks its question and stops.
        _wait(
            lambda: (lambda b: b if b["needs_context_question"] else None)(
                client.get(f"/work-items/{wid}").json()
            ),
            "the needs_context question",
        )
        item = client.get(f"/work-items/{wid}").json()
        assert item["needs_context_question"] == "which database should this target?"
        assert item["current_node_id"] == "implementation"

        # answered — the chain runs on to the last gate.
        monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "done")
        assert (
            client.post(f"/work-items/{wid}/resume", json={"steer": "the fork"}).status_code == 200
        )
        _await_gate(client, wid, "human_review_approval")

        item = client.get(f"/work-items/{wid}").json()
        assert item["pending_gate"] == "human_review_approval"
        assert item["needs_context_question"] is None
        assert client.post(f"/work-items/{wid}/steer", json={"text": "x"}).status_code == 409
        assert client.post(f"/work-items/{wid}/resume", json={}).status_code == 409
        assert (
            client.post(f"/work-items/{wid}/gates/human_review_approval/approve").status_code == 200
        )
