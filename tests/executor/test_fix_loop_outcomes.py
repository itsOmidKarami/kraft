"""What a fix-loop repair's outcome spends (Kraft-jdkoq): only a genuine
repair attempt costs one of the loop's attempts. A repair that never ran,
or whose own plumbing failed, stops under its own named cause instead of
spending a cycle and stopping as "stuck". And after a `/retry`, a repair is
dispatched again rather than reused from before it (Kraft-znsvg)."""

import shlex
import sys

import pytest

from kraft import executor, store
from kraft import policy as _policy
from kraft.executor import walk

NO_SETUP = {"setup_command": ""}
KEY = "build.fix_loop"


def _sub(task_id, argv=("true",)):
    return {"id": task_id, "kind": "subprocess", "command": shlex.join(argv)}


def _fails_until(marker, passes_on: int):
    """A command that fails until it has run `passes_on` times."""
    code = (
        f"import pathlib,sys; m=pathlib.Path({str(marker)!r}); "
        "n=int(m.read_text()) if m.exists() else 0; m.write_text(str(n+1)); "
        f"sys.exit(0 if n+1 >= {passes_on} else 1)"
    )
    return [sys.executable, "-c", code]


def _node(check, fix_steps, attempts=3):
    return [
        {
            "id": "build",
            "kind": "exec",
            "tasks": [check],
            "fix_loop": {"steps": fix_steps, "max_attempts": attempts},
        }
    ]


def _policy_():
    return _policy.Policy(loops={}, default=_policy.Cap(9, 3600))


def _walk_node(it):
    return walk.walk_node(
        it.database,
        it.run_dirs,
        it.id,
        it.chain.chain.nodes[0],
        it.row(),
        it.repo,
        policy=_policy_(),
        launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
    )


def _counter(it):
    return it.database.read(lambda c: store.read_counter(c, it.id, KEY))


def _reasons(it):
    return [e["payload"]["reason"] for e in it.events("work_item_needs_human")]


@pytest.mark.parametrize(
    ("outcome", "result", "status"),
    [
        ("rate_limited", "rate_limited", "rate_limited"),
        ("waiting", "waiting", "waiting"),
        ("infra_stop", "needs_human", "needs_human"),
        ("config_error", "needs_human", "needs_human"),
        ("paused", "paused", "active"),
    ],
)
async def test_a_repair_that_never_ran_spends_no_attempt_and_names_its_own_cause(
    item_on, script, outcome, result, status
):
    script.real = {"check"}
    script.plan = {"fix": [outcome]}
    it = await item_on(_node(_sub("check", ["false"]), [{"id": "repair", "tasks": [_sub("fix")]}]))

    assert await _walk_node(it) == result
    assert it.status() == status
    assert _counter(it) is None, "the attempt the repair never made was spent"
    assert [e["payload"]["outcome"] for e in it.events("fix_cycle_refunded")] == [outcome]
    assert not any("stuck" in r or "exhausted" in r for r in _reasons(it))


async def test_a_failed_sync_step_stops_naming_it_without_spending_an_attempt(item_on, script):
    """The repair ran; the sync that would have published it did not. Nothing
    the next measurement sees was repaired, so this is the plumbing's stop."""
    script.real = {"check"}
    script.plan = {"sync": ["failed"]}
    fix_steps = [
        {"id": "repair", "tasks": [_sub("fix")]},
        {"id": "sync", "tasks": [{"id": "sync", "kind": "forge", "target": "mr.sync"}]},
    ]
    it = await item_on(_node(_sub("check", ["false"]), fix_steps))

    assert await _walk_node(it) == "needs_human"
    assert _counter(it) is None
    assert _reasons(it) == ["fix attempt 1 in node build could not finish: sync [forge] failed"]


async def test_a_repair_that_ran_and_failed_is_a_spent_attempt(item_on, script):
    script.real = {"check"}
    script.plan = {"fix": ["failed"]}
    it = await item_on(
        _node(_sub("check", ["false"]), [{"id": "repair", "tasks": [_sub("fix")]}], attempts=1)
    )

    assert await _walk_node(it) == "needs_human"
    assert _counter(it)["count"] == 2
    assert _reasons(it)[-1].startswith(f"{KEY} exhausted after 1 fix cycle(s)")


async def test_a_refunded_cycle_does_not_read_as_no_progress_on_re_entry(item_on, script, tmp_path):
    """The rate-limit poller re-enters the node; the same failure measured
    again is not "unchanged across a fix", because no fix ran."""
    script.real = {"check"}
    script.plan = {"fix": ["rate_limited", "done"]}
    check = _sub("check", _fails_until(tmp_path / "runs", passes_on=3))
    it = await item_on(_node(check, [{"id": "repair", "tasks": [_sub("fix")]}]))

    assert await _walk_node(it) == "rate_limited"
    assert await _walk_node(it) == "ok"
    assert script.calls == ["check", "fix", "check", "fix", "check"]
    assert _reasons(it) == []


async def test_a_retried_node_dispatches_its_recovery_again_rather_than_reusing_it(item_on, script):
    """Kraft-znsvg, on the `on_failure` path: after `/retry` the rounds
    restart, so the pre-retry repair's `done` session sits at the same round
    and head -- and the steered repair must still run."""
    script.real = {"check", "repair"}
    node = {
        "id": "build",
        "kind": "exec",
        "tasks": [_sub("check", ["false"])],
        "on_failure": {"tasks": [_sub("repair")]},
    }
    it = await item_on([node])

    assert await _walk_node(it) == "needs_human"
    await it.database.write(lambda c: store.claim_for_run(c, it.id, from_statuses=["needs_human"]))
    await it.database.write(lambda c: store.retry_after_cap(c, it.id, "build", None, "try again"))
    assert await _walk_node(it) == "needs_human"

    assert script.calls.count("repair") == 2, script.calls
