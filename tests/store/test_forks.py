"""Run forks: what a retry records and what it invalidates
(`retry-creates-an-immutable-run-fork`, `retry-reopens-invalidated-gates`)."""

from __future__ import annotations

import sqlite3

import pytest
from support import schema

from kraft import events, policy, store
from kraft.templates.forks import ChainPath, ControlScope, RetryOverride

#: exec `a` (two concurrent tasks) -> gate `g1` -> exec `b` -> gate `g2` -> exec `c`.
CHAIN = """
- id: a
  kind: exec
  fix_loop:
    tasks: [{id: fix, kind: subprocess, command: "true"}]
  steps:
    - id: first
      tasks:
        - {id: x, kind: subprocess, command: "true"}
        - {id: y, kind: subprocess, command: "true"}
    - id: second
      tasks: [{id: z, kind: subprocess, command: "true"}]
- {id: g1, kind: gate}
- id: b
  kind: exec
  fix_loop:
    tasks: [{id: fix, kind: subprocess, command: "true"}]
  tasks: [{id: run, kind: subprocess, command: "true"}]
- {id: g2, kind: gate}
- {id: c, kind: exec, tasks: [{id: run, kind: subprocess, command: "true"}]}
"""


async def _fork(database, it, path, **kwargs):
    target = ChainPath.parse(store.materialized_chain_of(it.row()), path) if path else None
    return await database.write(lambda c: store.fork_run(c, it.id, target, **kwargs))


async def test_retry_creates_a_fork_and_preserves_prior_run_data(item_on, database):
    """A retry deletes nothing: every earlier session and event stays, and the
    fork says where the earlier run's record ends. A second retry forks from the
    first, so the lineage is a chain of parents."""
    it = await item_on(CHAIN, "c", status="needs_human")
    await it.session("s1", "a.first.x", "done")
    await it.session("s2", "c.main.run", "failed")
    before = it.events()

    first = await _fork(database, it, "b")
    second = await _fork(database, it, "a.first.y")

    assert [s["id"] for s in it.sessions()] == ["s1", "s2"]
    assert it.events()[: len(before)] == before
    assert first.after_seq == before[-1]["seq"]
    assert (first.parent, second.parent) == (None, first.id)
    assert [f.id for f in database.read(lambda c: store.run_forks(c, it.id))] == [
        first.id,
        second.id,
    ]
    assert (first.scope, first.path, second.scope) == (ControlScope.NODE, "b", ControlScope.TASK)
    assert it.row()["run_chain"] == second.materialized_chain
    assert store.materialized_chain_of(it.row()).task_paths == it.chain.task_paths


async def test_a_work_item_restart_forks_the_whole_chain(item_on, database):
    it = await item_on(CHAIN, "c", status="needs_human")

    fork = await _fork(database, it, None)

    assert (fork.scope, fork.path, fork.start) == (ControlScope.WORK_ITEM, None, (0, 0))


@pytest.mark.parametrize(
    "statement",
    ["UPDATE run_forks SET path = 'c'", "DELETE FROM run_forks"],
    ids=["update", "delete"],
)
def test_a_run_fork_is_immutable(tmp_path, statement):
    """Refused by the table itself. An upgraded database gets the same
    triggers: `test_a_fresh_schema_and_a_fully_migrated_one_agree`."""
    conn = schema.fresh(tmp_path)
    schema.insert_item(conn)
    conn.execute(
        "INSERT INTO run_forks (id, work_item_id, scope, path, after_seq, materialized_chain, "
        "created_at) VALUES ('f1', 'w1', 'node', 'b', 0, '{}', 'now')"
    )

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(statement)


async def test_retry_reopens_downstream_gates_only(item_on, database):
    """Both gates were approved. A retry of `b` reopens `g2`, which is in its
    span, and leaves `g1`, which is before it, decided."""
    it = await item_on(CHAIN, "c", status="needs_human")
    for gate in ("g1", "g2"):
        await database.write(lambda c, g=gate: store.approve_gate(c, it.id, g))

    fork = await _fork(database, it, "b")

    assert [e["payload"]["gate"] for e in it.events("gate_reopened")] == ["g2"]
    assert it.events("run_forked")[-1]["payload"]["reopened"] == ["g2"]
    assert fork.start == (2, 0)


async def test_a_gate_already_reopened_is_not_reopened_twice(item_on, database):
    it = await item_on(CHAIN, "c", status="needs_human")
    await database.write(lambda c: store.approve_gate(c, it.id, "g2"))

    await _fork(database, it, "b")
    await _fork(database, it, "b")

    assert len(it.events("gate_reopened")) == 1


async def test_a_retry_clears_loop_counters_across_its_span_only(item_on, database):
    it = await item_on(CHAIN, "c", status="needs_human")
    cap = policy.Cap(3, 3600)
    for key in ("a.fix_loop", "b.fix_loop", "ci_wait:c"):
        await database.write(lambda c, k=key: store.bump_counter(c, it.id, k, cap))

    await _fork(database, it, "b")

    left = database.read(
        lambda c: [r["key"] for r in c.execute("SELECT key FROM retry_counters").fetchall()]
    )
    assert left == ["a.fix_loop"]


async def test_a_rerun_node_completes_again_in_its_fork(item_on, database):
    """`complete_node` is idempotent within one run, not across a retry: a
    completed node the retry reruns must say it completed again."""
    it = await item_on(CHAIN, "c", status="needs_human")
    await database.write(lambda c: store.complete_node(c, it.id, "b"))
    await database.write(lambda c: store.complete_node(c, it.id, "b"))

    await _fork(database, it, "b")
    await database.write(lambda c: store.complete_node(c, it.id, "b"))
    await database.write(lambda c: store.complete_node(c, it.id, "b"))

    assert len(it.events("node_completed")) == 2


async def test_the_fork_boundary_is_where_the_current_run_starts(item_on, database):
    it = await item_on(CHAIN, "c", status="needs_human")
    assert database.read(lambda c: store.fork_boundary(c, it.id)) == 0

    fork = await _fork(database, it, "a")
    await database.write(lambda c: events.append(c, it.id, "later", {}))

    assert database.read(lambda c: store.fork_boundary(c, it.id)) == fork.after_seq > 0


async def test_the_item_runs_its_forks_copy_of_the_chain(item_on, database):
    """Every reader of the row gets the fork's copy, override applied; the
    intake snapshot stays as it was filed. (`fork_run` is handed the override
    here directly -- the route hands it only what `validate_retry_override`
    returned.)"""
    it = await item_on(CHAIN, "c")
    snapshot = it.row()["materialized_chain"]

    await _fork(database, it, "b.main.run", override=RetryOverride(task={"command": "make again"}))

    runs = ChainPath.parse(store.materialized_chain_of(it.row()), "b.main.run").task.task
    assert runs.command == "make again"
    assert it.row()["materialized_chain"] == snapshot
