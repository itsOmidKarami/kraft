"""Gate review dispatch, driven against a real DB, with
`_agent.run_agent_task` monkeypatched -- the same seam
`tests/test_escalate.py` uses.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from kraft import events, executor, gate_review, store
from kraft import policy as _policy
from kraft.db import Database
from kraft.paths import RunDirs
from kraft.templates import Registry

CHAIN = {
    "nodes": [
        {
            "id": "spec",
            "tasks": ["on.spec.requested"],
            "gate_after": "spec_approval",
            "auto_escalate": True,
        }
    ]
}


async def _seed(database, rd, wid):
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="the widget",
            description="make it stop throwing",
            repo=str(rd.base),
            chain_template="default",
            chain_definition=json.dumps(CHAIN),
            auto_gate=True,
        )
    )
    await database.write(lambda c: store.enter_node(c, wid, "spec"))
    await database.write(lambda c: store.request_gate(c, wid, "spec", "spec_approval"))
    doc = rd.worktrees / wid / ".engineering" / "specs" / f"{wid}.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("# the design\n")


def _registry():
    return Registry(
        hooks={
            "on.spec.requested": {
                "kind": "agent",
                "command": "claude",
                "skill": "spec",
                "artifact": "spec",
            }
        }
    )


def _fake_agent(result: dict | None, seen: dict):
    async def fake_run_agent_task(db, run_dirs, *, session_id, **kw):
        seen["kwargs"] = kw
        seen["session_id"] = session_id
        if result is not None:
            (run_dirs.results / f"{session_id}.json").write_text(json.dumps(result))
        return result.get("status", "done") if result else "failed"

    return fake_run_agent_task


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"status": "done", "verdict": "approve"}, "approve"),
        (
            {"status": "done", "verdict": "reject", "concerns": "the spec skips migrations"},
            "reject",
        ),
        (
            {"status": "done", "verdict": "fixed", "concerns": "typo in the spec, corrected"},
            "fixed",
        ),
        ({"status": "done", "verdict": "undecided"}, "undecided"),
        # Everything ambiguous collapses to undecided: the gate stays pending
        # and a human decides. This is the whole safety story of the feature.
        ({"status": "done"}, "undecided"),
        ({"status": "done", "verdict": "APPROVE!"}, "undecided"),
        ({"status": "failed", "verdict": "approve"}, "undecided"),
        ({"status": "needs_context", "verdict": "approve"}, "undecided"),
        # A rejection with no reason is worthless as a steer note.
        ({"status": "done", "verdict": "reject", "concerns": ""}, "undecided"),
        (None, "undecided"),
    ],
)
def test_verdict_resolution(tmp_path, monkeypatch, result, expected):
    seen = {}
    monkeypatch.setattr("kraft.gate_review._agent.run_agent_task", _fake_agent(result, seen))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            await _seed(database, rd, "w1")
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            verdict, _note = await gate_review.review(
                database,
                rd,
                work_item_id="w1",
                gate="spec_approval",
                node=CHAIN["nodes"][0],
                registry=_registry(),
                launch=launch,
            )
            assert verdict == expected
        finally:
            await database.close()

    asyncio.run(scenario())


def test_dispatch_is_a_worker_with_no_resume(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "kraft.gate_review._agent.run_agent_task",
        _fake_agent({"status": "done", "verdict": "approve"}, seen),
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            await _seed(database, rd, "w1")
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            await gate_review.review(
                database,
                rd,
                work_item_id="w1",
                gate="spec_approval",
                node=CHAIN["nodes"][0],
                registry=_registry(),
                launch=launch,
            )
            kw = seen["kwargs"]
            # The safety property: a worker cannot clear its own gate, and
            # `client._forbid_self_action` is what enforces that. Flipping this
            # flag would silently hand the agent the human's standing.
            assert kw.get("identify_as_worker", True) is True
            assert kw["hook_point"] == "gate_review"
            assert kw.get("resume_session_id") is None
            assert "spec_approval" in kw["task_instruction"]
            assert ".engineering/specs/w1.md" in kw["task_instruction"]
        finally:
            await database.close()

    asyncio.run(scenario())


CHAIN2 = {
    "nodes": [
        {"id": "implementation", "tasks": [], "gate_after": None, "auto_escalate": None},
        {
            "id": "human_review",
            "tasks": [],
            "gate_after": "human_review_approval",
            "reject_to": "implementation",
            "auto_escalate": True,
        },
    ]
}

POLICY = _policy.Policy(loops={}, default=_policy.Cap(attempts=2, wall_clock_s=3600))


async def _seed_at_gate(database, rd, wid, chain, *, auto_gate):
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="the widget",
            description="make it stop throwing",
            repo=str(rd.base),
            chain_template="default",
            chain_definition=json.dumps(chain),
            auto_gate=auto_gate,
        )
    )
    await database.write(lambda c: store.enter_node(c, wid, "human_review"))
    await database.write(
        lambda c: store.request_gate(c, wid, "human_review", "human_review_approval")
    )


async def _passthrough_approve(row, gate):
    """What `api.apply_approval` returns for a gate with nothing to splice."""
    return json.loads(row["chain_definition"]), None


async def _review_from_gate(
    database, rd, chain, *, auto_gate, wid="w1", on_approve=_passthrough_approve
):
    """Enter `_review_gates` exactly as `run` does when its walk stopped at a gate."""
    await _seed_at_gate(database, rd, wid, chain, auto_gate=auto_gate)
    return await executor._review_gates(
        "awaiting_gate",
        database,
        rd,
        work_item_id=wid,
        registry=_registry(),
        policy=POLICY,
        launch=executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None),
        bd_cwd=None,
        on_approve=on_approve,
    )


def _stub_review(monkeypatch, verdict, note="because the migration is missing"):
    async def fake_review(db, run_dirs, **kw):
        return verdict, note

    monkeypatch.setattr("kraft.executor.gate_review.review", fake_review)


def _stub_walk(monkeypatch, calls, status="completed"):
    async def fake_run_once(db, run_dirs, **kw):
        calls.append((kw.get("start_index"), kw.get("steer")))
        return status

    monkeypatch.setattr("kraft.executor._run_once", fake_run_once)


@pytest.mark.parametrize(
    "verdict,expected_start",
    [
        # approve -> the node after the gate
        ("approve", 2),
        # reject -> the chain's own reject_to
        ("reject", 0),
        # fixed -> the gate node itself, so the repair is measured, not trusted
        ("fixed", 1),
    ],
)
def test_verdict_reenters_the_walk_at_the_right_node(
    tmp_path, monkeypatch, verdict, expected_start
):
    calls = []
    _stub_review(monkeypatch, verdict)
    _stub_walk(monkeypatch, calls)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            status = await _review_from_gate(database, rd, CHAIN2, auto_gate=True)
            assert status == "completed"
            assert [c[0] for c in calls] == [expected_start]
            # A rejection's reasoning is the steer for whoever redoes the work.
            if verdict == "approve":
                assert calls[0][1] is None
            else:
                assert calls[0][1] == "because the migration is missing"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_undecided_leaves_the_gate_pending(tmp_path, monkeypatch):
    calls = []
    _stub_review(monkeypatch, "undecided")
    _stub_walk(monkeypatch, calls)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            status = await _review_from_gate(database, rd, CHAIN2, auto_gate=True)
            assert status == "awaiting_gate"
            assert calls == []
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = 'w1'").fetchone()
            )
            assert row["status"] == "needs_human"
        finally:
            await database.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("auto_gate,auto_escalate", [(False, True), (True, False), (False, False)])
def test_no_review_unless_both_knobs_are_on(tmp_path, monkeypatch, auto_gate, auto_escalate):
    """The AND is this feature's safety property, so it is pinned explicitly
    rather than implied by the happy path."""
    reviewed = []

    async def fake_review(db, run_dirs, **kw):
        reviewed.append(kw["gate"])
        return "approve", ""

    monkeypatch.setattr("kraft.executor.gate_review.review", fake_review)
    chain = json.loads(json.dumps(CHAIN2))
    chain["nodes"][1]["auto_escalate"] = True if auto_escalate else None

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            status = await _review_from_gate(database, rd, chain, auto_gate=auto_gate)
            assert status == "awaiting_gate"
            assert reviewed == []
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_human_decision_taken_during_the_review_wins(tmp_path, monkeypatch):
    """The agent worked for minutes and a person approved the gate meanwhile.
    The verdict was computed against state that no longer exists, so it is
    dropped rather than written on top of the human's decision."""
    calls = []

    async def fake_review(db, run_dirs, *, work_item_id, **kw):
        await db.write(lambda c: store.approve_gate(c, work_item_id, "human_review_approval"))
        return "reject", "send it back"

    monkeypatch.setattr("kraft.executor.gate_review.review", fake_review)
    _stub_walk(monkeypatch, calls)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            status = await _review_from_gate(database, rd, CHAIN2, auto_gate=True)
            assert status == "active"  # what the human's approval left behind
            assert calls == []  # the walk was not re-entered
            types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]
            assert "gate_rejected" not in types
            assert types[-1] == "gate_auto_review_discarded"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_repeated_fixed_verdicts_breach_the_reject_loop(tmp_path, monkeypatch):
    """A reviewer that keeps repairing and re-measuring is bounded by the same
    counter a human's rejections are bounded by, and ends at a person."""
    _stub_review(monkeypatch, "fixed", note="tidied the spec again")

    async def fake_run_once(db, run_dirs, *, work_item_id, **kw):
        # Each re-entry walks straight back into the same pending gate.
        await db.write(
            lambda c: store.request_gate(c, work_item_id, "human_review", "human_review_approval")
        )
        return "awaiting_gate"

    monkeypatch.setattr("kraft.executor._run_once", fake_run_once)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            status = await _review_from_gate(database, rd, CHAIN2, auto_gate=True)
            assert status == "needs_human"
            count = database.read(
                lambda c: c.execute(
                    "SELECT count FROM retry_counters WHERE work_item_id = 'w1' AND key = ?",
                    ("human_review_approval_reject_loop",),
                ).fetchone()
            )["count"]
            assert count == 3  # attempts=2, so the third is the breach
        finally:
            await database.close()

    asyncio.run(scenario())


def test_budget_exhaustion_skips_the_review(tmp_path, monkeypatch):
    """A review Kraft cannot pay for is not started, and the gate goes to a
    human rather than being cleared by nobody."""
    reviewed = []

    async def fake_review(db, run_dirs, **kw):
        reviewed.append(kw["gate"])
        return "approve", ""

    monkeypatch.setattr("kraft.executor.gate_review.review", fake_review)
    monkeypatch.setattr(
        "kraft.executor._budget_breach",
        lambda db, wid, budget: {
            "scope": "work_item",
            "spent_usd": 11.0,
            "cap_usd": 10.0,
        },
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            status = await _review_from_gate(database, rd, CHAIN2, auto_gate=True)
            assert status == "awaiting_gate"
            assert reviewed == []
            types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]
            assert types[-1] == "gate_auto_review_skipped"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_approve_without_an_approval_door_leaves_the_gate_for_a_human(tmp_path, monkeypatch):
    """`store.approve_gate` is not the whole of an approval: `chain_finalized`
    splices the reviewed nodes in and every artifact-carrying gate indexes its
    document. Without `on_approve` those cannot run, and clearing the gate with
    half an approval is worse than not clearing it."""
    calls = []
    _stub_review(monkeypatch, "approve")
    _stub_walk(monkeypatch, calls)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            status = await _review_from_gate(database, rd, CHAIN2, auto_gate=True, on_approve=None)
            assert status == "awaiting_gate"
            assert calls == []
            assert executor.pending_gate(database, "w1") == "human_review_approval"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_approve_parks_the_item_when_the_approval_refuses(tmp_path, monkeypatch):
    """A `chain_review` artifact that is missing, corrupt, or reports `error`
    makes `apply_approval` return no chain. A person gets it, and the gate is
    not cleared on the way."""
    calls = []
    _stub_review(monkeypatch, "approve")
    _stub_walk(monkeypatch, calls)

    async def refuse(row, gate):
        return None, "chain_review: no artifact found; the worker did not write one"

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            status = await _review_from_gate(
                database, rd, CHAIN2, auto_gate=True, on_approve=refuse
            )
            assert status == "needs_human"
            assert calls == []
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = 'w1'").fetchone()
            )
            assert row["status"] == "needs_human"
            log = database.read(lambda c: events.read_after(c, 0, "w1"))
            stops = [e for e in log if e["type"] == "work_item_needs_human"]
            assert "no artifact found" in stops[-1]["payload"]["reason"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_approve_walks_the_chain_the_approval_returned(tmp_path, monkeypatch):
    """The point of routing an agent approval through the same door: a
    `chain_finalized` approval splices a new tail in, and the walk has to
    re-enter against that tail rather than the chain the gate opened on."""
    calls = []
    _stub_review(monkeypatch, "approve")
    _stub_walk(monkeypatch, calls)

    spliced = {
        "nodes": [
            CHAIN2["nodes"][0],
            CHAIN2["nodes"][1],
            {"id": "extra", "tasks": [], "gate_after": None},
        ]
    }

    async def splice(row, gate):
        return spliced, None

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            status = await _review_from_gate(
                database, rd, CHAIN2, auto_gate=True, on_approve=splice
            )
            assert status == "completed"
            assert [c[0] for c in calls] == [2]
        finally:
            await database.close()

    asyncio.run(scenario())
