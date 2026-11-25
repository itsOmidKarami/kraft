"""A forge call the network cut off (`waits.note_no_answer`): the item parks and
tries again on the wait scheduler, launches nothing, and stops for a person only
after its tries run out. What counts as "the network" is test_models.py."""

from __future__ import annotations

import asyncio
import shlex
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from test_waits import ON_A_FORGE, forge_node, walk  # noqa: F401

from kraft import events, executor, waits
from kraft.adapters import forge

DROPPED = forge.ForgeError(
    "git push failed: Connection to github.com closed by remote host.", no_answer=True
)
TASK = "feedback.ci.await_ci"


def _feedback_node() -> dict:
    """A finished step, then a CI wait, with both of the node's repair controls."""
    edit = shlex.join([sys.executable, "-c", "open('left.txt', 'a').write('x')"])
    repair = {
        "steps": [{"id": "r", "tasks": [{"id": "fix", "kind": "subprocess", "command": edit}]}]
    }
    return {
        "id": "feedback",
        "kind": "exec",
        "steps": [
            {"id": "first", "tasks": [{"id": "work", "kind": "subprocess", "command": "true"}]},
            {"id": "ci", "tasks": [{"id": "await_ci", "kind": "forge", "target": "mr.ci"}]},
        ],
        "on_failure": repair,
        "fix_loop": repair,
    }


def _hooks(it) -> list[str]:
    return [s["hook_point"] for s in it.sessions("feedback")]


async def test_a_dropped_connection_parks_launches_nothing_and_recovers(
    walk,  # noqa: F811
    item_on,
    wait_clock,
):
    """The incident: no `on_failure` repair, no fix cycle. The finished step stays
    finished, the wait keeps its clock, and the next look carries on."""
    fake = forge.FakeForge(ci_states=["pending", "success"])
    real = fake.ci_status
    it = await item_on([forge_node("open", "mr.open_draft"), _feedback_node()])
    assert await walk(fake, it) == "waiting"
    deadline = it.events("external_wait_started")[-1]["payload"]["deadline"]

    fake.ci_status = AsyncMock(side_effect=DROPPED)
    wait_clock.advance(30)
    assert await walk(fake, it) == "waiting"

    assert it.row()["retry_at"] == wait_clock.at(60), "30s from this failure"
    assert not it.events("fix_cycle_started") and not it.events("node_recovery_started")
    assert not [h for h in _hooks(it) if ".on_failure." in h or ".fix_loop." in h]
    assert not [e for e in it.events("external_wait_ended") if e["payload"]["task"] == TASK]
    (noted,) = it.events("forge_no_answer")
    assert noted["payload"]["attempt"] == 1
    assert "network" in it.events("work_item_waiting")[-1]["payload"]["reason"]

    fake.ci_status = real
    wait_clock.advance(30)
    assert await walk(fake, it) == "completed"

    assert _hooks(it).count("feedback.first.work") == 1, "the finished step ran again"
    assert not it.events("fix_cycle_started")
    assert it.events("forge_answered")[-1]["payload"] == {
        "node_id": "feedback",
        "task": TASK,
        "attempts": 1,
    }
    started = [e for e in it.events("external_wait_started") if e["payload"]["task"] == TASK]
    assert [e["payload"]["deadline"] for e in started] == [deadline], "the wait's clock restarted"


@pytest.mark.parametrize("control", ["on_failure", "fix_loop"])
async def test_a_drop_in_a_repairs_sync_does_not_run_the_repair_again(
    walk,  # noqa: F811
    item_on,
    wait_clock,
    control,
):
    """Two of the incident's three failures were here: the repair's own sync."""
    edit = shlex.join([sys.executable, "-c", "open('left.txt', 'a').write('x')"])
    review = {"id": "await_review", "kind": "forge", "target": "mr.automated_review"}
    node = {
        "id": "feedback",
        "kind": "exec",
        "steps": [{"id": "automated_review", "tasks": [review]}],
        control: {
            "steps": [
                {"id": "repair", "tasks": [{"id": "fix", "kind": "subprocess", "command": edit}]},
                {"id": "sync", "tasks": [{"id": "sync", "kind": "forge", "target": "mr.sync"}]},
            ]
        },
    }
    fake = forge.FakeForge(
        review_results=[
            forge.ReviewResult("actionable", findings=("rename `x`",)),
            forge.ReviewResult("clean"),
        ]
    )
    dropped, real_push = [], fake.push

    async def drop_the_repairs_push(**kw):
        if (kw["repo"] / "left.txt").exists() and not dropped:  # the repair has run
            dropped.append(kw)
            raise DROPPED
        await real_push(**kw)

    fake.push = drop_the_repairs_push
    it = await item_on([forge_node("open", "mr.open_draft"), node])

    assert await walk(fake, it) == "waiting"
    wait_clock.now = datetime.fromisoformat(it.row()["retry_at"])
    assert await walk(fake, it) == "completed"

    assert _hooks(it).count(f"feedback.{control}.repair.fix") == 1, "the repair ran again"
    assert len(it.events("fix_cycle_started")) == (control == "fix_loop")
    assert it.events("forge_no_answer")[0]["payload"]["task"] == f"feedback.{control}.sync.sync"


async def test_a_merge_that_landed_but_lost_its_answer_is_not_asked_again(
    walk,  # noqa: F811
    item_on,
    wait_clock,
):
    fake = forge.FakeForge(ci_states=["success"])
    asked, real_merge = [], fake.merge

    async def lands_then_drops(**kw):
        asked.append(kw)
        await real_merge(**kw)
        raise DROPPED

    fake.merge = lands_then_drops
    it = await item_on([forge_node("open", "mr.open_draft"), forge_node("land", "mr.merge")])

    assert await walk(fake, it) == "waiting"
    wait_clock.now = datetime.fromisoformat(it.row()["retry_at"])
    assert await walk(fake, it) == "completed"

    assert len(asked) == 1


@pytest.mark.parametrize(
    "error, answered",
    [
        (forge.CliUnavailable("gh is not installed or not on PATH"), False),
        (forge.git.UnsafeWorktree("HEAD is off the branch"), False),
        (forge.ForgeError("Validation failed (HTTP 422)", status=422), True),
    ],
    ids=["cli-gone", "unsafe-worktree", "the-forge-answered"],
)
async def test_only_an_answer_from_the_forge_ends_the_count_of_tries(
    walk,  # noqa: F811
    item_on,
    wait_clock,
    error,
    answered,
):
    fake = forge.FakeForge()
    fake.ci_status = AsyncMock(side_effect=DROPPED)
    it = await item_on([forge_node("ci", "mr.ci")])
    assert await walk(fake, it) == "waiting"

    fake.ci_status = AsyncMock(side_effect=error)
    wait_clock.now = datetime.fromisoformat(it.row()["retry_at"])
    await walk(fake, it)

    assert bool(it.events("forge_answered")) is answered
    kept = it.database.read(lambda c: waits._last_no_answer(c, it.id, "ci.main.ci"))
    assert (kept is None) is answered


async def test_a_local_refusal_on_the_next_try_keeps_the_count(
    walk,  # noqa: F811
    item_on,
    wait_clock,
    monkeypatch,
):
    """The branch came up empty: `config_error` with no call to the forge at all."""
    ahead = AsyncMock(return_value=1)
    monkeypatch.setattr(forge.git, "commits_ahead", ahead)
    fake = forge.FakeForge()
    fake.find_mr = AsyncMock(side_effect=DROPPED)
    it = await item_on([forge_node("open", "mr.open_draft")])
    assert await walk(fake, it) == "waiting"

    ahead.return_value = 0
    wait_clock.now = datetime.fromisoformat(it.row()["retry_at"])
    assert await walk(fake, it) == "needs_human"

    assert not it.events("forge_answered")
    kept = it.database.read(lambda c: waits._last_no_answer(c, it.id, "open.main.open"))
    assert kept["attempt"] == 1


@pytest.mark.parametrize(
    "answered_before, tries, names",
    [
        ("this-item", waits.NO_ANSWER_TRIES, "could not reach the forge"),
        ("another-item", waits.NO_ANSWER_TRIES, "could not reach the forge"),
        ("recovered", waits.NO_ANSWER_TRIES, "could not reach the forge"),
        ("never", waits.FIRST_CONTACT_TRIES, "api_url"),
    ],
    ids=[
        "opened-an-mr",
        "the-repo-answered-another-item",
        "the-repo-recovered-from-a-park",
        "a-forge-that-never-answered",
    ],
)
async def test_the_limit_stops_for_a_person_naming_the_network_and_a_retry_starts_over(
    walk,  # noqa: F811
    item_on,
    wait_clock,
    answered_before,
    tries,
    names,
):
    """A forge that has never answered this repo is a setup mistake until proven
    otherwise (Kraft-675vy): it stops after a few tries, not ten."""
    fake = forge.FakeForge()
    fake.ci_status = AsyncMock(side_effect=DROPPED)
    opened = [forge_node("open", "mr.open_draft")] if answered_before == "this-item" else []
    it = await item_on([*opened, _feedback_node()])
    if answered_before in ("another-item", "recovered"):
        other = await item_on([forge_node("open", "mr.open_draft")], wid="w2")
        kind, said = (
            ("mr_opened", {"number": 1, "url": "u"})
            if answered_before == "another-item"
            else ("forge_answered", {"node_id": "open", "task": "open.main.open", "attempts": 1})
        )
        await it.database.write(lambda c: events.append(c, other.id, kind, said))

    for _ in range(tries):
        assert await walk(fake, it) == "waiting"
        assert f"of {tries})" in it.events("work_item_waiting")[-1]["payload"]["reason"]
        wait_clock.now = datetime.fromisoformat(it.row()["retry_at"])
    assert await walk(fake, it) == "needs_human"

    stop = it.events("work_item_needs_human")[-1]["payload"]
    assert names in stop["reason"] and "closed by remote" in stop["reason"]
    assert stop["facts"]["cause"] == "forge_unreachable"
    assert not it.events("fix_cycle_started") and not it.events("node_recovery_started")
    assert it.events("external_wait_ended")[-1]["payload"]["outcome"] == "error"

    await it.database.write(lambda c: events.append(c, it.id, "work_item_retried", {}))
    assert await it.database.write(
        lambda c: waits.note_no_answer(
            c, it.id, node_id="feedback", task=TASK, error="x", started=wait_clock.now
        )
    )
    assert it.events("forge_no_answer")[-1]["payload"]["attempt"] == 1


@pytest.mark.parametrize("clears", [True, False], ids=["lands-on-a-later-ask", "never-clears"])
async def test_a_merge_the_forge_is_still_making_is_read_again_and_asked_again(
    walk,  # noqa: F811
    item_on,
    wait_clock,
    clears,
):
    """A forge "already merging" is a lock, not a promise (Kraft-l1hbl): the item parks, the
    retry reads the state and asks again, and a lock that never clears stops for a
    person naming it, not the network."""
    fake = forge.FakeForge(ci_states=["success"])
    asked, real_merge = [], fake.merge

    async def busy_then_free(**kw):
        asked.append(kw)
        if len(asked) == 1 or not clears:
            raise forge.MergeBusy("Merge already in progress")
        await real_merge(**kw)

    fake.merge = busy_then_free
    it = await item_on([forge_node("open", "mr.open_draft"), forge_node("land", "mr.merge")])

    assert await walk(fake, it) == "waiting"
    assert "finish a merge" in it.events("work_item_waiting")[-1]["payload"]["reason"]
    tries = 1 if clears else waits.NO_ANSWER_TRIES
    for _ in range(tries):
        wait_clock.now = datetime.fromisoformat(it.row()["retry_at"])
        status = await walk(fake, it)
    assert status == ("completed" if clears else "needs_human")

    assert len(asked) == 1 + tries
    assert not it.events("fix_cycle_started") and not it.events("node_recovery_started")
    if not clears:
        stop = it.events("work_item_needs_human")[-1]["payload"]
        assert "still merging" in stop["reason"] and "network" not in stop["reason"]


@pytest.mark.parametrize(
    "started_after_due, attempt",
    [(5, 2), (3 * 3600, 1)],
    ids=["on-time-spends-a-try", "a-machine-that-slept-spends-none"],
)
async def test_a_try_is_spent_only_when_it_ran_about_when_due(
    item_on, wait_clock, started_after_due, attempt
):
    it = await item_on([forge_node("ci", "mr.ci")])

    def note(started):
        return it.database.write(
            lambda c: waits.note_no_answer(
                c, it.id, node_id="ci", task="ci.main.ci", error="x", started=started
            )
        )

    await note(wait_clock.now)
    due = datetime.fromisoformat(it.events("forge_no_answer")[-1]["payload"]["retry_at"])
    started = due + timedelta(seconds=started_after_due)
    # The call itself ran out its two-minute timeout: when it began is what counts.
    wait_clock.now = started + timedelta(seconds=120)
    await note(started)

    last = it.events("forge_no_answer")[-1]["payload"]
    assert last["attempt"] == attempt
    delay = 30 * 2 ** (attempt - 1)
    assert last["retry_at"] == (wait_clock.now + timedelta(seconds=delay)).isoformat()


async def test_a_parked_item_survives_a_restart(item_on, database, run_dirs, stub_app, monkeypatch):
    """A new server over the same database: the wait scheduler finds the park and
    the walk completes, with no steer and no restart of the run."""
    from kraft.api import deps

    fake = forge.FakeForge(ci_states=["success"])
    real, fake.ci_status = fake.ci_status, AsyncMock(side_effect=DROPPED)
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    it = await item_on([forge_node("ci", "mr.ci")])
    launch = executor.LaunchContext(repo_entry=ON_A_FORGE)
    assert await executor.run(database, run_dirs, work_item_id=it.id, launch=launch) == "waiting"

    fake.ci_status = real
    monkeypatch.setattr(deps, "launch", lambda st, repo: launch)
    app = stub_app(policy=None)
    assert await waits.tick(app, now="2999-01-01T00:00:00+00:00") == [it.id]
    await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)

    assert it.status() == "completed"
    assert not it.events("work_item_retried")
