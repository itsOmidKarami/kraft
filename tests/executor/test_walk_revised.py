"""A chain revised under a running walk (an applied item draft): the walk goes
on with it from the next node boundary, finds its place by node id, and stops
for a person rather than guess when that place is gone."""

from __future__ import annotations

from pathlib import Path

import pytest
from support.harness import entry_of, v1_chain

from kraft import executor, store
from kraft.templates import revision
from kraft.templates.library import TemplateLibrary

NO_SETUP = entry_of({"setup_command": ""})
LIBRARY = TemplateLibrary.from_mappings(
    {"tasks": {"later": {"kind": "agent", "harness": "fake", "prompt": "Do it."}}},
    (),
    library_path=Path("library.yaml"),
)


def _agent(task_id):
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": "Do it."}


def _exec(node_id, *tasks):
    return {"id": node_id, "kind": "exec", "tasks": [_agent(t) for t in tasks]}


def _walk(it):
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )


async def _write_chain(it, nodes):
    """The chain columns written directly, as a writer that skips `revise` would."""
    raw = v1_chain(nodes, repo=it.repo).to_json()
    await it.database.write(
        lambda c: c.execute(
            "UPDATE work_items SET materialized_chain = ? WHERE id = ?", (raw, it.id)
        )
    )


async def test_a_running_walk_runs_the_nodes_a_revision_changed_after_it(item_on, script):
    """While `first` runs, a draft removes `second` and adds `added` after
    `first`: the walk runs `added`, never `second`."""
    it = await item_on([_exec("first", "a"), _exec("second", "b")])

    async def revise(row):
        changes = revision.ChangeSet.model_validate(
            {
                "rationale": "item draft",
                "skip": [{"node": "second", "evidence": "item draft"}],
                "add": [
                    {
                        "after": "first",
                        "node": {"id": "added", "tasks": [{"id": "c", "extends": "later"}]},
                        "evidence": "item draft",
                    }
                ],
            }
        )
        chain = store.materialized_chain_of(row)
        revised = revision.revise(chain, changes, at="first", library=LIBRARY)
        seen = (row["materialized_chain"], row["run_chain"])
        await it.database.write(
            lambda c: store.revise_chain(c, it.id, revised.to_json(), {}, seen=seen)
        )

    script.effects["a"] = revise

    assert await _walk(it) == "completed"
    assert script.calls == ["a", "c"]


async def test_the_node_running_keeps_the_steps_it_started_with(item_on, script):
    """A swap happens between nodes, never inside one: `first`'s second step
    runs as it started, whatever the chain now says it is."""
    two_steps = {
        "id": "first",
        "kind": "exec",
        "steps": [{"id": "one", "tasks": [_agent("a")]}, {"id": "two", "tasks": [_agent("a2")]}],
    }
    it = await item_on([two_steps, _exec("second", "b")])

    async def rewrite(row):
        changed = {
            **two_steps,
            "steps": [two_steps["steps"][0], {"id": "two", "tasks": [_agent("a9")]}],
        }
        await _write_chain(it, [changed, _exec("second", "b")])

    script.effects["a"] = rewrite

    assert await _walk(it) == "completed"
    assert script.calls == ["a", "a2", "b"]


@pytest.mark.parametrize(
    ("nodes", "where"),
    [
        ([_exec("other", "z"), _exec("second", "b")], "is gone"),
        (
            [_exec("other", "z"), _exec("first", "a"), _exec("second", "b")],
            "moved from position 0 to 1",
        ),
    ],
    ids=["gone", "moved"],
)
async def test_a_walk_whose_place_left_the_chain_stops_for_a_person(item_on, script, nodes, where):
    """Unreachable through `revise`, which freezes what has run; this is the
    guard for the next writer of the chain columns."""
    it = await item_on([_exec("first", "a"), _exec("second", "b")])

    async def rewrite(row):
        await _write_chain(it, nodes)

    script.effects["a"] = rewrite

    assert await _walk(it) == "needs_human"
    assert script.calls == ["a"]
    [stop] = it.events("work_item_needs_human")
    assert "'first'" in stop["payload"]["reason"] and where in stop["payload"]["reason"]
