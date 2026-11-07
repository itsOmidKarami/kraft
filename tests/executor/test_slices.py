"""Walking a slice group: deciding, the prelude, the fork into child items, and
re-entering a group by its stored phase."""

from __future__ import annotations

import json

import pytest
from support.frozen import v1_frozen
from support.harness import commit_all, entry_of, git, write
from support.workspace import workspace_target

from kraft import executor, store
from kraft.executor import gates as gates_module
from kraft.executor import slices
from kraft.executor.context import LaunchContext
from kraft.templates.environment import WorkItemTarget

NO_SETUP = LaunchContext(repo_entry=entry_of({"setup_command": ""}))

PLAN = """# Plan

## Slices

- **Slice 1: Parser.** Tasks 1-2.
- **Slice 2: CLI.** Task 3.

### Task 1: a

### Task 2: b

### Task 3: c
"""


def _body(id: str, command: str = "true") -> dict:
    return {
        "id": id,
        "kind": "exec",
        "tasks": [{"id": "run", "kind": "subprocess", "command": command}],
    }


def _nodes(*body: dict, unforked=None) -> list[dict]:
    body = body or (_body("implementation"), _body("verification"))
    return [
        {
            "id": "slices",
            "kind": "group",
            "nodes": list(body),
            "unforked": unforked or [body[0]["id"]],
        },
        _body("after"),
    ]


@pytest.fixture
def planned(repo):
    """`repo` with a committed spec and a plan that declares two slices."""
    write(repo, "spec.md", "# spec\n")
    write(repo, "plan.md", PLAN)
    commit_all(repo, "docs")
    return repo


async def _item(item_on, planned, nodes=None, plan_path="plan.md", **kw):
    return await item_on(
        v1_chain_nodes(nodes or _nodes()),
        repo=planned,
        attachments=[
            {"kind": "spec", "path": "spec.md"},
            {"kind": "plan", "path": plan_path},
        ],
        **kw,
    )


def v1_chain_nodes(nodes, **kw):
    return v1_frozen(nodes, repo="/r", **kw)


def _walk(it, **kw):
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        launch=NO_SETUP,
        policy=kw.pop("policy", None),
        **kw,
    )


def _children(it):
    return it.database.read(lambda c: store.children_of(c, it.id))


async def test_a_group_with_two_slices_files_two_children_and_waits(item_on, planned, monkeypatch):
    async def no_members(*args, **kwargs):  # the fixture repo holds no submodule to check out
        return None

    monkeypatch.setattr("kraft.builtins._setup_submodules", no_members)
    it = await _item(item_on, planned)
    overrides = json.dumps({"implementation": {"model": "opus"}})
    await it.database.write(
        lambda c: c.execute(
            "UPDATE work_items SET agent_overrides = ?, submodules = ?, root_merge_policy = ? "
            "WHERE id = ?",
            (overrides, json.dumps(["libs/a"]), "bump", it.id),
        )
    )

    assert await _walk(it) == "waiting"

    row = it.row()
    assert (row["status"], row["stop_kind"]) == ("waiting", "slices")
    children = _children(it)
    assert [(c["slice_key"], c["status"]) for c in children] == [(1, "queued"), (2, "queued")]
    fork = git(it.worktree, "rev-parse", "HEAD")
    assert {c["base_ref"] for c in children} == {fork}
    for child in children:
        assert {a["kind"] for a in json.loads(child["attachments"])} == {"spec", "plan"}
        assert child["title"].startswith("t · slice ")
        assert child["agent_overrides"] == overrides  # inherited, frozen
        # The cross-repo paths it was filed with, carried to the child as they stand.
        assert json.loads(child["submodules"]) == ["libs/a"]
        assert child["root_merge_policy"] == "bump"
        chain = json.loads(child["materialized_chain"])
        assert [n["id"] for n in chain["chain"]["nodes"]] == ["implementation", "verification"]
    state = it.database.read(lambda c: store.group_state(c, it.id))
    assert (state.phase, state.generation, state.fork_commit) == ("forked", 1, fork)
    assert [e["type"] for e in it.events() if e["type"] == "slices_forked"] == ["slices_forked"]


@pytest.mark.parametrize(
    ("crashed", "status"),
    [
        pytest.param(True, "queued", id="never-queued"),
        pytest.param(False, "paused", id="paused-by-a-person-after-it-queued"),
    ],
)
async def test_re_entry_queues_only_the_children_the_fork_filed_paused(
    item_on, planned, crashed, status
):
    """A crash between filing and queueing leaves children paused that were never
    queued; one a person paused before it started was, and stays paused."""
    it = await _item(item_on, planned)
    await _walk(it)
    before = [c["id"] for c in _children(it)]

    def crash(c):
        c.execute(
            "UPDATE work_items SET status='paused', queued_request=NULL WHERE parent_id=?", (it.id,)
        )
        if crashed:
            c.execute("DELETE FROM events WHERE type='work_item_queued'")
        c.execute("UPDATE work_items SET status='active' WHERE id=?", (it.id,))

    await it.database.write(crash)
    assert await _walk(it) == "waiting"

    assert [c["id"] for c in _children(it)] == before
    assert [c["status"] for c in _children(it)] == [status, status]


async def test_re_entering_a_forked_group_files_no_children_again(item_on, planned):
    it = await _item(item_on, planned)
    await _walk(it)
    await it.database.write(
        lambda c: c.execute("UPDATE work_items SET status='active' WHERE id=?", (it.id,))
    )

    assert await _walk(it) == "waiting"

    assert len(_children(it)) == 2
    assert len([e for e in it.events() if e["type"] == "slices_forked"]) == 1


async def test_a_prelude_runs_in_the_parent_before_the_fork(item_on, planned):
    write(planned, "plan.md", PLAN.replace("Task 3.", "Task 3.\n").replace("Tasks 1-2.", "Task 1."))
    commit_all(planned, "plan with groundwork")
    it = await _item(item_on, planned)

    assert await _walk(it) == "waiting"

    ran = [e["payload"]["node_id"] for e in it.events("node_completed")]
    assert ran == ["slices.implementation", "slices.verification"]
    forked_at = next(e["seq"] for e in it.events() if e["type"] == "slices_forked")
    assert all(e["seq"] < forked_at for e in it.events("node_completed"))
    assert len(_children(it)) == 2


@pytest.mark.parametrize(
    ("target", "says"),
    [
        pytest.param(
            lambda: workspace_target({"a": "libs/a", "b": "libs/b"}),
            "workspace",
            id="workspace-item",
        ),
        pytest.param(
            lambda: WorkItemTarget.for_repository("target", in_place=True),
            "in-place",
            id="in-place-item",
        ),
    ],
)
async def test_an_item_that_cannot_be_sliced_runs_the_group_unforked(
    item_on, planned, target, says
):
    """Never forked, and the reason is an event. Decided before any worktree is
    touched, so the decision is read straight off the row."""
    it = await item_on(
        v1_chain_nodes(_nodes(), target=target()),
        repo=planned,
        attachments=[{"kind": "plan", "path": "plan.md"}],
        worktree=True,
    )
    row = it.row()
    node = it.chain.chain.nodes[0]

    decided = slices._decide(it.database, it.run_dirs, row, it.worktree, None, it.id, node, None)

    assert isinstance(decided, str)
    assert says in decided


async def test_a_group_runs_unforked_when_the_plan_has_no_split(item_on, planned):
    write(planned, "plan.md", "### Task 1: a\n")
    commit_all(planned, "plain plan")
    it = await _item(item_on, planned)

    assert await _walk(it) == "completed"

    [event] = it.events("slices_not_forked")
    assert "no ## Slices" in event["payload"]["reason"]
    assert [e["payload"]["node_id"] for e in it.events("node_completed")] == [
        "slices.implementation",
        "slices",
        "after",
    ]
    assert it.database.read(lambda c: store.group_state(c, it.id)).phase == "joined"


async def test_a_rejection_after_the_join_runs_unforked_in_the_parent(item_on, planned):
    it = await _item(item_on, planned)

    def joined(c):
        store.set_group_state(
            c, it.id, store.GroupState(group="slices", phase="joined", generation=1)
        )

    await it.database.write(joined)

    assert await _walk(it) == "completed"

    assert _children(it) == []
    assert [e["payload"]["node_id"] for e in it.events("node_completed")] == [
        "slices.implementation",
        "slices",
        "after",
    ]


async def test_a_fork_over_the_storage_limit_files_no_children(item_on, planned):
    it = await _item(item_on, planned)
    launch = LaunchContext(
        repo_entry=entry_of({"setup_command": ""}),
        slice_room=lambda n: f"{n} new worktrees would not fit",
    )

    status = await executor.run_once(it.database, it.run_dirs, work_item_id=it.id, launch=launch)

    assert status == "needs_human"
    assert _children(it) == []
    assert it.database.read(lambda c: store.group_state(c, it.id)) is None or (
        it.database.read(lambda c: store.group_state(c, it.id)).phase != "forked"
    )


async def test_a_completed_child_pins_its_head(item_on, repo):
    it = await item_on([_body("implementation")])
    fork = git(repo, "rev-parse", "HEAD")

    def child(c):
        c.execute(
            "UPDATE work_items SET parent_id = 'p1', slice_generation = 1, slice_key = 1, "
            "base_ref = ? WHERE id = ?",
            (fork, it.id),
        )

    await it.database.write(child)

    assert await _walk(it) == "completed"

    assert it.row()["pinned_head"] == git(it.worktree, "rev-parse", "HEAD")


async def test_the_work_brief_names_what_each_slice_skipped(item_on, repo):
    from kraft.executor import prompts
    from kraft.templates.models import AgentTask

    it = await item_on([_body("implementation")])
    state = store.GroupState(
        group="slices", phase="joined", generation=1, skipped={2: ["skipped verification"]}
    )
    await it.database.write(lambda c: store.set_group_state(c, it.id, state))
    brief = AgentTask.model_validate(
        {
            "id": "w",
            "kind": "agent",
            "harness": "claude",
            "prompt": "p",
            "produces": "work_brief",
            "skill": "kraft:work-brief",
        }
    )
    other = brief.model_copy(update={"produces": "spec"})

    note = prompts.slice_skips_note(brief, it.row(), it.database)

    assert "slice 2: skipped verification" in note
    assert prompts.slice_skips_note(other, it.row(), it.database) == ""


async def test_a_prelude_that_passed_is_not_built_again_when_the_fork_is_retried(item_on, planned):
    """The fork refused (storage), then went on after a retry: the prelude's body
    ran once, not once per entry."""
    write(planned, "plan.md", PLAN.replace("Task 3.", "Task 3.\n").replace("Tasks 1-2.", "Task 1."))
    commit_all(planned, "plan with groundwork")
    it = await _item(item_on, planned)
    refusing = LaunchContext(
        repo_entry=entry_of({"setup_command": ""}), slice_room=lambda n: "no room"
    )
    assert (
        await executor.run_once(it.database, it.run_dirs, work_item_id=it.id, launch=refusing)
        == "needs_human"
    )
    await it.database.write(
        lambda c: c.execute("UPDATE work_items SET status='active' WHERE id=?", (it.id,))
    )

    assert await _walk(it) == "waiting"

    ran = [e["payload"]["node_id"] for e in it.events("node_completed")]
    assert ran == ["slices.implementation", "slices.verification"]
    assert len(_children(it)) == 2


async def test_a_prelude_stopped_in_its_second_node_resumes_there(item_on, planned):
    """The groundwork's tests failed: the retry runs them again, not the
    implementation that passed before them."""
    write(planned, "plan.md", PLAN.replace("Task 3.", "Task 3.\n").replace("Tasks 1-2.", "Task 1."))
    commit_all(planned, "plan with groundwork")
    it = await _item(
        item_on,
        planned,
        _nodes(_body("implementation"), _body("verification", "test -f ../flag")),
    )
    assert await _walk(it) == "needs_human"
    (it.worktree.parent / "flag").write_text("ok")
    await it.database.write(
        lambda c: c.execute("UPDATE work_items SET status='active' WHERE id=?", (it.id,))
    )

    assert await _walk(it) == "waiting"

    ran = [e["payload"]["node_id"] for e in it.events("node_completed")]
    assert ran == ["slices.implementation", "slices.verification"]
    launched = [e["payload"].get("node_id") for e in it.events("worker_session_created")]
    assert launched == ["slices.implementation", "slices.verification", "slices.verification"]


def test_a_group_counts_as_the_execution_node_before_a_gate():
    """A rejection with no named target re-enters at the node that produced what
    the gate is about; for a sliced chain that node is the group."""
    nodes = v1_frozen(
        [
            {
                "id": "slices",
                "kind": "group",
                "nodes": [
                    {
                        "id": "implementation",
                        "kind": "exec",
                        "tasks": [{"id": "build", "kind": "subprocess", "command": "true"}],
                    }
                ],
            },
            {"id": "review", "kind": "gate"},
        ]
    ).nodes
    assert gates_module.preceding_exec_node(nodes, 1) == 0
