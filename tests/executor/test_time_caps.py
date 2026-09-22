"""Per-scope time caps at runtime (Rulings 194, 195, 196, `kraft.caps`): each
configured level stops its own scope and names it; the tightest one binds; a
running process is killed at its deadline, a sandbox's container with it;
paused, wait, gate and rate-limited time is left out of running time, and only
a manual pause out of the wall clock; a cap stop spends no recovery or fix
attempt and is not escalated; and a parked item's total cap and a gate's own
timeout stop it where nothing runs.

No test waits real minutes: a launch's deadline runs on `caps.monotonic`, which
`fast` speeds up 600-fold, and the clocks a cap reads off the timeline are
seeded with timestamps in the past."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from support.harness import entry_of

from kraft import analytics, caps, executor, store
from kraft import policy as _policy
from kraft.adapters import subprocess as _subprocess
from kraft.executor import gates, walk
from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.templates.environment import WorkItemTarget
from kraft.templates.models import Chain, ResolvedChain

LAUNCH = executor.LaunchContext(repo_entry=entry_of({"setup_command": ""}), steering_dir=None)
POLICY = _policy.Policy(loops={}, default=_policy.Cap(9, 3600))
FIELDS = ("time_cap_minutes", "total_time_cap_minutes")
WHAT = {"time_cap_minutes": "time cap", "total_time_cap_minutes": "total time cap"}
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def fast(monkeypatch):
    """A launch's deadline clock, 600 times real time: a one-minute cap runs
    out a tenth of a second into the run."""
    real = time.monotonic
    monkeypatch.setattr(caps, "monotonic", lambda: real() * 600)


def _sub(task_id: str, command: str = "sleep 30", **fields) -> dict:
    return {"id": task_id, "kind": "subprocess", "command": command, **fields}


def _chain(
    *, chain=None, node=None, step=None, task=None, command="sleep 30", impl=None, **node_fields
):
    """`build` -> step `run` -> task `impl`, each given `policy:` when set."""

    def own(policy):
        return {"policy": policy} if policy else {}

    nodes = [
        {
            "id": "build",
            "kind": "exec",
            **own(node),
            "steps": [
                {
                    "id": "run",
                    "tasks": [_sub("impl", command, **own(task), **(impl or {}))],
                    **own(step),
                }
            ],
            **node_fields,
        }
    ]
    return ResolvedChain.from_chain(
        Chain.model_validate({"id": "c", **own(chain), "nodes": nodes})
    ).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput()),
    )


def _walk(it):
    return walk.walk_node(
        it.database,
        it.run_dirs,
        it.id,
        it.chain.chain.nodes[0],
        it.row(),
        it.repo,
        policy=POLICY,
        launch=LAUNCH,
    )


def _reason(it) -> str:
    return it.events("work_item_needs_human")[-1]["payload"]["reason"]


async def _level(item_on, level: str, field: str, minutes: int = 1, **chain):
    """An item at `build` whose `level` alone sets `field`."""
    if level == "item":
        return await item_on(_chain(**chain), "build", policy_override={field: minutes})
    return await item_on(_chain(**{level: {field: minutes}}, **chain), "build")


_SCOPE = {
    "task": "`build.run.impl`",
    "step": "`build.run`",
    "node": "`build`",
    "chain": "the work item",
    "item": "the work item",
}


# ── each level stops its own scope, and names it ──────────────────────────────


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("level", list(_SCOPE))
async def test_each_levels_cap_stops_its_own_scope_and_names_it(item_on, fast, level, field):
    """Ruling 194: "at any level that it's configured, it applies". The
    running task is killed at the deadline, and the item stops for a human
    under "`<scope>` hit its time cap of N minutes"."""
    it = await _level(item_on, level, field)
    started = time.monotonic()

    assert await _walk(it) == "needs_human"

    assert time.monotonic() - started < 10, "the task ran out its own sleep"
    assert _reason(it) == f"{_SCOPE[level]} hit its {WHAT[field]} of 1 minutes"
    (session,) = it.sessions()
    assert session["status"] == "capped_out"
    (reached,) = it.events(caps.REACHED)
    assert reached["payload"]["session_id"] == session["id"]
    assert reached["payload"]["field"] == field


async def test_a_task_cap_under_a_larger_step_cap_stops_the_task_at_its_own_value(item_on, fast):
    it = await item_on(_chain(step={"time_cap_minutes": 5}, task={"time_cap_minutes": 1}), "build")

    assert await _walk(it) == "needs_human"

    assert _reason(it) == "`build.run.impl` hit its time cap of 1 minutes"


async def _ran(it, path: str, start: datetime, end: datetime, status: str = "done") -> None:
    """A session under `path` that ran from `start` to `end`."""
    sid = f"s-{path}-{start.timestamp()}"
    await it.session(sid, path, status)
    await it.database.write(
        lambda c: c.execute(
            "UPDATE worker_sessions SET created_at = ?, started_at = ?, exited_at = ? WHERE id = ?",
            (start.isoformat(), start.isoformat(), end.isoformat(), sid),
        )
    )


async def test_a_launch_under_a_spent_cap_is_refused_and_nothing_runs(item_on, tmp_path):
    marker = tmp_path / "ran"
    it = await item_on(_chain(step={"time_cap_minutes": 1}, command=f"touch {marker}"), "build")
    now = datetime.now(UTC)
    await _ran(it, "build.run.impl", now - timedelta(minutes=3), now - timedelta(minutes=1))

    assert await _walk(it) == "needs_human"

    assert not marker.exists()
    assert _reason(it) == "`build.run` hit its time cap of 1 minutes"
    refused = it.sessions()[-1]
    assert refused["status"] == "capped_out"
    assert it.events(caps.REACHED)[0]["payload"]["session_id"] == refused["id"]


async def test_a_cap_stop_spends_no_attempt_and_is_not_escalated(item_on, monkeypatch):
    """Not a code failure (Ruling 194): no recovery handler, no fix cycle and
    no escalation task runs, the stop is outside the stuck set (Ruling 176),
    and the generic stuck escalation leaves it for a human too."""
    it = await item_on(
        _chain(
            step={"time_cap_minutes": 1},
            impl={"on_failure": {"tasks": [_sub("retry_it", "true")]}},
            on_failure={"tasks": [_sub("repair", "true")]},
            fix_loop={"tasks": [_sub("fix", "true")], "max_attempts": 3},
            escalation=_sub("esc", "true"),
        ),
        "build",
    )
    now = datetime.now(UTC)
    await _ran(it, "build.run.impl", now - timedelta(minutes=3), now - timedelta(minutes=1))

    assert await _walk(it) == "needs_human"

    assert not it.events("fix_cycle_started") and not it.events("stuck_escalation_started")
    assert [s["hook_point"] for s in it.sessions()] == ["build.run.impl", "build.run.impl"]
    assert "stuck" not in it.events("work_item_needs_human")[-1]["payload"]
    dispatched = []

    async def fake_dispatch(*a, **kw):
        dispatched.append(kw)
        return "done"

    monkeypatch.setattr(gates.escalate, "dispatch", fake_dispatch)
    status = await gates.auto_escalate_stuck(
        "needs_human",
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        policy=_policy.Policy(loops={}, default=_policy.Cap(1, 1), auto_escalate_delay_s=0),
        launch=LAUNCH,
    )
    assert status == "needs_human" and dispatched == []


async def test_a_time_capped_run_is_its_own_outcome_in_usage_and_analytics(item_on, fast):
    it = await item_on(_chain(task={"time_cap_minutes": 1}), "build")
    assert await _walk(it) == "needs_human"

    total = it.database.read(lambda c: store.usage_rollup(c, it.id))["total"]
    totals = it.database.read(lambda c: analytics.compute(c, range_="all"))["totals"]

    assert (total["time_capped"], total["capped_out"]) == (1, 0)
    assert (totals["time_capped"], totals["capped_out"]) == (1, 0)


async def test_the_kill_reaches_a_sandboxs_container(item_on, run_dirs, monkeypatch, tmp_path):
    """For a sandboxed task the process to stop is its container: the kill
    path tears it down, whichever way the run ends."""
    torn_down = []

    async def teardown(session_id):
        torn_down.append(session_id)

    monkeypatch.setattr(_subprocess._sandbox, "docker_argv", lambda cmd, *a, **kw: cmd)
    monkeypatch.setattr(_subprocess._sandbox, "teardown", teardown)
    it = await item_on(_chain(), "build")
    hit = caps.Hit("build.run.impl", "time_cap_minutes", 1, 0.2)

    status = await _subprocess.run_task(
        it.database,
        run_dirs,
        session_id="s1",
        work_item_id=it.id,
        node_id="build",
        hook_point="build.run.impl",
        cmd=["sleep", "30"],
        cwd=tmp_path,
        sandbox={"kind": "docker", "image": "x:1"},
        group_kill_grace=1,
        time_cap=caps.Deadline(caps.monotonic() + 0.2, hit),
    )

    assert status == caps.TIME_CAPPED
    assert torn_down == ["s1"]
    assert it.sessions()[0]["status"] == "capped_out"


# ── what the clocks leave out ────────────────────────────────────────────────


async def _event(it, type: str, at: datetime, **payload) -> None:
    await it.database.write(
        lambda c: c.execute(
            "INSERT INTO events (work_item_id, type, payload, created_at) VALUES (?, ?, ?, ?)",
            (it.id, type, json.dumps(payload), at.isoformat()),
        )
    )


def _waiting_chain():
    nodes = [
        {"id": "build", "kind": "exec", "tasks": [_sub("impl", "true")]},
        {"id": "review", "kind": "gate"},
        {
            "id": "feedback",
            "kind": "exec",
            "tasks": [{"id": "ci", "kind": "forge", "target": "mr.ci"}],
        },
    ]
    return ResolvedChain.from_chain(Chain.model_validate({"id": "c", "nodes": nodes})).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput()),
    )


async def _an_hour_of(it, kind: str) -> None:
    """Five minutes of work at T0, then an hour of `kind`."""
    m = timedelta(minutes=1)
    await _event(it, "node_started", T0, node_id="build")
    await _ran(it, "build.main.impl", T0, T0 + 5 * m)
    if kind == "pause":
        await _event(it, "pause_requested", T0 + 5 * m, sessions=[])
        await _event(it, "work_item_resumed", T0 + 65 * m, steer=None)
    elif kind == "gate":
        await _event(it, "gate_requested", T0 + 5 * m, gate="review", node_id="review")
        await _event(it, "gate_approved", T0 + 65 * m, gate="review", by="human")
    elif kind == "wait":
        await _event(it, "node_started", T0 + 5 * m, node_id="feedback")
        await _ran(it, "feedback.main.ci", T0 + 5 * m, T0 + 65 * m, "waiting")
    elif kind == "rate_limited":
        await _ran(it, "build.main.impl", T0 + 5 * m, T0 + 5 * m, "rate_limited")
        await _event(it, "work_item_rate_limited", T0 + 5 * m, node_id="build", retry_at="")


def _launch(it):
    task = it.chain.chain.nodes[0].steps[0].tasks[0]
    return it.database.read(
        lambda c: caps.at_launch(c, it.row(), task, now=(T0 + timedelta(minutes=65)).isoformat())
    )


@pytest.mark.parametrize("kind", ["pause", "gate", "wait", "rate_limited"])
async def test_running_time_leaves_out_paused_wait_gate_and_rate_limited_time(item_on, kind):
    it = await item_on(_waiting_chain(), policy_override={"time_cap_minutes": 10})
    await _an_hour_of(it, kind)

    hit = _launch(it)

    assert (hit.scope, hit.field) == ("", "time_cap_minutes")
    assert hit.remaining_s == 5 * 60


@pytest.mark.parametrize(
    ("kind", "left"), [("pause", 95), ("gate", 35), ("wait", 35), ("rate_limited", 35)]
)
async def test_the_wall_clock_leaves_out_only_a_manual_pause(item_on, kind, left):
    it = await item_on(_waiting_chain(), policy_override={"total_time_cap_minutes": 100})
    await _an_hour_of(it, kind)

    hit = _launch(it)

    assert (hit.scope, hit.field, hit.remaining_s) == ("", "total_time_cap_minutes", left * 60)


@pytest.mark.parametrize(("escalated", "left"), [(False, 10), (True, 5)])
async def test_only_a_persons_retry_starts_the_clocks_afresh(item_on, escalated, left):
    """A human `/retry` is a fresh run; the stuck escalation's own retry is
    the machinery continuing, and must not reset a cap meant to bound it."""
    it = await item_on(_waiting_chain(), policy_override={"time_cap_minutes": 10})
    await _an_hour_of(it, "pause")
    await _event(
        it, "work_item_retried", T0 + timedelta(minutes=64), node_id="build", escalated=escalated
    )
    await _event(it, "run_forked", T0 + timedelta(minutes=64))

    assert _launch(it).remaining_s == left * 60


# ── parked: a total cap and a gate's own timeout ─────────────────────────────


@pytest.mark.parametrize("status", ["waiting", "rate_limited"])
async def test_a_parked_item_past_its_total_cap_is_stopped_for_a_human(item_on, status):
    it = await item_on(
        _waiting_chain(),
        "feedback",
        status=status,
        policy_override={"total_time_cap_minutes": 1},
    )
    await it.database.write(
        lambda c: c.execute(
            "UPDATE events SET created_at = ? WHERE work_item_id = ?", (T0.isoformat(), it.id)
        )
    )

    stopped = await caps.tick(it.database, now=(T0 + timedelta(minutes=2)).isoformat())

    assert stopped == [it.id]
    assert it.status() == "needs_human"
    assert _reason(it) == "the work item hit its total time cap of 1 minutes"


async def test_a_gate_past_its_timeout_stops_naming_the_gate_and_stays_answerable(item_on):
    nodes = [{"id": "review", "kind": "gate", "timeout": "1m"}]
    chain = ResolvedChain.from_chain(Chain.model_validate({"id": "c", "nodes": nodes})).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput()),
    )
    it = await item_on(chain, "review", status="needs_human")
    await _event(it, "gate_requested", T0, gate="review", node_id="review")

    assert await caps.tick(it.database, now=(T0 + timedelta(seconds=30)).isoformat()) == []
    assert await caps.tick(it.database, now=(T0 + timedelta(minutes=2)).isoformat()) == [it.id]
    assert await caps.tick(it.database, now=(T0 + timedelta(minutes=3)).isoformat()) == []

    assert _reason(it) == "gate `review` waited past its timeout of 1 minutes"
    assert gates.pending_gate(it.database, it.id) == "review"
    assert "stuck" not in it.events("work_item_needs_human")[-1]["payload"]
    assert [e["type"] for e in it.events() if e["type"] == caps.REACHED] == [caps.REACHED]
