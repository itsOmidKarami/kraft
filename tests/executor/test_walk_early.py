"""A fix loop that starts early (`fix_loop.early`): a step's eligible findings end
the measurement, a declined start continues it, and what it did not re-read is
kept apart."""

import json
import shlex
import sys
from pathlib import Path

import pytest
from support.chain_builders import v1_node, v1_task
from support.harness import entry_of

from kraft import events, executor
from kraft import policy as _policy
from kraft.executor import walk
from kraft.vocab import ChainEvent

REVIEWER = Path(__file__).resolve().parents[1] / "support" / "fake_reviewer.py"
CLEAN = {"status": "done", "findings": []}
RED = {"status": "failed", "findings": []}
FIX = shlex.join(["sh", "-c", "echo x >> fixed.txt && git add fixed.txt && git commit -qm fix"])
#: The tasks `script` runs for real: the reporters and the fix.
REAL = frozenset({"bots", "triage", "ci", "fix"})
POLICY = _policy.Policy(loops={}, default=_policy.Cap(9, 3600))
LAUNCH = executor.LaunchContext(repo_entry=entry_of({"setup_command": ""}))


def _found(message, severity="critical", *, status="done", source="reviewer", **extra) -> dict:
    """One result entry holding one finding."""
    finding = {
        "severity": severity,
        "message": message,
        "file": "a.py",
        "line": 1,
        "source_plugin": source,
        **extra,
    }
    return {"status": status, "findings": [finding]}


def _reporting(tmp_path, task_id, entries) -> dict:
    """A task that answers `entries` in turn, one per run."""
    plan = tmp_path / f"{task_id}-plan.json"
    plan.write_text(json.dumps(list(entries)))
    return v1_task(task_id, command=shlex.join([sys.executable, str(REVIEWER), str(plan)]))


def _node(tmp_path, *, bots=(CLEAN,), triage=(CLEAN,), ci=(CLEAN,), on_failure=False, early=True):
    """Node `n`: the bots, the triage, then the CI wait (and a scripted `hold`)."""
    steps = [
        {"id": "wait", "tasks": [_reporting(tmp_path, "bots", bots)]},
        {"id": "review", "tasks": [_reporting(tmp_path, "triage", triage)]},
        {"id": "ci", "tasks": [_reporting(tmp_path, "ci", ci), v1_task("hold")]},
    ]
    fix_loop = {"tasks": [v1_task("fix", command=FIX)], "judge": v1_task("judge"), "early": early}
    extra = {"on_failure": {"tasks": [v1_task("repair")]}} if on_failure else {}
    return v1_node("n", steps=steps, fix_loop=fix_loop, **extra)


async def _walk(it, start_step=0):
    """`walk_node` over the item's only node."""
    return await walk.walk_node(
        it.database,
        it.run_dirs,
        it.id,
        it.chain.chain.nodes[0],
        it.row(),
        it.repo,
        policy=POLICY,
        launch=LAUNCH,
        start_step=start_step,
    )


def _cycles(it) -> list[int]:
    """The cycle of each `findings_measured` event."""
    return [e["payload"]["cycle"] for e in it.events("findings_measured")]


@pytest.mark.parametrize(
    "between",
    [None, "upgrade", "restart"],
    ids=["own-round", "filed-before-the-upgrade", "after-a-restart"],
)
async def test_a_parked_re_measure_resumes_at_its_own_round(item_on, script, tmp_path, between):
    """The re-measure after on_failure runs at round -1. Parked in its last step, it
    resumes there, not at the loop counter's round. With no stored measurement, or
    one from before the node's run restarted, it measures as before."""
    it = await item_on([_node(tmp_path, ci=(RED, CLEAN, CLEAN), on_failure=True)])
    script.real = REAL
    script.plan = {"hold": ["done", "paused", "done"]}

    assert await _walk(it) == "paused"
    if between == "upgrade":
        await it.database.write(
            lambda c: c.execute("UPDATE work_items SET measurement = NULL WHERE id = ?", (it.id,))
        )
    elif between == "restart":
        await it.database.write(
            lambda c: events.append(c, it.id, ChainEvent.BASE_CHANGE_RESTART, {"node_id": "n"})
        )
    assert await _walk(it, it.row()["current_step"]) == "ok"

    assert _cycles(it) == ([-1] if between is None else [0])
    # resumed at -1, the re-measure's `done` ci session stands; at round 0 it reruns
    assert script.calls.count("ci") == (2 if between is None else 3)
