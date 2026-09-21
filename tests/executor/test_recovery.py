"""Recovery by scope: the nearest handler below a failed task -- its own, its
step's, or the node's -- runs once the step has settled, and exactly that
scope is retried. The node-level repair's own prompt and findings are
`test_walk.py`'s; this is which handler runs, when, and what it retries."""

import asyncio
import shlex

import pytest

from kraft import executor
from kraft import policy as _policy
from kraft.executor import dispatch

NO_SETUP = {"setup_command": ""}


def _sub(task_id, **fields):
    return {"id": task_id, "kind": "subprocess", "command": shlex.join(["true"]), **fields}


def _fix(task_id="fix"):
    return {"tasks": [_sub(task_id)]}


class Script:
    """`dispatch_node`, scripted per task id. `plan[id]` is the statuses that
    task answers in turn (the last one repeats; `done` when unplanned);
    `log` is every `("start"|"end", id)` in the order it happened, and
    `steers[id]` the steer each launch was handed."""

    def __init__(self):
        self.plan: dict[str, list[str]] = {}
        self.delay: dict[str, float] = {}
        self.log: list[tuple[str, str]] = []
        self.steers: dict[str, list] = {}

    @property
    def calls(self) -> list[str]:
        return [task for event, task in self.log if event == "start"]

    def index(self, event: str, task: str, nth: int = 0) -> int:
        return [i for i, e in enumerate(self.log) if e == (event, task)][nth]

    async def __call__(self, db_, run_dirs_, task, node, row_, wt, **kw):
        tid = task.task.id
        self.log.append(("start", tid))
        self.steers.setdefault(tid, []).append(kw.get("steer"))
        await asyncio.sleep(self.delay.get(tid, 0))
        seq = self.plan.get(tid, ["done"])
        status = seq.pop(0) if len(seq) > 1 else seq[0]
        self.log.append(("end", tid))
        return status


@pytest.fixture
def script(monkeypatch) -> Script:
    spy = Script()
    monkeypatch.setattr(dispatch, "dispatch_node", spy)
    return spy


def _walk(it, **kwargs):
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        registry=None,
        launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
        **kwargs,
    )


def _node(steps, **fields):
    return {"id": "build", "kind": "exec", "steps": steps, **fields}


async def test_a_task_recovery_retries_only_the_failed_task(item_on, script):
    """`task-recovery-retries-only-the-task`: its passing co-task is not rerun."""
    script.plan = {"a": ["failed", "done"]}
    it = await item_on(
        [_node([{"id": "check", "tasks": [_sub("a", on_failure=_fix()), _sub("b")]}])]
    )

    assert await _walk(it) == "completed"
    assert sorted(script.calls[:2]) == ["a", "b"]
    assert script.calls[2:] == ["fix", "a"]
    [started] = it.events("node_recovery_started")
    assert started["payload"] == {
        "node_id": "build",
        "scope": "task",
        "failed_tasks": ["build.check.a"],
        "tasks": ["build.check.a.on_failure.main.fix"],
    }


async def test_a_step_recovery_reruns_every_task_in_the_step_and_no_earlier_step(item_on, script):
    """`step-recovery-retries-the-entire-step`."""
    script.plan = {"a": ["failed", "done"]}
    it = await item_on(
        [
            _node(
                [
                    {"id": "prep", "tasks": [_sub("prep")]},
                    {"id": "check", "tasks": [_sub("a"), _sub("b")], "on_failure": _fix()},
                ]
            )
        ]
    )

    assert await _walk(it) == "completed"
    assert script.calls[0] == "prep"
    assert script.calls[3] == "fix"
    assert sorted(script.calls[1:3]) == sorted(script.calls[4:]) == ["a", "b"]
    assert script.calls.count("prep") == 1


async def test_a_node_recovery_reruns_the_node_from_its_first_step(item_on, script):
    """`node-recovery-retries-the-entire-node`."""
    script.plan = {"a": ["failed", "done"]}
    it = await item_on(
        [
            _node(
                [{"id": "prep", "tasks": [_sub("prep")]}, {"id": "check", "tasks": [_sub("a")]}],
                on_failure=_fix(),
            )
        ]
    )

    assert await _walk(it) == "completed"
    assert script.calls == ["prep", "a", "fix", "prep", "a"]


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        (("task", "step", "node"), "task_fix"),
        (("step", "node"), "step_fix"),
        (("node",), "node_fix"),
    ],
    ids=["task-wins", "step-wins", "node-only"],
)
async def test_the_nearest_handler_wins_and_only_it_runs(item_on, script, declared, expected):
    """`nearest-recovery-handler-wins`: at most one handler per failure, task
    before step before node -- and a farther one never runs after the nearest
    one did not take."""
    script.plan = {"a": ["failed"]}
    task = _sub("a", on_failure=_fix("task_fix")) if "task" in declared else _sub("a")
    step = {"id": "check", "tasks": [task]}
    if "step" in declared:
        step["on_failure"] = _fix("step_fix")
    fields = {"on_failure": _fix("node_fix")} if "node" in declared else {}
    it = await item_on([_node([step], **fields)])

    assert await _walk(it) == "needs_human"
    handlers = [c for c in script.calls if c.endswith("_fix")]
    assert handlers == [expected]
    assert "(after on_failure)" in it.events("work_item_needs_human")[-1]["payload"]["reason"]


async def test_a_concurrent_step_settles_before_recovery_begins(item_on, script):
    """`parallel-step-settles-before-recovery`: the slow co-task finishes
    before the step's handler starts, and the later step never starts before
    it."""
    script.plan = {"fast": ["failed", "done"]}
    script.delay = {"slow": 0.2}
    it = await item_on(
        [
            _node(
                [
                    {"id": "check", "tasks": [_sub("fast"), _sub("slow")], "on_failure": _fix()},
                    {"id": "later", "tasks": [_sub("later")]},
                ]
            )
        ]
    )

    assert await _walk(it) == "completed"
    assert script.index("end", "slow") < script.index("start", "fix")
    assert script.index("end", "fix") < script.index("start", "later")


async def test_sibling_task_recoveries_run_one_at_a_time_after_the_step_settles(item_on, script):
    """`recovery-tasks-run-after-a-concurrent-step-settles`."""
    script.plan = {"a": ["failed", "done"], "b": ["failed", "done"]}
    script.delay = {"fix_a": 0.1, "fix_b": 0.1}
    it = await item_on(
        [
            _node(
                [
                    {
                        "id": "check",
                        "tasks": [
                            _sub("a", on_failure=_fix("fix_a")),
                            _sub("b", on_failure=_fix("fix_b")),
                        ],
                    }
                ]
            )
        ]
    )

    assert await _walk(it) == "completed"
    settled = max(script.index("end", "a"), script.index("end", "b"))
    assert settled < script.index("start", "fix_a") < script.index("end", "fix_a")
    assert script.index("end", "fix_a") < script.index("start", "fix_b")


@pytest.mark.parametrize("with_fix_loop", [True, False], ids=["fix-loop", "no-fix-loop"])
async def test_a_recovery_that_does_not_take_enters_the_fix_loop_or_stops(
    item_on, script, tmp_path, with_fix_loop
):
    """`failed-recovery-enters-node-fix-loop-or-needs-human`, and the stop
    names the task's original failure."""
    script.plan = {"a": ["failed"], "repair": ["done"]}
    fields = {"fix_loop": {"tasks": [_sub("repair")], "max_attempts": 1}} if with_fix_loop else {}
    it = await item_on(
        [_node([{"id": "check", "tasks": [_sub("a", on_failure=_fix())]}], **fields)]
    )
    policy = _policy.Policy(loops={}, default=_policy.Cap(3, 3600))

    assert await _walk(it, policy=policy) == "needs_human"
    assert script.calls[:3] == ["a", "fix", "a"]
    assert ("repair" in script.calls) is with_fix_loop
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert ("fix_loop exhausted" in reason) is with_fix_loop
    if not with_fix_loop:
        assert reason.startswith("task failed in node build: a [subprocess]")


@pytest.mark.parametrize(
    "stop", ["paused", dispatch.BUDGET, dispatch.RATE_LIMITED, "needs_context"]
)
async def test_a_stop_or_a_question_spends_no_recovery(item_on, script, stop):
    """Only a failure is evidence a handler can act on: a pause, a budget
    refusal or a rate limit is not about the task, and a question is
    addressed to a human."""
    script.plan = {"a": [stop]}
    it = await item_on(
        [_node([{"id": "check", "tasks": [_sub("a", on_failure=_fix())]}], on_failure=_fix("n"))]
    )

    await _walk(it)
    assert script.calls == ["a"]


async def test_a_task_recovery_is_told_the_failure_it_repairs(item_on, script):
    script.plan = {"a": ["failed", "done"]}
    it = await item_on([_node([{"id": "check", "tasks": [_sub("a", on_failure=_fix())]}])])

    await _walk(it)
    [steer] = script.steers["fix"]
    assert steer.source == "seeded"
    note = steer.take()
    assert "The task a ended failed" in note
    assert "re-measured" in note
