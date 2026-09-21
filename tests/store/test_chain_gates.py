import json

import pytest
from support.store_fixtures import mk_item

from kraft import events, store


async def test_node_lifecycle_events_and_current_node(database):
    await mk_item(database)
    await database.write(lambda c: store.load_chain(c, "w1", "env_setup"))
    await database.write(lambda c: store.enter_node(c, "w1", "env_setup"))
    await database.write(lambda c: store.complete_node(c, "w1", "env_setup"))
    await database.write(lambda c: store.enter_node(c, "w1", "verify"))
    row = database.read(
        lambda c: c.execute("SELECT current_node_id FROM work_items WHERE id='w1'").fetchone()
    )
    assert row["current_node_id"] == "verify"
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0))]
    assert types == [
        "work_item_created",
        "chain_loaded",
        "node_started",
        "node_completed",
        "node_started",
    ]


async def test_complete_node_is_idempotent(database):
    """Resume can re-enter an already-completed node; only one node_completed."""

    await mk_item(database)
    await database.write(lambda c: store.load_chain(c, "w1", "env_setup"))
    await database.write(lambda c: store.complete_node(c, "w1", "env_setup"))
    await database.write(lambda c: store.complete_node(c, "w1", "env_setup"))
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0))]
    assert types.count("node_completed") == 1


async def test_row_and_event_are_atomic(database):
    """A write that mutates a row then raises leaves neither row nor event."""

    await mk_item(database)

    def bad(c):
        store.enter_node(c, "w1", "env_setup")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await database.write(bad)

    row = database.read(
        lambda c: c.execute("SELECT current_node_id FROM work_items WHERE id='w1'").fetchone()
    )
    assert row["current_node_id"] is None  # enter_node rolled back
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0))]
    assert types == ["work_item_created"]  # no node_started event


async def test_last_rejection_reads_the_note_and_the_target_back(database):
    await mk_item(database)
    await database.write(
        lambda c: store.reject_gate(
            c, "w1", "plan_approval", "task 4 has no test", reopen=False, node="plan"
        )
    )
    got = database.read(lambda c: store.last_rejection(c, "w1"))
    assert got == {
        "gate": "plan_approval",
        "note": "task 4 has no test",
        "node": "plan",
        "by": "human",
        # Kraft-s7c04.16: additive, and None for a caller that names no
        # verdict -- the key is always present so a reader never has to
        # tell "no verdict" from "event predates the field".
        "verdict": None,
    }


async def test_reject_gate_records_the_verdict_that_produced_it(database):
    """Kraft-s7c04.16: without this a reviewer that rejected and a reviewer that
    fixed-and-committed are the same row, so the oscillation Kraft-s7c04.6 is
    about is invisible in every aggregate -- the investigation that found it had
    to read session logs in sequence."""

    await mk_item(database)
    await database.write(
        lambda c: store.reject_gate(
            c,
            "w1",
            "human_review_approval",
            "repaired the swallowed OSError",
            reopen=True,
            node="human_review",
            by="agent",
            verdict="fixed",
        )
    )
    got = database.read(lambda c: store.last_rejection(c, "w1"))
    assert got["verdict"] == "fixed"
    assert got["by"] == "agent"


async def test_fixed_verdicts_are_countable_without_opening_a_log(database):
    """The whole point of Kraft-s7c04.16: one query over `events`, no session
    logs, no ordering reconstruction."""

    await mk_item(database)
    for verdict in ("fixed", "reject", "fixed"):
        await database.write(
            lambda c, v=verdict: store.reject_gate(
                c, "w1", "g", "n", reopen=True, node="x", by="agent", verdict=v
            )
        )
    n = database.read(
        lambda c: c.execute(
            "SELECT COUNT(*) FROM events WHERE work_item_id = 'w1' "
            "AND type = 'gate_rejected' "
            "AND json_extract(payload, '$.verdict') = 'fixed'"
        ).fetchone()[0]
    )
    assert n == 2


async def test_a_rejection_is_spent_once_a_node_starts_on_it(database):
    """The note is the *pending* rejection's, not the newest one ever. A
    rejection whose re-run already launched has had its note delivered; handing
    it to an unrelated retry three nodes later would steer with stale text."""

    await mk_item(database)
    await database.write(
        lambda c: store.reject_gate(c, "w1", "plan_approval", "old news", reopen=True, node="plan")
    )
    await database.write(lambda c: store.enter_node(c, "w1", "plan"))
    assert database.read(lambda c: store.last_rejection(c, "w1")) is None


def _item(database, columns="*", wid="w1"):
    return database.read(
        lambda c: c.execute(f"SELECT {columns} FROM work_items WHERE id = ?", (wid,)).fetchone()
    )


def _node_skipped(database):
    evs = database.read(lambda c: events.read_after(c, 0, "w1"))
    return [e["payload"] for e in evs if e["type"] == "node_skipped"]


async def test_skip_node_marks_active_and_appends_event(database):
    await mk_item(database)
    await database.write(lambda c: store.mark_needs_human(c, "w1", "verify", "boom"))
    await database.write(lambda c: store.skip_node(c, "w1", "verify", None, "flaky, known issue"))
    assert tuple(_item(database, "status, retry_at")) == ("active", None)
    assert _node_skipped(database) == [
        {"node_id": "verify", "gate": None, "note": "flaky, known issue"}
    ]


async def test_skip_node_records_the_gate_it_bypassed(database):
    await mk_item(database)
    await database.write(lambda c: store.skip_node(c, "w1", "env_setup", "spec_approval", None))
    assert _node_skipped(database) == [
        {"node_id": "env_setup", "gate": "spec_approval", "note": None}
    ]


async def test_skip_node_marks_a_running_session_paused_before_it_can_be_read_as_failed(database):
    """Same race `pause_work_item` guards against (test_pause_resume.py's
    `test_a_paused_row_and_its_event_never_disagree`): the session's own exit
    handler must see 'paused' already there, or a `_terminate`d session reads
    back as 'failed' forever."""
    await mk_item(database)
    await database.write(
        lambda c: store.create_session(
            c,
            id="s1",
            work_item_id="w1",
            node_id="verify",
            hook_point="on.test.run",
            log_path="l",
            result_path="r",
        )
    )
    await database.write(
        lambda c: store.skip_node(c, "w1", "verify", None, None, session_ids=["s1"])
    )
    await database.write(lambda c: store.session_exited(c, "s1", "failed"))
    row = database.read(
        lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
    )
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]
    assert row["status"] == "paused"
    assert "worker_session_exited" not in types
    assert "worker_session_paused" in types


async def _stopped_on(database, node_id, overrides=None, *, chain=None, materialized=None):
    """`w1` stopped on `node_id`, its chain a legacy `chain_definition` dict
    (`chain`) or a V1 `materialized_chain` with a `"{}"` chain_definition, and
    `overrides` as its node overrides. Returns the row."""
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id=None,
            title="t",
            repo="/r",
            chain_template="default",
            chain_definition=json.dumps(chain or {}),
            materialized_chain=materialized,
        )
    )
    await database.write(lambda c: store.enter_node(c, "w1", node_id))
    if overrides is not None:
        await database.write(lambda c: store.set_node_overrides(c, "w1", overrides))
    return _item(database)


def _impl(**fields):
    return {"nodes": [{"id": "implementation", "tasks": [], **fields}]}


_STUCK = store.effective_auto_escalate_stuck
_DELAY = store.effective_auto_escalate_delay_s


@pytest.mark.parametrize(
    ("effective", "chain", "overrides", "default", "expected"),
    [
        (_STUCK, _impl(), None, True, True),
        (_STUCK, _impl(), None, False, False),
        (_STUCK, _impl(auto_escalate_stuck=False), None, True, False),
        (_STUCK, _impl(auto_escalate_stuck=False), {"auto_escalate_stuck": True}, False, True),
        (_STUCK, {}, {"auto_escalate_stuck": True}, False, False),
        (_STUCK, {}, {"auto_escalate_stuck": False}, True, True),
        (_DELAY, _impl(), None, 300, 300),
        (_DELAY, _impl(), None, 0, 0),
        (_DELAY, _impl(auto_escalate_delay_s=60), None, 0, 60),
        (_DELAY, _impl(auto_escalate_delay_s=60), {"auto_escalate_delay_s": 5}, 0, 5),
        (_DELAY, {}, {"auto_escalate_delay_s": 5}, 30, 30),
    ],
    ids=[
        "stuck-falls-back-to-the-default",
        "stuck-falls-back-to-a-false-default",
        "stuck-the-node-value-beats-the-default",
        "stuck-the-override-beats-the-node-value",
        "stuck-survives-a-bare-chain-definition-with-an-override",
        "stuck-bare-chain-takes-a-true-default",
        "delay-falls-back-to-the-default",
        "delay-falls-back-to-a-zero-default",
        "delay-the-node-value-beats-the-default",
        "delay-the-override-beats-the-node-value",
        "delay-survives-a-bare-chain-definition-with-an-override",
    ],
)
async def test_effective_auto_escalate_on_a_legacy_chain(
    database, effective, chain, overrides, default, expected
):
    """Override -> chain node value -> the caller's default. The bare-chain rows
    are a regression: `effective_chain` indexes `chain_definition["nodes"]` once
    `node_overrides` is non-empty, so a `{}` chain plus any override raised
    KeyError. No node to apply the override to -> the default, not a crash."""
    ov = None if overrides is None else {"implementation": overrides}
    row = await _stopped_on(database, "implementation", ov, chain=chain)
    assert effective(row, default) == expected


# ── the V1 materialized snapshot, stored alongside the legacy chain_definition ──


def _materialized():
    from pathlib import Path

    from kraft.policy import InstancePolicy, InstancePolicyInput
    from kraft.templates.environment import Repository, WorkItemTarget
    from kraft.templates.library import TemplateLibrary

    root = Path(__file__).resolve().parents[2] / "templates"
    return (
        TemplateLibrary.from_yaml_dir(root)
        .resolve_chain("default")
        .materialize(
            target=WorkItemTarget.for_repository(Repository(id="api", path="/work/api")),
            effective_policy=InstancePolicy.from_input(
                InstancePolicyInput.model_validate({"defaults": {"timeout_minutes": 60}})
            ),
        )
    )


async def test_a_materialized_chain_round_trips_through_the_work_item_row(database):
    """The column is the work item's immutable V1 input: what intake wrote is
    what the executor reads back, target and effective policy included."""
    from kraft.templates.models import MaterializedChain

    materialized = _materialized()
    row = await _stopped_on(database, "n", materialized=materialized.to_json())
    restored = store.materialized_chain_of(row)

    assert isinstance(restored, MaterializedChain)
    assert restored.task_paths == materialized.task_paths
    assert restored.target == materialized.target
    assert restored.policy == materialized.policy
    # Decision 1: additive. The legacy column is untouched and still readable.
    assert row["chain_definition"] == "{}"


async def test_a_row_with_no_materialized_chain_reads_as_none(database):
    """Every row written before this migration, and every legacy-path item
    Task 5 has not converted yet."""
    row = await _stopped_on(database, "n", chain={"nodes": []})
    assert store.materialized_chain_of(row) is None
    assert row["run_fork_parent"] is None


async def test_the_run_fork_parent_column_is_written_at_intake(database):
    """Reserved for Phase 5's retry forks: a fork records the run it came from
    without a second migration."""
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w2",
            bead_id=None,
            title="t",
            repo="/r",
            chain_template="default",
            chain_definition="{}",
            run_fork_parent="w1",
        )
    )
    assert _item(database, "run_fork_parent", "w2")[0] == "w1"


def _v1(node_id):
    """A V1 `materialized_chain` of one exec node, `node_id`."""
    from support.harness import v1_chain

    task = {"id": "run", "kind": "subprocess", "command": "true"}
    return v1_chain([{"id": node_id, "kind": "exec", "tasks": [task]}], repo="/r").to_json()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"verify": {"auto_escalate_stuck": False, "auto_escalate_delay_s": 900}}, (False, 900)),
        ({"other": {"auto_escalate_stuck": False}}, (True, 30)),
    ],
    ids=["a-per-node-override-is-actually-read", "a-node-nobody-overrode-takes-the-default"],
)
async def test_effective_auto_escalate_on_a_v1_item(database, overrides, expected):
    """Both `effective_auto_escalate_*` helpers bailed to `default` for every V1
    row -- a V1 item's `chain_definition` is `"{}"`, so their `"nodes" not in`
    guard fired first -- while `PATCH /work-items/{id}` validated the override,
    persisted it and returned 200. The policy-level value still worked; the
    per-node override was silently dead.

    In V1 neither key is a *node* field (`ExecNode`/`GateNode` declare neither;
    both are `policy.yaml` keys), so the override layer is the whole of what "the
    per-node value" can mean here -- there is no node value beneath it to fall
    through to.
    """
    row = await _stopped_on(database, "verify", overrides, materialized=_v1("verify"))
    assert (_STUCK(row, True), _DELAY(row, 30)) == expected
