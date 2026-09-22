"""External waits: one due scheduler owns every wait (`kraft.waits`).

A forge task that observes an external condition records the wait instance
(its start, every observation, its outcome), and a pending condition parks the
item with the next observation time instead of holding a worker. `waits.tick`
re-enters whatever is due. The forge side of each wait kind -- what one
observation reads -- is tests/adapters/forge/test_waits.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from support.harness import v1_chain, v1_item, v1_resolved

from kraft import events, executor, policy, store, waits
from kraft.adapters import forge
from kraft.policy import InstancePolicy, InstancePolicyInput, PolicyError

ON_A_FORGE = {"setup_command": "", "forge": "github"}
#: A `now` every parked wait is due by.
LATER = "2999-01-01T00:00:00+00:00"


def forge_node(node_id: str, target: str, **task) -> dict:
    """A one-task exec node running forge `target` (as tests/adapters/forge/nodes.py)."""
    return {
        "id": node_id,
        "kind": "exec",
        "tasks": [{"id": node_id, "kind": "forge", "target": target, **task}],
    }


def _wait(timeout="10m", initial="30s", maximum="2m") -> dict:
    return {"timeout": timeout, "polling": {"initial_interval": initial, "max_interval": maximum}}


@pytest.fixture
def walk(database, run_dirs, monkeypatch):
    """`await walk(fake, item)`: `executor.run` on `item` from its own cursor,
    with every forge task answered by `fake` -- the same re-entry the scheduler
    makes. Returns the run's status."""

    async def go(fake, it):
        monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
        if it.status() == "waiting":
            await database.write(lambda c: store.mark_reentered(c, it.id))
        return await executor.run(
            database,
            run_dirs,
            work_item_id=it.id,
            # No stuck escalation: these tests are about the stop, not what
            # answers it.
            policy=policy.Policy(
                loops={},
                default=policy.Cap(attempts=3, wall_clock_s=3600),
                auto_escalate_stuck=False,
            ),
            launch=executor.LaunchContext(repo_entry=ON_A_FORGE, steering_dir=None),
        )

    return go


@pytest.fixture
def scheduler(stub_app, tmp_path, monkeypatch):
    """`scheduler(fake=None)`: the app `waits.tick` reads, its re-entries
    launched on a repo whose forge is `fake`."""
    from kraft.api import deps

    def make(fake=None):
        if fake is not None:
            monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
        monkeypatch.setattr(
            deps,
            "launch",
            lambda st, repo: executor.LaunchContext(repo_entry=ON_A_FORGE, steering_dir=None),
        )
        return stub_app(
            templates_dir=tmp_path / "templates",
            skills_dir=tmp_path / "skills",
            policy=None,
        )

    return make


def _trail(it, task: str) -> list[tuple[str, str | None]]:
    """This task's wait events: (type, observed state or outcome)."""
    return [
        (e["type"], e["payload"].get("state") or e["payload"].get("outcome"))
        for e in it.events()
        if e["type"].startswith("external_wait_") and e["payload"]["task"] == task
    ]


# --- a pending wait parks, and backs off ---------------------------------------


async def test_pending_wait_releases_worker_and_reschedules_with_backoff(walk, item_on, wait_clock):
    """`external-wait-does-not-hold-an-active-worker`: one observation, then
    the walk returns. The item is parked with its next observation time, and
    the wait's condition and timeout are on record for whoever looks next."""
    it = await item_on([forge_node("ci", "mr.ci", wait=_wait())])

    assert await walk(forge.FakeForge(ci_states=["pending"]), it) == "waiting"

    assert it.status() == "waiting"
    assert it.row()["retry_at"] == wait_clock.at(30)
    (started,) = [e["payload"] for e in it.events("external_wait_started")]
    assert started == {
        "task": "ci.main.ci",
        "node_id": "ci",
        "kind": "mr.ci",
        "timeout_s": 600.0,
        "initial_interval_s": 30.0,
        "max_interval_s": 120.0,
        "started_at": wait_clock.at(0),
        "deadline": wait_clock.at(600),
    }
    assert _trail(it, "ci.main.ci") == [
        ("external_wait_started", None),
        ("external_wait_observed", "pending"),
    ]


async def test_interval_grows_from_initial_to_max_while_pending(walk, item_on, wait_clock):
    """`external-waits-use-a-shared-due-scheduler`: 30s, then doubling, and
    never past the 2m maximum while the condition stays pending."""
    it = await item_on([forge_node("ci", "mr.ci", wait=_wait())])
    fake = forge.FakeForge(ci_states=["pending"])
    gaps = []
    for _ in range(5):
        assert await walk(fake, it) == "waiting"
        gap = (datetime.fromisoformat(it.row()["retry_at"]) - wait_clock.now).total_seconds()
        gaps.append(gap)
        wait_clock.advance(gap)

    assert gaps == [30, 60, 120, 120, 120]
    observed = [e["payload"]["observation"] for e in it.events("external_wait_observed")]
    assert observed == [1, 2, 3, 4, 5]


async def test_a_node_waiting_on_two_conditions_is_due_at_the_earlier_one(
    walk, item_on, wait_clock
):
    """Two waits in one step park the item once, at whichever observation
    comes due first -- the other is simply observed again with it."""
    node = {
        "id": "feedback",
        "kind": "exec",
        "tasks": [
            {
                "id": "ci",
                "kind": "forge",
                "target": "mr.ci",
                "wait": _wait(initial="5m", maximum="5m"),
            },
            {"id": "approval", "kind": "forge", "target": "mr.external_approval", "wait": _wait()},
        ],
    }
    it = await item_on([node])
    fake = forge.FakeForge(ci_states=["pending"], approval_states=["pending"])

    assert await walk(fake, it) == "waiting"

    assert it.row()["retry_at"] == wait_clock.at(30)


# --- timeout -------------------------------------------------------------------


async def test_wait_timeout_stops_for_human_and_is_not_a_code_failure(walk, item_on, wait_clock):
    """`external-wait-timeout-needs-human`: the last observation, at the
    deadline, is still pending, so the item stops for a person. No recovery
    handler and no fix cycle runs -- nothing about the code failed -- and the
    session reads neither done nor failed."""
    node = forge_node("ci", "mr.ci", wait=_wait(timeout="1m", initial="45s", maximum="45s"))
    node["on_failure"] = {"tasks": [{"id": "repair", "kind": "subprocess", "command": "true"}]}
    node["fix_loop"] = {"tasks": [{"id": "fix", "kind": "subprocess", "command": "true"}]}
    it = await item_on([node])
    fake = forge.FakeForge(ci_states=["pending"])

    assert await walk(fake, it) == "waiting"
    wait_clock.advance(45)
    assert await walk(fake, it) == "waiting"
    # The next observation is due at the deadline, not past it.
    assert it.row()["retry_at"] == wait_clock.at(60)
    wait_clock.advance(15)

    assert await walk(fake, it) == "needs_human"

    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert "timed out" in reason and "ci.main.ci" in reason
    assert not it.events("fix_cycle_started")
    assert [(s["hook_point"], s["status"]) for s in it.sessions()] == [
        ("ci.main.ci", "capped_out")
    ], "a repair ran, or the timed-out wait reads as something it is not"
    assert _trail(it, "ci.main.ci")[-1] == ("external_wait_ended", "timed_out")


async def test_a_wait_timeout_and_a_loop_cap_are_reported_apart(walk, item_on, wait_clock):
    """Kraft-uwbc8: both sessions end `capped_out` (no migration), but a wait
    that ran out is not a fix loop that ran out. Usage and analytics count
    them apart, from the wait's own `external_wait_ended` record."""
    from kraft import analytics

    it = await item_on([forge_node("ci", "mr.ci", wait=_wait(timeout="1m", initial="1m"))])
    fake = forge.FakeForge(ci_states=["pending"])
    assert await walk(fake, it) == "waiting"
    wait_clock.advance(60)
    assert await walk(fake, it) == "needs_human"
    # A fix-loop breach marks the node's measuring sessions the same way.
    await it.session("s-loop", "ci.main.ci", "capped_out")

    (node,) = it.database.read(lambda c: store.usage_rollup(c, it.id))["by_node"]
    ci = next(
        n
        for n in it.database.read(lambda c: analytics.compute(c, range_="all"))["by_node"]
        if n["node"] == "ci"
    )

    for rollup in (node, ci):
        assert (rollup["capped_out"], rollup["wait_timed_out"]) == (1, 1), rollup


# --- one scheduler, every wait kind --------------------------------------------


@pytest.mark.parametrize(
    "target, fake_kw",
    [
        ("mr.ci", {"ci_states": ["pending", "success"]}),
        ("mr.automated_review", {"review_results": ["pending", "clean"]}),
        ("mr.external_approval", {"approval_states": ["pending", "approved"]}),
        ("mr.merge", {"merge_delay": 1}),
        ("mr.post_merge_ci", {"branch_ci_states": ["pending", "success"]}),
    ],
    ids=["ci", "automated-review", "external-approval", "merge-completion", "post-merge-ci"],
)
async def test_every_wait_kind_parks_and_is_resumed_by_the_one_scheduler(
    walk, item_on, scheduler, target, fake_kw
):
    """`external-wait-covers-merge-request-lifecycle`: each of the five kinds
    parks on its first pending observation, and the same `waits.tick` wakes
    it and walks it on. None of them sleeps in-process (Kraft-7jja)."""
    fake = forge.FakeForge(**fake_kw)
    it = await item_on([forge_node("open", "mr.open_draft"), forge_node("wait", target)])

    assert await walk(fake, it) == "waiting"
    app = scheduler(fake)
    assert await waits.tick(app, now=LATER) == [it.id]
    await asyncio.gather(*app.state.tasks.values())

    assert it.status() == "completed"
    assert _trail(it, "wait.main.wait") == [
        ("external_wait_started", None),
        ("external_wait_observed", "pending"),
        ("external_wait_observed", "settled"),
        ("external_wait_ended", "settled"),
    ]


# --- a wait instance owns its clock (Kraft-3r9fe) ------------------------------


def _observe(conn, state: str, result: str = "pending"):
    from kraft.templates.models import WaitBounds

    return waits.observe(
        conn,
        "w1",
        node_id="ci",
        task="ci.main.ci",
        kind="mr.ci",
        bounds=WaitBounds.from_seconds(timeout=60, initial=30, maximum=30),
        condition="ci",
        state=state,
        result=result,
    )


@pytest.mark.parametrize("ends_with", ["settled", "retried"])
async def test_a_second_pass_starts_its_own_wait_clock(database, repo, wait_clock, ends_with):
    """Kraft-3r9fe: the first pass's wait ended (it settled, or the item was
    retried under it), so a later pass on the same task is a new instance with
    its own deadline -- never the old one's, long since passed."""
    await v1_item(database, v1_chain([forge_node("ci", "mr.ci")], repo=repo), repo=repo)
    await database.write(lambda c: _observe(c, "pending"))
    if ends_with == "settled":
        await database.write(lambda c: _observe(c, "settled", "done"))
    else:
        await database.write(lambda c: events.append(c, "w1", "work_item_retried", {}))
    wait_clock.advance(3600)

    assert await database.write(lambda c: _observe(c, "pending")) == "pending"

    starts = [
        e["payload"]["deadline"]
        for e in database.read(lambda c: events.read_after(c, 0, "w1"))
        if e["type"] == "external_wait_started"
    ]
    assert starts == [wait_clock.at(60), wait_clock.at(3660)]


async def test_a_retry_on_a_run_fork_starts_a_fresh_wait(walk, item_on, wait_clock):
    """A `/retry` (`executor.retry`: a run fork over the waiting node) is a
    fresh budget. The wait it re-observes starts its own clock rather than
    timing out on the one the first pass started an hour ago."""
    from kraft.templates.forks import ChainPath

    it = await item_on([forge_node("ci", "mr.ci", wait=_wait(timeout="1m"))])
    fake = forge.FakeForge(ci_states=["pending"])
    assert await walk(fake, it) == "waiting"
    wait_clock.advance(3600)
    await it.database.write(lambda c: store.mark_reentered(c, it.id))

    result = await executor.retry(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        target=ChainPath.parse(store.materialized_chain_of(it.row()), "ci"),
        launch=executor.LaunchContext(repo_entry=ON_A_FORGE, steering_dir=None),
    )

    assert result == "waiting", "the retried wait timed out on its first pass's clock"
    starts = [e["payload"]["deadline"] for e in it.events("external_wait_started")]
    assert starts == [wait_clock.at(60), wait_clock.at(3660)]


# --- automated review feedback enters the node's controls ----------------------


@pytest.mark.parametrize("control", ["on_failure", "fix_loop"])
async def test_actionable_automated_review_is_repaired_resynced_and_remeasured(
    walk, item_on, control
):
    """`post-draft-feedback-uses-node-recovery-controls`: actionable review
    feedback fails the review task with its findings, the node's declared
    control repairs it and resyncs the draft, and the node is measured again
    from its first step -- where the review now settles clean."""
    review = {"id": "await_review", "kind": "forge", "target": "mr.automated_review"}
    node = {
        "id": "feedback",
        "kind": "exec",
        "steps": [
            {"id": "ci", "tasks": [{"id": "await_ci", "kind": "forge", "target": "mr.ci"}]},
            {"id": "automated_review", "tasks": [review]},
        ],
        control: {
            "steps": [
                {"id": "repair", "tasks": [{"id": "fix", "kind": "subprocess", "command": "true"}]},
                {"id": "sync", "tasks": [{"id": "sync", "kind": "forge", "target": "mr.sync"}]},
            ]
        },
    }
    fake = forge.FakeForge(
        review_results=[
            forge.ReviewResult("actionable", findings=("rename `x` to `total`",)),
            forge.ReviewResult("clean"),
        ]
    )
    it = await item_on([forge_node("open", "mr.open_draft"), node])

    assert await walk(fake, it) == "completed"

    assert [s["hook_point"] for s in it.sessions("feedback")] == [
        "feedback.ci.await_ci",
        "feedback.automated_review.await_review",
        f"feedback.{control}.repair.fix",
        f"feedback.{control}.sync.sync",
        "feedback.ci.await_ci",
        "feedback.automated_review.await_review",
    ]
    assert fake.bodies, "the draft was never resynced after the repair"
    ended = [
        e["payload"]["result"]
        for e in it.events("external_wait_ended")
        if e["payload"]["task"] == "feedback.automated_review.await_review"
    ]
    # A wait's result is its task's status: actionable feedback failed it.
    assert ended == ["failed", "done"]


async def test_a_chain_without_an_automated_review_task_never_waits_for_one(walk, item_on):
    """`automated-review-is-an-explicit-optional-task`: a reviewer that would
    stay pending forever is never asked, because nothing declared it."""

    class NeverAsk(forge.FakeForge):
        async def automated_review(self, *, repo, branch):
            raise AssertionError("asked for an automated review no task declared")

    it = await item_on([forge_node("open", "mr.open_draft"), forge_node("ci", "mr.ci")])

    assert await walk(NeverAsk(review_results=["pending"]), it) == "completed"


async def test_the_repository_s_named_reviewer_reaches_the_review_task(
    item_on, database, run_dirs, monkeypatch
):
    """Ruling 171: which reviewer is the repository's (`repos.yaml`), handed to
    the backend at dispatch -- never the template's."""
    from kraft.automated_review import AutomatedReview

    asked = []

    class Recording(forge.FakeForge):
        async def automated_review(self, *, repo, branch, reviewer=None):
            asked.append(reviewer)
            return await super().automated_review(repo=repo, branch=branch)

    monkeypatch.setattr(forge.run, "resolve", lambda name: Recording())
    it = await item_on([forge_node("review", "mr.automated_review")])
    entry = {**ON_A_FORGE, "automated_review": {"bot": "coderabbitai", "check": None}}

    await executor.run(
        database,
        run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(repo_entry=entry, steering_dir=None),
    )

    assert asked == [AutomatedReview(bot="coderabbitai")]


@pytest.mark.parametrize("key", ["webhook_event", "check_name", "comment_author", "command"])
def test_a_template_cannot_configure_how_automated_review_is_read(key):
    """`automated-review-implementation-is-not-template-configuration`: a
    transport detail on the task, or on its wait, is refused at load -- the
    forge backend owns it."""
    from pydantic import ValidationError

    task = {"id": "review", "kind": "forge", "target": "mr.automated_review"}
    for authored in ({**task, key: "x"}, {**task, "wait": {key: "x"}}):
        with pytest.raises(ValidationError, match=key):
            v1_resolved([{"id": "feedback", "kind": "exec", "tasks": [authored]}])


async def test_a_reviewer_error_stops_for_a_human_and_spends_no_repair(walk, item_on):
    """Kraft-sm2r2, Ruling 170: a reviewer that errored said nothing about the
    code, so -- unlike actionable feedback, which the test above repairs -- it
    launches no repair and no fix cycle, and the stop names it."""
    node = forge_node("feedback", "mr.automated_review")
    node["on_failure"] = {"tasks": [{"id": "repair", "kind": "subprocess", "command": "true"}]}
    node["fix_loop"] = {"tasks": [{"id": "fix", "kind": "subprocess", "command": "true"}]}
    fake = forge.FakeForge(
        review_results=[forge.ReviewResult("error", detail="reviewer bot crashed")]
    )
    it = await item_on([forge_node("open", "mr.open_draft"), node])

    assert await walk(fake, it) == "needs_human"

    assert [s["hook_point"] for s in it.sessions("feedback")] == ["feedback.main.feedback"]
    assert not it.events("fix_cycle_started")
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert "automated reviewer errored" in reason and "reviewer bot crashed" in reason


# --- policy bounds the wait (Kraft-5p69g) --------------------------------------


def _materialize(nodes, repo, maxima: dict):
    from kraft.templates.environment import WorkItemTarget

    return v1_resolved(nodes).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(
            InstancePolicyInput.model_validate({"maxima": maxima})
        ),
    )


def test_a_wait_over_the_administrator_maximum_is_refused_when_the_item_is_filed(repo):
    """`external-wait-has-configurable-timeout-and-polling`: "subject to
    applicable policy limits". Refused at filing, naming the task, never
    mid-run."""
    with pytest.raises(PolicyError, match=r"ci\.main\.ci.*wait_timeout_minutes 60"):
        _materialize(
            [forge_node("ci", "mr.ci", wait=_wait(timeout="2h"))],
            repo,
            {"wait_timeout_minutes": 60},
        )


@pytest.mark.parametrize(
    "wait, maxima, expected",
    [
        # Authored values win, within the maximum.
        (
            _wait(timeout="45m", initial="10s", maximum="1m"),
            {"wait_timeout_minutes": 60},
            (2700, 10, 60),
        ),
        # Nothing authored: the seed default, 90 minutes (Kraft-7xpv4).
        (None, {}, (5400, 30, 300)),
        # Nothing authored, under a lower maximum: the default is clamped to
        # it rather than refusing a chain whose author never chose a number.
        (None, {"wait_timeout_minutes": 20}, (1200, 30, 300)),
    ],
    ids=["authored", "seed-default", "default-clamped-to-maximum"],
)
def test_a_wait_resolves_its_bounds_through_the_task_policy(repo, wait, maxima, expected):
    task = {"wait": wait} if wait else {}
    chain = _materialize([forge_node("ci", "mr.ci", **task)], repo, maxima)
    (resolved,) = [t for n in chain.chain.nodes for t in n.tasks()]

    bounds = resolved.task.wait_bounds(chain.policy_for(resolved))

    assert (
        bounds.timeout.total_seconds(),
        bounds.initial_interval.total_seconds(),
        bounds.max_interval.total_seconds(),
    ) == expected


# --- the scheduler itself ------------------------------------------------------


def _parked_chain(repo):
    return v1_chain(
        [
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [{"id": "build", "kind": "subprocess", "command": "true"}],
            },
            {
                "id": "mr_checks",
                "kind": "exec",
                "tasks": [{"id": "poll", "kind": "subprocess", "command": "true"}],
            },
        ],
        repo=repo,
    )


async def _seed_waiting(app, repo, *, retry_at: str, node: str = "mr_checks") -> None:
    await v1_item(app.state.db, _parked_chain(repo), repo=str(repo))
    await app.state.db.write(lambda c: store.enter_node(c, "w1", node))
    await app.state.db.write(lambda c: store.mark_waiting(c, "w1", node, retry_at))


def _status(app, wid="w1"):
    return app.state.db.read(
        lambda c: c.execute("SELECT status FROM work_items WHERE id=?", (wid,)).fetchone()
    )["status"]


async def test_tick_ignores_a_not_yet_due_item(repo, scheduler):
    app = scheduler()
    await _seed_waiting(app, repo, retry_at="2999-01-01T00:00:00+00:00")

    assert await waits.tick(app) == []
    assert _status(app) == "waiting"


async def test_tick_re_enters_a_due_item_at_its_waiting_node(repo, scheduler):
    app = scheduler()
    await _seed_waiting(app, repo, retry_at="2000-01-01T00:00:00+00:00")

    assert await waits.tick(app) == ["w1"]
    await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)

    assert _status(app) == "completed"
    started = [
        e["payload"]["node_id"]
        for e in app.state.db.read(lambda c: events.read_after(c, 0, "w1"))
        if e["type"] == "node_started"
    ]
    assert "implementation" not in started, "the re-entry re-ran the node before its own"


async def test_tick_ignores_an_item_that_was_paused_while_waiting(repo, scheduler):
    """Kraft-tnak: pause clears retry_at and moves the row off 'waiting'."""
    app = scheduler()
    await _seed_waiting(app, repo, retry_at="2000-01-01T00:00:00+00:00")
    await app.state.db.write(lambda c: store.pause_work_item(c, "w1", []))

    assert await waits.tick(app) == []
    assert _status(app) == "paused"


async def test_tick_does_not_re_enter_a_row_its_own_previous_tick_already_claimed(repo, scheduler):
    """Kraft-ppk9: deliberately does NOT await the first tick's spawned walk
    before ticking again -- that gap is the race that used to fire twice."""
    app = scheduler()
    await _seed_waiting(app, repo, retry_at="2000-01-01T00:00:00+00:00")

    assert await waits.tick(app) == ["w1"]
    assert await waits.tick(app) == []
    assert _status(app) != "waiting"


async def test_a_reentry_resumes_at_the_waiting_step(repo, scheduler, monkeypatch):
    """A node whose waiting step was its fourth must not re-run the first
    three -- a paid agent session per tick on a node that opens the MR. The
    scheduler hands the walk no position: `walk.run_once` reads the item's
    cursor itself (tests/executor/test_entry_paths.py)."""
    seen = {}

    async def fake_run(*args, **kw):
        seen.update(kw)
        return "waiting"

    monkeypatch.setattr(executor, "run", fake_run)
    app = scheduler()
    await _seed_waiting(app, repo, retry_at="2000-01-01T00:00:00+00:00")
    await app.state.db.write(lambda c: store.set_current_step(c, "w1", 3))

    assert await waits.tick(app) == ["w1"]
    await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
    assert "start_index" not in seen and "start_step" not in seen
    assert seen["work_item_id"] == "w1"


async def test_a_v1_item_is_re_entered_rather_than_stranded_waiting(repo, scheduler, monkeypatch):
    """H1: `chain_definition` is `"{}"` on a V1 row, so the start index comes
    from `store.node_index`, never the legacy `chain["nodes"]`."""
    from kraft.api import deps as api_deps

    spawned: list[int] = []

    def spawn(app, wid, coro):
        spawned.append(1)
        api_deps.discard(coro)  # never run: close it, or it leaks unawaited

    app = scheduler()
    await _seed_waiting(app, repo, retry_at="2000-01-01T00:00:00+00:00")
    monkeypatch.setattr(api_deps, "spawn", spawn)

    await waits.tick(app)
    assert spawned, "the scheduler found no node index and left the item waiting"


async def test_a_node_the_chain_does_not_have_stops_the_item_rather_than_wedging_it(
    repo, scheduler
):
    """N1: the claim has flipped the row to `active` before the start index
    is read, and `tick` selects only `waiting` rows -- so an exit that leaves
    it `active` wedges it forever. The bracket turns that exit into a stop."""
    app = scheduler()
    await _seed_waiting(app, repo, retry_at="2000-01-01T00:00:00+00:00", node="gone")

    assert await waits.tick(app) == []
    assert _status(app) == "needs_human"
