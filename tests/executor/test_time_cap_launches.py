"""Time caps at the doors outside `dispatch_node` (Kraft-8en38, Kraft-kx2fs,
Kraft-l5fl2), and each level's default cap binding every scope of its kind at
runtime (Ruling 211). The dispatch door itself is tests/executor/test_time_caps.py."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime, timedelta

import pytest
from support.harness import entry_of

from kraft import caps, escalate, events, executor, gate_review, store
from kraft.adapters import agent as agent_mod
from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.templates.environment import WorkItemTarget
from kraft.templates.models import Chain, ResolvedChain
from kraft.worker import reattach

LAUNCH = executor.LaunchContext(repo_entry=entry_of({"setup_command": ""}), steering_dir=None)


def _agent(task_id: str, **fields) -> dict:
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": "Do it.", **fields}


def _materialize(nodes, **policy_yaml):
    return ResolvedChain.from_chain(Chain.model_validate({"id": "c", "nodes": nodes})).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput.model_validate(policy_yaml)),
    )


def _task(chain, path):
    return next(t for n in chain.chain.nodes for t in n.tasks() if t.path == path)


async def _ran(it, sid, path, minutes, *, node=None, status="done"):
    """A session under `path` that ran `minutes`, ending now."""
    now = datetime.now(UTC)
    await it.session(sid, path, status, node=node)
    await it.database.write(
        lambda c: c.execute(
            "UPDATE worker_sessions SET created_at = ?, started_at = ?, exited_at = ? WHERE id = ?",
            (
                (now - timedelta(minutes=minutes)).isoformat(),
                (now - timedelta(minutes=minutes)).isoformat(),
                now.isoformat(),
                sid,
            ),
        )
    )


# ── Ruling 211: a level's default binds every scope of its kind ──────────────

#: Omid's shape (Ruling 211).
LEVEL_DEFAULTS = {
    "work_item": {"time_cap_minutes": 480},
    "nodes": {"time_cap_minutes": 180},
    "steps": {"time_cap_minutes": 120},
    "tasks": {"time_cap_minutes": 90},
}


async def test_each_levels_default_binds_every_scope_of_its_kind_that_set_none(item_on):
    """A node that sets 240 minutes runs under 240, not the nodes' default,
    and nothing under it takes a narrower level's default either (it set a
    value). A node that set nothing runs under the nodes' default, each of
    its steps under the steps', each task under the tasks'."""
    nodes = [
        {"id": "slow", "kind": "exec", "policy": {"time_cap_minutes": 240}, "tasks": [_agent("a")]},
        {
            "id": "plain",
            "kind": "exec",
            "steps": [
                {"id": "one", "tasks": [_agent("b")]},
                {"id": "two", "tasks": [_agent("c")]},
            ],
        },
    ]
    chain = _materialize(
        nodes, defaults=LEVEL_DEFAULTS, maxima={"work_item": {"time_cap_minutes": 1440}}
    )
    it = await item_on(chain)

    def left(path):
        hit = it.database.read(lambda c: caps.at_launch(c, it.row(), _task(chain, path)))
        return hit.scope, hit.remaining_s

    assert left("slow.main.a") == ("slow", 240 * 60)
    assert left("plain.one.b") == ("plain.one.b", 90 * 60)

    await _ran(it, "s-c", "plain.two.c", 100)

    assert left("plain.two.c") == ("plain.two", pytest.approx(20 * 60, abs=5))
    assert left("plain.one.b") == ("plain", pytest.approx(80 * 60, abs=5))


async def test_raising_the_items_own_cap_unsticks_a_capped_item(item_on):
    """Ruling 198's consequence: a person raises the item's cap and the next
    launch has time again, with no config edit."""
    chain = _materialize(
        [{"id": "build", "kind": "exec", "tasks": [_agent("a")]}],
        maxima={"work_item": {"time_cap_minutes": 600}},
    )
    it = await item_on(chain, "build", policy_override={"time_cap_minutes": 5})
    await _ran(it, "s-old", "build.main.a", 6)
    task = _task(chain, "build.main.a")
    assert it.database.read(lambda c: caps.at_launch(c, it.row(), task)).remaining_s <= 0

    raised = it.chain.with_item_policy({"time_cap_minutes": 60}).item_policy
    await it.database.write(lambda c: store.set_policy_override(c, it.id, raised))

    assert it.database.read(lambda c: caps.at_launch(c, it.row(), task)).remaining_s > 0


# ── Kraft-8en38: a node's cap bounds its gate review and its auto escalation ──


def _capture(monkeypatch) -> dict:
    seen: dict = {}

    async def launch(_db, _rd, **kw):
        seen.update(kw)
        return "done"

    monkeypatch.setattr(agent_mod, "run_agent_task", launch)
    return seen


def _gate(policy):
    return [
        {
            "id": "spec",
            "kind": "exec",
            "tasks": [{"id": "w", "kind": "subprocess", "command": "true"}],
        },
        {"id": "review", "kind": "gate", "policy": policy, "auto_review": _agent("reviewer")},
    ]


async def _review(it):
    return await gate_review.review(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate="review",
        node=it.chain.chain.nodes[1],
        launch=LAUNCH,
    )


async def test_a_gate_review_launches_under_its_gates_time_cap(item_on, fake_agent, monkeypatch):
    it = await item_on(_materialize(_gate({"time_cap_minutes": 20})), "review")
    seen = _capture(monkeypatch)

    await _review(it)

    assert (seen["time_cap"].hit.scope, seen["time_cap"].hit.minutes) == ("review", 20)


async def test_a_gate_review_under_a_spent_cap_never_launches(item_on, fake_agent, monkeypatch):
    it = await item_on(_materialize(_gate({"time_cap_minutes": 1})), "review")
    await _ran(it, "s-old", "review.auto_review", 2)
    seen = _capture(monkeypatch)

    verdict, note = await _review(it)

    assert seen == {} and verdict == "undecided"
    assert note == "`review` hit its time cap of 1 minutes"


def _node_with_cap():
    return _materialize(
        [
            {
                "id": "build",
                "kind": "exec",
                "policy": {"time_cap_minutes": 10},
                "tasks": [_agent("a")],
            }
        ]
    )


@pytest.mark.parametrize(("auto", "capped"), [(True, True), (False, False)], ids=["auto", "human"])
async def test_only_an_automatic_escalation_turn_runs_under_its_nodes_time_cap(
    item_on, fake_agent, monkeypatch, auto, capped
):
    """The node's cap covers its escalation (Kraft-8en38); a person talking to
    the agent is not the node running, so a human's turn is not capped."""
    it = await item_on(_node_with_cap(), "build", status="needs_human")
    seen = _capture(monkeypatch)

    await escalate.dispatch(
        it.database, it.run_dirs, work_item_id=it.id, message="help", launch=LAUNCH, auto=auto
    )

    assert (seen["time_cap"] is not None) is capped
    if capped:
        assert seen["time_cap"].hit.scope == "build"


@pytest.mark.parametrize(("auto", "left"), [(True, 5), (False, 10)], ids=["auto", "human"])
async def test_an_automatic_escalation_turn_counts_toward_its_nodes_running_time(
    item_on, auto, left
):
    it = await item_on(_node_with_cap(), "build")
    await _ran(it, "s-esc", "escalation", 5, node="build")
    await it.database.write(
        lambda c: events.append(
            c, it.id, "escalation_message", {"session_id": "s-esc", "auto": auto, "thread": 1}
        )
    )

    hit = it.database.read(lambda c: caps.at_launch(c, it.row(), _task(it.chain, "build.main.a")))

    assert hit.scope == "build"
    assert round(hit.remaining_s / 60) == left


# ── Kraft-kx2fs: an adopted session keeps its deadline ─────────────────────────


async def test_a_session_adopted_after_a_restart_is_killed_at_its_caps_deadline(
    item_on, database, monkeypatch
):
    real = time.monotonic
    monkeypatch.setattr(caps, "monotonic", lambda: real() * 600)
    # The test, not init, is this child's parent: on Linux it lingers as a
    # zombie nothing here reaps, so the group check would sit out the grace.
    monkeypatch.setattr(reattach, "_KILL_GRACE_S", 0.5)
    chain = _materialize(
        [
            {
                "id": "build",
                "kind": "exec",
                "tasks": [
                    {
                        "id": "t",
                        "kind": "subprocess",
                        "command": "sleep 30",
                        "policy": {"time_cap_minutes": 1},
                    }
                ],
            }
        ]
    )
    it = await item_on(chain, "build")
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        await it.session("s1", "build.main.t", running=(proc.pid, None))
        started = time.monotonic()

        await reattach._adopt(database, "s1", proc.pid, poll_s=0.01)

        assert time.monotonic() - started < 10
        assert proc.wait(timeout=10) is not None
    finally:
        proc.kill()
    assert it.sessions()[0]["status"] == "capped_out"
    (reached,) = it.events(caps.REACHED)
    assert reached["payload"]["session_id"] == "s1"
    assert it.status() == "needs_human"
    assert it.events("work_item_needs_human")[-1]["payload"]["reason"] == (
        "`build.main.t` hit its time cap of 1 minutes"
    )


# ── Kraft-l5fl2: the poller stops only what it measured ──────────────────────


@pytest.mark.parametrize("moved", ["status", "node", "gate"])
async def test_the_poller_does_not_stop_an_item_that_moved_since_it_measured(item_on, moved):
    chain = _materialize([{"id": "review", "kind": "gate"}, {"id": "next", "kind": "gate"}])
    it = await item_on(chain, "review", status="needs_human")
    await it.database.write(
        lambda c: events.append(c, it.id, "gate_requested", {"gate": "review", "node_id": "review"})
    )
    seen = {**it.row(), "pending_gate": "review"}
    hit = caps.Hit("review", "timeout", 1, -1)
    change = {
        "status": "UPDATE work_items SET status = 'active' WHERE id = ?",
        "node": "UPDATE work_items SET current_node_id = 'next' WHERE id = ?",
        "gate": None,
    }[moved]
    if change:
        await it.database.write(lambda c: c.execute(change, (it.id,)))
    else:
        await it.database.write(
            lambda c: events.append(c, it.id, "gate_approved", {"gate": "review", "by": "human"})
        )

    stopped = await it.database.write(lambda c: caps.stop_if_still_parked(c, seen, hit))

    assert stopped is False
    assert not it.events(caps.REACHED)


async def test_the_poller_stops_an_item_still_where_it_measured_it(item_on):
    chain = _materialize([{"id": "review", "kind": "gate"}])
    it = await item_on(chain, "review", status="needs_human")
    await it.database.write(
        lambda c: events.append(c, it.id, "gate_requested", {"gate": "review", "node_id": "review"})
    )
    seen = {**it.row(), "pending_gate": "review"}

    stopped = await it.database.write(
        lambda c: caps.stop_if_still_parked(c, seen, caps.Hit("review", "timeout", 1, -1))
    )

    assert stopped is True
    assert json.loads(json.dumps(it.events(caps.REACHED)[0]["payload"]))["scope"] == "review"
