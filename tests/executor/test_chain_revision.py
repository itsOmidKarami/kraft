"""A chain revision on a work item (Kraft-oydes, Ruling 208): an unchanged
proposal passes its gate without a human, an approved one replaces the chain
every later reader sees, and an invalid one never reaches it."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.harness import entry_of

from kraft import executor, store
from kraft.api.routes import gates as gates_route
from kraft.executor.context import LaunchContext
from kraft.templates.library import TemplateLibrary

NO_SETUP = LaunchContext(repo_entry=entry_of({"setup_command": ""}), steering_dir=None)
GATE = "revision_approval"

LIBRARY = TemplateLibrary.from_mappings(
    {
        "tasks": {"check": {"kind": "subprocess", "command": "true"}},
        "nodes": {"extra_check": {"kind": "exec", "tasks": [{"id": "run", "extends": "check"}]}},
    },
    (),
    library_path=Path("library.yaml"),
)


def _run(id: str) -> dict:
    return {
        "id": id,
        "kind": "exec",
        "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
    }


def _chain(tmp_path: Path, change: dict) -> list[dict]:
    """revise (writes `change` as its chain_revision) -> revision_approval ->
    build -> brief."""
    source = tmp_path / "proposal.md"
    source.write_text(f"---\nwork_item_ids: [w1]\n---\n```json\n{json.dumps(change)}\n```\n")
    write = (
        "sh -c 'mkdir -p .engineering/chain_revisions && "
        f"cp {source} .engineering/chain_revisions/w1.md'"
    )
    return [
        {
            "id": "revise",
            "kind": "exec",
            "tasks": [{"id": "write", "kind": "subprocess", "command": write}],
        },
        {"id": GATE, "kind": "gate", "artifact": "chain_revision", "reject_to": "revise"},
        _run("build"),
        _run("brief"),
    ]


PROPOSAL = {
    "rationale": "the plan needs a second check and no brief",
    "skip": [{"node": "brief", "evidence": "plan: docs only"}],
    "add": [
        {"after": "build", "node": {"id": "checked", "extends": "extra_check"}, "evidence": "plan"}
    ],
}


def _walk(it, **kwargs):
    return executor.run_once(
        it.database, it.run_dirs, work_item_id=it.id, launch=NO_SETUP, **kwargs
    )


def _state(it, library=LIBRARY):
    class _Indexer:
        async def ingest_gate_artifact(self, **kw):
            pass

    return SimpleNamespace(
        db=it.database, run_dirs=it.run_dirs, indexer=_Indexer(), library=library
    )


def _ids(row) -> list[str]:
    return [n.id for n in store.materialized_chain_of(row).chain.nodes]


def _completed(it) -> list[str]:
    return [e["payload"]["node_id"] for e in it.events("node_completed")]


async def _approve(it):
    """`apply_approval`, then what the approve route does after it."""
    nodes, reason = await gates_route.apply_approval(_state(it), it.row(), GATE)
    assert reason is None
    await it.database.write(lambda c: store.approve_gate(c, it.id, GATE))
    return nodes


async def test_an_unchanged_revision_advances_without_a_human(item_on, tmp_path):
    it = await item_on(_chain(tmp_path, {"rationale": "the chain fits the plan"}))

    assert await _walk(it) == "completed"

    assert it.events("gate_requested") == []
    [unchanged] = it.events("chain_revision_unchanged")
    assert unchanged["payload"] == {"gate": GATE, "rationale": "the chain fits the plan"}
    assert [e["payload"] for e in it.events("gate_approved")] == [{"gate": GATE, "by": "kraft"}]
    assert _completed(it) == ["revise", GATE, "build", "brief"]


async def test_a_proposed_change_stops_at_the_gate(item_on, tmp_path):
    it = await item_on(_chain(tmp_path, PROPOSAL))

    assert await _walk(it) == "awaiting_gate"

    assert [e["payload"]["gate"] for e in it.events("gate_requested")] == [GATE]
    assert it.events("chain_revision_unchanged") == []
    assert _ids(it.row()) == ["revise", GATE, "build", "brief"]


async def test_an_approved_revision_is_the_chain_every_later_reader_sees(item_on, tmp_path):
    it = await item_on(_chain(tmp_path, PROPOSAL))
    await _walk(it)

    nodes = await _approve(it)

    revised = ["revise", GATE, "build", "checked"]
    # The approval door's own nodes, the stored chain, and the board's view.
    assert [n.id for n in nodes] == revised
    assert _ids(it.row()) == revised
    assert [n["id"] for n in store.chain_view(it.row())["nodes"]] == revised
    [event] = it.events("chain_revised")
    assert event["payload"]["gate"] == GATE
    assert event["payload"]["changes"]["skip"] == [{"node": "brief", "evidence": "plan: docs only"}]
    assert "- brief" in event["payload"]["diff"] and "+ checked" in event["payload"]["diff"]
    # The walk the approval starts runs the revised chain.
    assert await _walk(it, start_index=2) == "completed"
    assert _completed(it) == ["revise", GATE, "build", "checked"]


async def test_a_retry_after_a_revision_keeps_it(item_on, tmp_path):
    it = await item_on(_chain(tmp_path, PROPOSAL))
    await _walk(it)
    await _approve(it)

    await it.database.write(lambda c: store.fork_run(c, it.id, None))

    assert _ids(it.row()) == ["revise", GATE, "build", "checked"]


async def test_a_revision_after_a_retry_revises_the_forks_chain(item_on, tmp_path):
    """After a retry the item runs its fork's copy (`run_chain`); a revision
    that wrote the intake snapshot instead would be invisible to every reader."""
    it = await item_on(_chain(tmp_path, PROPOSAL))
    await it.database.write(lambda c: store.fork_run(c, it.id, None))
    intake = it.row()["materialized_chain"]
    await _walk(it)

    await _approve(it)

    assert _ids(it.row()) == ["revise", GATE, "build", "checked"]
    assert it.row()["materialized_chain"] == intake


async def test_an_invalid_revision_never_reaches_the_chain(item_on, tmp_path):
    change = {"rationale": "redo it", "skip": [{"node": "revise", "evidence": "none"}]}
    it = await item_on(_chain(tmp_path, change))
    await _walk(it)
    before = it.row()

    nodes, reason = await gates_route.apply_approval(_state(it), before, GATE)

    assert nodes is None and "already run" in reason
    after = it.row()
    assert (after["materialized_chain"], after["run_chain"]) == (
        before["materialized_chain"],
        before["run_chain"],
    )
    assert it.events("chain_revised") == []


async def test_approving_a_revision_twice_applies_it_once(item_on, tmp_path):
    """A crash between the revision's write and the gate's own approval leaves
    the gate pending; approving it again must not re-apply (and so refuse)
    the change set against the chain it already revised."""
    it = await item_on(_chain(tmp_path, PROPOSAL))
    await _walk(it)
    st = _state(it)

    await gates_route.apply_approval(st, it.row(), GATE)
    nodes, reason = await gates_route.apply_approval(st, it.row(), GATE)

    assert reason is None
    assert [n.id for n in nodes] == ["revise", GATE, "build", "checked"]
    assert len(it.events("chain_revised")) == 1


async def test_the_gate_document_is_the_rendered_revision(item_on, tmp_path):
    """`GET /work-items/{id}/artifact` shows the proposal a person decides, not
    the JSON Kraft applies."""
    from kraft.api.routes import artifacts

    it = await item_on(_chain(tmp_path, PROPOSAL))
    await _walk(it)
    request = SimpleNamespace(app=SimpleNamespace(state=_state(it)))

    shown = (await artifacts.get_work_item_artifact(it.id, request))["content"]

    assert "- skip `brief` -- plan: docs only" in shown
    assert "```diff" in shown and "+ checked" in shown


async def test_the_revising_task_is_told_which_nodes_it_may_change(item_on, fake_agent):
    """Keyed on what the task produces (`chain_revision`), not on its name."""
    it = await item_on(
        [
            {
                "id": "rethink",
                "kind": "exec",
                "tasks": [
                    {
                        "id": "any_name",
                        "kind": "agent",
                        "harness": "fake",
                        "prompt": "Revise.",
                        "produces": "chain_revision",
                    }
                ],
            },
            {"id": GATE, "kind": "gate", "artifact": "chain_revision", "reject_to": "rethink"},
            _run("build"),
        ]
    )

    await _walk(it)

    [prompt] = fake_agent.prompts()
    assert f"including `{GATE}`" in prompt and '"build.main.run"' in prompt


async def test_a_resume_after_an_approved_revision_runs_the_revised_chain(item_on, tmp_path):
    """A crash between the approval and the walk it starts leaves the item on
    the gate; reattach resumes it from the stored chain, the revised one."""
    from kraft.executor import resuming

    it = await item_on(_chain(tmp_path, PROPOSAL))
    await _walk(it)
    await _approve(it)

    await resuming.resume_once(
        it.database, it.run_dirs, work_item_id=it.id, adopted={}, launch=NO_SETUP
    )

    assert _completed(it) == ["revise", GATE, "build", "checked"]


async def test_a_revision_computed_from_a_chain_that_has_since_changed_is_refused(
    item_on, tmp_path
):
    """A compare-and-set, like the attachment PATCH's: the revision is built
    from the row the approval read, and a write that changed the chain since
    (here a retry's fork) must not be overwritten by it."""
    it = await item_on(_chain(tmp_path, PROPOSAL))
    await _walk(it)
    stale = it.row()
    await it.database.write(lambda c: store.fork_run(c, it.id, None))
    forked = it.row()["run_chain"]

    nodes, reason = await gates_route.apply_approval(_state(it), stale, GATE)

    assert nodes is None and "changed" in reason
    assert it.row()["run_chain"] == forked
    assert it.events("chain_revised") == []


@pytest.mark.parametrize(
    "body",
    [
        "the chain fits, no changes",
        '```json\n{"rationale": "fits"\n```',
        '```json\n{"rationale": "fits", "note": "x"}\n```',
        "",
    ],
    ids=["prose", "truncated-json", "unknown-key", "empty"],
)
async def test_a_malformed_revision_is_never_read_as_no_change(item_on, tmp_path, body):
    """The mirror of the unchanged pass: only a change set that parses, and is
    empty, clears the gate unasked. Anything unreadable stops for a person."""
    it = await item_on(_chain(tmp_path, {"rationale": "placeholder"}))
    (tmp_path / "proposal.md").write_text(f"---\nwork_item_ids: [w1]\n---\n{body}\n")

    assert await _walk(it) == "awaiting_gate"

    assert [e["payload"]["gate"] for e in it.events("gate_requested")] == [GATE]
    assert it.events("chain_revision_unchanged") == []
