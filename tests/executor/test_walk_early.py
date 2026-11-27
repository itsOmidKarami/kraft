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
from kraft.executor import dispatch, walk
from kraft.findings import Finding
from kraft.vocab import ChainEvent

REVIEWER = Path(__file__).resolve().parents[1] / "support" / "fake_reviewer.py"
CLEAN = {"status": "done", "findings": []}
RED = {"status": "failed", "findings": []}
FIX = shlex.join(["sh", "-c", "echo x >> fixed.txt && git add fixed.txt && git commit -qm fix"])
#: The tasks `script` runs for real: the reporters and the fix.
REAL = frozenset({"bots", "triage", "ci", "fix"})
POLICY = _policy.Policy(loops={}, default=_policy.Cap(9, 3600))
LAUNCH = executor.LaunchContext(repo_entry=entry_of({"setup_command": ""}))


class Judge:
    """What the stand-in judge answers: `verdicts` in turn, the last repeating, and
    the `round` of every call in `rounds`."""

    def __init__(self):
        self.verdicts = ["continue"]
        self.rounds: list[int] = []


@pytest.fixture
def judge(monkeypatch) -> Judge:
    """`dispatch.judge_verdict` answered by a `Judge` rather than by a session."""
    stand_in = Judge()

    async def fake(*args, round, **kw):
        stand_in.rounds.append(round)
        verdicts = stand_in.verdicts
        return (verdicts.pop(0) if len(verdicts) > 1 else verdicts[0]), "why"

    monkeypatch.setattr(dispatch, "judge_verdict", fake)
    return stand_in


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


@pytest.mark.parametrize(
    ("bots", "calls", "cycles"),
    [
        ((CLEAN,), ["bots", "triage", "fix", "bots", "triage", "ci", "hold"], [0, 1]),
        (
            (RED, CLEAN),
            ["bots", "repair", "bots", "triage", "fix", "bots", "triage", "ci", "hold"],
            [-1, 1],
        ),
    ],
    ids=["measured-first", "after-on_failure"],
)
async def test_an_early_end_goes_straight_to_the_fix_loop(
    item_on, script, tmp_path, bots, calls, cycles
):
    """The triage finding ends the measurement before the CI step runs. The fix loop
    takes it: `on_failure` never answers it, and CI is first waited for on the fix."""
    triage = (_found("rename it"), CLEAN)
    it = await item_on([_node(tmp_path, bots=bots, triage=triage, on_failure=True)])
    script.real = REAL

    assert await _walk(it) == "ok"

    assert script.calls == calls
    assert _cycles(it) == cycles
    first = it.events("findings_measured")[0]["payload"]
    assert "n.ci.ci" in first["unmeasured_tasks"]


@pytest.mark.parametrize(
    ("then", "judged", "tail"),
    [
        (CLEAN, [1], []),
        (_found("lint failed", status="failed"), [1, -1], ["fix", "bots", "triage", "ci", "hold"]),
    ],
    ids=["re-measure-passes", "re-measure-fails"],
)
async def test_a_declined_early_start_is_not_judged_again_on_the_same_commit(
    item_on, script, judge, tmp_path, then, judged, tail
):
    """Round 1 ends early on "naming nit", which the judge declines. The continued ci
    step fails after a park, so on_failure runs and re-measures at -1 on the same
    commit: the nit doesn't end that measurement early, isn't collected and isn't
    judged again."""
    red = _found("lint failed", status="failed")
    nit = _found("naming nit", "important")
    it = await item_on(
        [
            _node(
                tmp_path,
                triage=(_found("rename"), nit, nit, CLEAN),
                ci=(red, red, then, CLEAN),
                on_failure=True,
            )
        ]
    )
    script.real = REAL
    script.plan = {"hold": ["paused", "done"]}
    judge.verdicts = ["stop_downgrade", "continue"]

    assert await _walk(it) == "paused"
    assert await _walk(it, it.row()["current_step"]) == "ok"

    assert judge.rounds == judged
    assert script.calls == [
        *[
            "bots",
            "triage",
            "fix",
            "bots",
            "triage",
            "ci",
            "hold",
        ],  # entry 1: parked after the continue
        *["ci", "hold", "repair", "bots", "triage", "ci", "hold"],  # entry 2: the -1 re-measure
        *tail,
    ]
    declined = it.events("judge_verdict")[0]["payload"]
    round_1 = it.events("findings_measured")[1]["payload"]
    assert (declined["ended_early"], declined["head_sha"]) == (True, round_1["head_sha"])
    for e in it.events("findings_measured")[2:] + it.events("judge_verdict")[1:]:
        assert "naming nit" not in [f["message"] for f in e["payload"]["findings"]]
    for instruction in script.instructions["fix"][1:]:
        # only the list to fix: round 1's early record still lists the nit in the history
        assert "naming nit" not in instruction.split("Findings to fix:")[1].split("\n\n")[0]


@pytest.mark.parametrize(
    "hold", [["done"], ["paused", "done"]], ids=["ci-at-once", "ci-parks-first"]
)
async def test_a_declined_early_start_continues_and_amends_its_record(
    item_on, script, judge, tmp_path, hold
):
    """The judge declines round 1's early start: the measurement goes on with the
    ci step at the same round, and its result amends round 1's one record."""
    it = await item_on(
        [
            _node(
                tmp_path,
                triage=(_found("rename"), _found("naming nit", "important")),
                ci=(_found("ci note", "minor"),),
                on_failure=True,
            )
        ]
    )
    script.real = REAL
    script.plan = {"hold": list(hold)}  # the script consumes its plan
    judge.verdicts = ["stop_downgrade"]

    result = await _walk(it)
    if hold[0] == "paused":
        assert result == "paused"
        result = await _walk(it, it.row()["current_step"])

    assert result == "ok"
    assert _cycles(it) == [0, 1]  # one record per measurement: the continue amended it
    record = it.events("findings_measured")[1]["payload"]
    assert "n.ci.ci" in record["measured_tasks"]
    assert [f["message"] for f in record["findings"]] == ["ci note"]
    # parked: ci's `done` session at round 1 stands on re-entry, only hold runs again
    parked = ["hold"] if hold[0] == "paused" else []
    assert script.calls == ["bots", "triage", "fix", "bots", "triage", "ci", "hold", *parked]
    assert [s["round"] for s in it.sessions("n") if s["hook_point"] == "n.ci.ci"] == [1]


async def test_a_recorded_measurement_is_not_resumed(item_on, script, tmp_path):
    """A stop after the record (the repair paused, so refunded) then a re-entry at the
    cursor starts a new measurement: it appends a record, never amends the finished one."""
    it = await item_on([_node(tmp_path, triage=(_found("rename"), CLEAN))])
    script.real = REAL - {"fix"}  # scripted, so it can pause; a scripted repair commits when done
    script.plan = {"fix": ["paused", "done"]}

    assert await _walk(it) == "paused"
    assert await _walk(it, it.row()["current_step"]) == "ok"

    assert _cycles(it) == [0, 0, 1]


async def test_findings_an_early_end_did_not_re_read_are_kept_apart(
    item_on, script, judge, tmp_path
):
    """Round 0's red ci (a reviewer-sourced finding) isn't re-read when round 1 ends at
    triage: it is recorded apart, never in the list to fix, shown as not re-read, keeps
    its severity and its tag. Re-read reworded in round 2, it may claim that tag, and it
    is no REPEAT: nobody was asked to fix it in round 1."""
    lint = _found("lint failed", status="failed", source="ci-bot")
    tag = Finding("critical", "lint failed", "a.py", 1, "ci-bot").fingerprint
    again = _found("lint failed again", status="failed", source="ci-bot", same_as=tag)
    it = await item_on(
        [_node(tmp_path, triage=(CLEAN, _found("rename"), CLEAN), ci=(lint, again, CLEAN))]
    )
    script.real = REAL
    judge.verdicts = ["continue"]

    assert await _walk(it) == "ok"

    rounds = [e["payload"] for e in it.events("findings_measured")]
    assert [f["message"] for f in rounds[1]["findings"]] == ["rename"]
    assert [(f["message"], f["severity"]) for f in rounds[1]["unmeasured"]] == [
        ("lint failed", "critical")
    ]
    to_fix = [i.split("Findings to fix:")[1].split("\n\n")[0] for i in script.instructions["fix"]]
    assert "rename" in to_fix[1] and "lint failed" not in to_fix[1]
    assert "not re-read this round" in script.instructions["fix"][1]
    assert rounds[2]["findings"][0]["same_as"] == tag
    assert "lint failed again" in to_fix[2] and "REPEAT" not in to_fix[2]


@pytest.mark.parametrize(
    ("bots", "triage", "ci", "stuck"),
    [
        (
            (CLEAN, _found("lint failed", source="ci-bot"), CLEAN),
            (CLEAN,),
            (_found("lint failed", source="ci-bot"), CLEAN),
            False,
        ),
        ((CLEAN,), (_found("rename"), _found("rename")), (CLEAN,), True),
    ],
    ids=["previous-read-other-tasks", "same-tasks"],
)
async def test_the_whole_set_stuck_check_needs_the_same_tasks_read(
    item_on, script, judge, tmp_path, bots, triage, ci, stuck
):
    """The same finding read by another task (the bot wait seeing a red pipeline, part 2)
    after a fix is not "unchanged" when the previous measurement read other tasks."""
    it = await item_on([_node(tmp_path, bots=bots, triage=triage, ci=ci)])
    script.real = REAL
    judge.verdicts = ["continue"]

    result = await _walk(it)

    reasons = [e["payload"]["reason"] for e in it.events("work_item_needs_human")]
    stuck_stop = any(r.startswith("stuck:") for r in reasons)
    assert (result, stuck_stop) == (("needs_human", True) if stuck else ("ok", False))
