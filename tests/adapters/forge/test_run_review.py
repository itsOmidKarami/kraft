"""`mr.automated_review` through the executor against a `FakeForge`: which
reviewers reach the task (`dispatch._reviewers`), and where a task may name
them. What each reviewer's read returns is test_automated_review.py."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from pydantic import ValidationError
from support.harness import commit_all, entry_of, fake_harness_home, v1_resolved, write

from kraft import executor, policy, store, waits
from kraft.adapters import forge
from kraft.automated_review import AutomatedReview, as_reviewers
from kraft.executor import dispatch

from .nodes import forge_node
from .test_run_chain import _multi_repo_item, _run_task

_BOT = {"bot": "coderabbitai"}
_TASK_BOT = {"bot": "task-bot"}
_ITEM_CHECK = {"check": "item-check"}


@pytest.mark.parametrize(
    "item, in_repo, task, asked",
    [
        (None, _BOT, None, [_BOT]),
        (None, [_BOT, {"check": "scan"}], None, [_BOT, {"check": "scan"}]),
        (None, None, [_TASK_BOT], [_TASK_BOT]),
        (None, _BOT, [_TASK_BOT], [_BOT]),
        # `[]` is a layer saying "none", not an unset one.
        (None, [], [_TASK_BOT], []),
        ([_ITEM_CHECK], _BOT, [_TASK_BOT], [_ITEM_CHECK]),
        (None, None, None, []),
    ],
    ids=[
        "repo",
        "repo-list",
        "task",
        "repo-over-task",
        "repo-says-none",
        "item-over-repo-and-task",
        "none-anywhere",
    ],
)
async def test_the_nearest_layer_s_reviewers_reach_the_review_task(
    item_on, database, run_dirs, monkeypatch, item, in_repo, task, asked
):
    """Ruling 171: which reviewers is the item's node override's, else the
    repository's (`repos.yaml`), else the review task's own -- handed to the
    backend at dispatch."""
    fake = forge.FakeForge()
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    node = forge_node(
        "review", "mr.automated_review", **({} if task is None else {"reviewers": task})
    )
    it = await item_on([node])
    if item is not None:
        await database.write(
            lambda c: store.set_node_overrides(c, it.id, {"review": {"reviewers": item}})
        )
    entry = entry_of(
        {
            "setup_command": "",
            "forge": "github",
            **({} if in_repo is None else {"automated_review": in_repo}),
        }
    )

    await executor.run(
        database,
        run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(repo_entry=entry),
    )

    assert fake.reviewers_asked == [as_reviewers(asked)]


@pytest.mark.parametrize(
    "target, fields, refused",
    [
        ("mr.automated_review", {"reviewers": [{"bot": "b"}]}, False),
        ("mr.ci", {"reviewers": [{"bot": "b"}]}, True),
        ("mr.automated_review", {"reviewers": [{"login": "b"}]}, True),
        ("mr.automated_review", {"wait": {"grace_period": "30s"}}, False),
        ("mr.ci", {"wait": {"grace_period": "30s"}}, True),
    ],
    ids=[
        "the-review",
        "another-wait",
        "malformed-entry",
        "grace-period-on-the-review",
        "grace-period-on-another-wait",
    ],
)
def test_only_the_review_task_names_reviewers(target, fields, refused):
    """The chain may name *which* reviewers, and how long a bot must stay quiet,
    on the one task that waits for them; any other forge task naming either, or
    a malformed entry, is refused at load."""
    task = {"id": "t", "kind": "forge", "target": target, **fields}
    nodes = [{"id": "feedback", "kind": "exec", "tasks": [task]}]
    if refused:
        with pytest.raises(ValidationError):
            v1_resolved(nodes)
    else:
        v1_resolved(nodes)


# --- the collect path ------------------------------------------------------------

_A, _B = AutomatedReview(bot="a"), AutomatedReview(bot="b")


def _feedback(reviewer, active=None, *, threads=()):
    """What `reviewer` has said, its newest activity `active` seconds after the
    clock's start (the item's head is dated there)."""
    from datetime import UTC, datetime

    at = None if active is None else datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=active)
    return forge.ReviewerFeedback(
        reviewer,
        at,
        threads=tuple({"id": t, "file": "calc.py", "line": 1, "body": f"fix {t}"} for t in threads),
    )


def _review_node(minutes, *, reads=True, reviewers=(_A, _B)):
    """One node: the review wait, and (when `reads`) a task that reads what it
    collected, in the step after."""
    wait = {
        "id": "review",
        "kind": "forge",
        "target": "mr.automated_review",
        "reviewers": [r.model_dump(exclude_none=True) for r in reviewers],
        "policy": {"total_time_cap_minutes": minutes},
        "wait": {"polling": {"initial_interval": "30s", "max_interval": "2m"}},
    }
    steps = [{"id": "settle", "tasks": [wait]}]
    if reads:
        triage = {
            "id": "triage",
            "kind": "agent",
            "harness": "fake",
            "prompt": "Decide.",
            "inputs": ["bot_feedback"],
        }
        steps.append({"id": "decide", "tasks": [triage]})
    return {"id": "feedback", "kind": "exec", "steps": steps}


@pytest.fixture
def rig(item_on, database, run_dirs, monkeypatch, wait_clock, tmp_path):
    """`await rig(node, fake)` files the node as an item; `await rig.walk()`
    walks it from its own cursor with the clock where `rig.clock` put it.
    `rig.triaged` lists the tasks the walk launched an agent for."""

    class Rig:
        triaged: list[str] = []

        async def __call__(self, node, fake):
            monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
            # A harness for the reading task to select; what it launches is
            # replaced below, since only whether it is reached is asked.
            monkeypatch.setenv("KRAFT_HOME", str(fake_harness_home(tmp_path, ["true"])))
            self.triaged = []

            async def launched(*_, task, **__):
                self.triaged.append(task.path)
                return "done", True

            monkeypatch.setattr(dispatch, "_launch_agent", launched)
            self.item = await item_on([node])
            return self

        def moment(self, seconds):
            return wait_clock.start + timedelta(seconds=seconds)

        def clock(self, seconds):
            wait_clock.now = self.moment(seconds)

        async def walk(self):
            if self.item.status() == "waiting":
                await database.write(lambda c: store.mark_reentered(c, self.item.id))
            return await executor.run(
                database,
                run_dirs,
                work_item_id=self.item.id,
                policy=policy.Policy(
                    loops={},
                    default=policy.Cap(attempts=3, wall_clock_s=3600),
                    auto_escalate_stuck=False,
                ),
                launch=executor.LaunchContext(
                    repo_entry=entry_of({"setup_command": "", "forge": "github"})
                ),
            )

        def wait_session(self) -> dict:
            [wait] = [
                s
                for s in self.item.sessions("feedback")
                if s["hook_point"] == "feedback.settle.review"
            ]
            return wait

        def collection(self) -> dict | None:
            path = self.item.files(self.wait_session()["id"]).folder / "bot_feedback.json"
            return json.loads(path.read_text()) if path.exists() else None

    return Rig()


@pytest.mark.parametrize(
    "minutes, plan, ended, finished, threads",
    [
        # Both bots are quiet for the grace period after their last activity.
        (
            30,
            [(10, None), (63, None)],
            "settled",
            ["activity", "activity"],
            {"a": ["a1"], "b": ["b1"]},
        ),
        # Neither ever posts: the wait's deadline settles it.
        (1, [(0, None), (61, None)], "timed_out", ["timeout", "timeout"], {"a": [], "b": []}),
        # The deadline falls between Kraft's own reads of the clock: the decision
        # and the wait's record must agree on the one instant it was made at.
        (1, [(0, None), (0, 61)], "timed_out", ["timeout", "timeout"], {"a": [], "b": []}),
    ],
    ids=["both-settle", "silent-until-timeout", "deadline-crossed-between-reads"],
)
async def test_the_collect_path_settles_then_hands_on_the_collection(
    rig, monkeypatch, minutes, plan, ended, finished, threads
):
    quiet = ended == "settled"
    fake = forge.FakeForge(
        feedback_script=[
            [
                _feedback(_A, 1 if quiet else None, threads=threads["a"]),
                _feedback(_B, 2 if quiet else None, threads=threads["b"]),
            ]
        ]
    )
    await rig(_review_node(minutes), fake)

    for clock, _ in plan[:-1]:
        rig.clock(clock)
        assert await rig.walk() == "waiting"
        assert rig.triaged == []
    clock, decided_at = plan[-1]
    rig.clock(clock)
    if decided_at is not None:
        monkeypatch.setattr(waits, "now", lambda: rig.moment(decided_at))
    status = await rig.walk()

    assert status == "completed", "a silent bot is nothing to stop a person for"
    assert rig.wait_session()["status"] == "done"
    assert [e["payload"]["outcome"] for e in rig.item.events("external_wait_ended")] == [ended]
    got = rig.collection()
    assert [(r["reviewer"], r["finished"]) for r in got["reviewers"]] == list(
        zip(["bot:a", "bot:b"], finished, strict=True)
    )
    assert {r["reviewer"][4:]: [t["id"] for t in r["threads"]] for r in got["reviewers"]} == threads
    # Bots with threads open are read; silent ones leave nothing for the triage to
    # read, so no agent is started for it.
    assert rig.triaged == (["feedback.decide.triage"] if quiet else [])


async def test_the_next_check_comes_when_a_bots_grace_period_ends_not_at_the_backoff(rig):
    await rig(
        _review_node(30), forge.FakeForge(feedback_script=[[_feedback(_A, 1), _feedback(_B, 2)]])
    )

    rig.clock(10)
    assert await rig.walk() == "waiting"

    # a was last active at 1s, so its 60s of quiet ends at 61s: the 30s backoff
    # would have looked again at 40s.
    assert rig.item.row()["retry_at"] == rig.moment(61).isoformat()


async def test_no_reviewer_named_settles_with_an_empty_collection(rig):
    await rig(_review_node(30, reviewers=()), forge.FakeForge())

    assert await rig.walk() == "completed"

    assert len(rig.item.events("automated_review_not_configured")) == 1
    assert rig.collection()["reviewers"] == []
    assert rig.triaged == [], "nothing was collected, so nothing is triaged"


@pytest.mark.parametrize(
    "result, advance, status",
    [
        # Nothing in the node reads a collection: feedback fails the wait now.
        (forge.ReviewResult("actionable", findings=("calc.py:1: fix a1",)), 0, "failed"),
        # And a silence is still a timeout that stops the item for a person.
        (forge.ReviewResult("pending"), 61, "capped_out"),
    ],
    ids=["no-reader-feedback-fails-now", "no-reader-timeout-stops"],
)
async def test_a_node_nothing_reads_takes_the_direct_path(
    item_on, database, run_dirs, monkeypatch, wait_clock, result, advance, status
):
    it = await item_on([_review_node(1, reads=False)])
    fake = forge.FakeForge(review_results=[result])
    ask = {
        "handler": "automated_review",
        "reviewers": (_A,),
        "reads_feedback": False,
        "wait": forge.WaitBounds.from_seconds(timeout=60, initial=30, maximum=120),
    }

    got = await _run_task(database, run_dirs, it, it.worktree, "kraft/w1", fake, monkeypatch, **ask)
    if advance:
        assert got == "waiting"
        wait_clock.advance(advance)
        got = await _run_task(
            database, run_dirs, it, it.worktree, "kraft/w1", fake, monkeypatch, **ask
        )

    assert got == status
    assert not list(run_dirs.base.rglob("bot_feedback.json"))


async def test_a_multi_repo_item_takes_the_direct_path_whatever_its_node_reads(
    item_on, database, run_dirs, tmp_path, monkeypatch
):
    """One triage across several repositories' feedback is follow-up work, so
    such an item keeps failing the wait on feedback, and nothing is collected
    and then dropped."""
    it, worktree, branch = await _multi_repo_item(item_on, database, run_dirs, tmp_path, "ignore")
    write(worktree / "repos" / "pkg", "new.txt", "x\n")
    commit_all(worktree / "repos" / "pkg")
    fake = forge.FakeForge(
        review_results=[forge.ReviewResult("actionable", findings=("calc.py:1: fix a1",))]
    )

    status = await _run_task(
        database, run_dirs, it, worktree, branch, fake, monkeypatch,
        handler="automated_review", reviewers=(_A,), reads_feedback=True,
    )  # fmt: skip

    assert status == "failed"
    assert fake.reviewers_asked == [(_A,)]
    assert not list(run_dirs.base.rglob("bot_feedback.json"))
