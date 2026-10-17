"""The pure half of implementation progress: what a plan's tasks are, what
commits say, and how the two signals combine -- and `for_detail`, the detail
read's walk-dependent half, over a real item and worktree. The rest of the
DB/git half is exercised through the API in tests/api/test_progress.py."""

from __future__ import annotations

import json
import os

import pytest
from pydantic import ValidationError
from support.harness import write

from kraft import item_paths, progress
from kraft.progress import ProgressReport, TaskProgress

PLAN = """# Land it

## Before you start

## Task 1 — the repo stops forbidding commits (Kraft-f3mv)

### The failing test

## Task 2: one commit contract

### Task 3. open_mr refuses a dirty worktree

## Full verification
"""


def test_parse_tasks_reads_both_heading_depths_and_separators():
    assert progress.parse_tasks(PLAN) == [
        ("the repo stops forbidding commits (Kraft-f3mv)", False),
        ("one commit contract", False),
        ("open_mr refuses a dirty worktree", False),
    ]


def test_parse_tasks_without_task_headings_is_empty():
    assert progress.parse_tasks("# Plan\n\n## Step one\n- [ ] do it\n") == []


def test_a_bare_task_heading_has_an_empty_title_and_does_not_eat_the_next_line():
    assert progress.parse_tasks("## Task 1\nbody text\n## Task 2 — b\n") == [
        ("", False),
        ("b", False),
    ]


def test_parse_tasks_skips_headings_inside_a_fenced_code_block():
    plan = f"""# Land it

## Task 1 — real task

The fixture below is example text for a test, not real plan structure:

```
{PLAN}
```

## Task 2 — also real
"""
    assert progress.parse_tasks(plan) == [
        ("real task", False),
        ("also real", False),
    ]


def test_parse_tasks_reads_the_done_marker():
    plan = "## Task 1 — old work [DONE]\n## Task 2 — new work\n"
    assert progress.parse_tasks(plan) == [
        ("old work", True),
        ("new work", False),
    ]


def test_parse_tasks_done_marker_is_exact_not_a_word_match():
    plan = "## Task 1 — mark request as done\n"
    assert progress.parse_tasks(plan) == [("mark request as done", False)]


def test_parse_tasks_done_marker_strips_trailing_space_before_it():
    plan = "## Task 1 — old work   [DONE]\n"
    assert progress.parse_tasks(plan) == [("old work", True)]


def test_committed_task_takes_the_highest_task_number_named():
    subjects = [
        "Task 7: branch name computed once (Kraft-nhps)",
        "Task 5+6: forge.find_mr",
        "agent: per-node permission grant (task 6, Kraft-3tw)",
        "fmt: ruff format",
    ]
    assert progress.committed_task(subjects) == 7


def test_committed_task_reads_a_joined_pair():
    assert progress.committed_task(["Task 5+6: forge.find_mr"]) == 6


def test_committed_task_ignores_words_that_merely_end_in_task():
    assert progress.committed_task(["subtask 4 renamed", "Plan: x"]) == 0


TASKS = [("parse", False), ("serve", False), ("render", False)]


def test_combine_with_no_signal_is_task_one():
    p = progress.combine(TASKS, reported=0, committed=0)
    assert (p.current, p.total, p.title) == (1, 3, "parse")
    assert [t.state for t in p.tasks] == ["current", "pending", "pending"]
    assert [t.n for t in p.tasks] == [1, 2, 3]


def test_combine_follows_the_agent_report():
    assert progress.combine(TASKS, reported=2, committed=0).current == 2


def test_a_committed_task_moves_current_past_it():
    assert progress.combine(TASKS, reported=1, committed=2).current == 3


def test_combine_caps_at_the_last_task():
    p = progress.combine(TASKS, reported=0, committed=9)
    assert p.current == 3
    assert [t.state for t in p.tasks] == ["done", "done", "current"]


def test_combine_without_tasks_is_none():
    assert progress.combine([], reported=2, committed=1) is None


def test_combine_clamps_current_off_a_done_marked_task():
    tasks = [("a", False)] * 6 + [("g", True), ("h", True), ("i", True), ("j", True)]
    # reported=0, committed=6 -> raw current would be 7, but task 7 is [DONE]
    p = progress.combine(tasks, reported=0, committed=6)
    assert p.current == 6
    assert p.title == "a"


def test_combine_marks_a_done_task_done_regardless_of_position():
    tasks = [("a", False), ("b", True), ("c", False)]
    p = progress.combine(tasks, reported=1, committed=0)
    assert [t.state for t in p.tasks] == ["current", "done", "pending"]


def test_combine_clamp_stops_at_task_one():
    tasks = [("a", True), ("b", True)]
    p = progress.combine(tasks, reported=0, committed=1)
    assert p.current == 1
    assert [t.state for t in p.tasks] == ["done", "done"]


def test_current_cannot_exceed_total():
    """The invariant the docstring asserts is now enforced, not just claimed."""
    with pytest.raises(ValidationError, match="current task index cannot exceed total"):
        ProgressReport(
            current=4, total=3, title="x", tasks=[TaskProgress(n=1, title="a", state="current")]
        )


def test_implementation_node_is_found_by_hook_not_name():
    chain = {
        "nodes": [
            {"id": "env", "tasks": ["on.env.prepare"]},
            {"id": "build", "tasks": ["on.implementation.start", "on.repos.scan"]},
        ]
    }
    assert progress.implementation_node(chain) == "build"


def test_a_chain_without_the_hook_has_no_implementation_node():
    assert (
        progress.implementation_node({"nodes": [{"id": "verify", "tasks": ["on.test.run"]}]})
        is None
    )


def _seeded_default_chain():
    from pathlib import Path

    from kraft.templates.library import TemplateLibrary

    root = Path(__file__).resolve().parents[1] / "config"
    return TemplateLibrary.from_yaml_dir(root).resolve_chain("default")


def test_the_seeded_default_chain_has_exactly_one_implementing_node():
    """A repair task in a fix loop or `on_failure` pass is an agent task with
    no skill too (`merge_request_feedback`'s), but it repairs a node's output,
    it is not the node's work -- so only a node's own steps count."""
    assert progress.implementing_nodes(_seeded_default_chain()) == ["implementation"]


def test_the_implementing_node_does_not_depend_on_node_order():
    import dataclasses

    chain = _seeded_default_chain()
    reversed_chain = dataclasses.replace(chain, nodes=tuple(reversed(chain.nodes)))
    assert progress.v1_implementation_node(reversed_chain) == "implementation"


def test_two_implementing_nodes_is_said_out_loud(caplog):
    import dataclasses

    chain = _seeded_default_chain()
    impl = next(n for n in chain.nodes if n.id == "implementation")
    doubled = dataclasses.replace(
        chain, nodes=(*chain.nodes, dataclasses.replace(impl, id="implementation_again"))
    )
    with caplog.at_level("WARNING", logger="kraft.progress"):
        assert progress.v1_implementation_node(doubled) == "implementation"
    assert "2 implementing nodes (implementation, implementation_again)" in caplog.text


def _row(attachments: list[dict]) -> dict:
    return {"id": "w1", "attachments": json.dumps(attachments)}


@pytest.mark.parametrize(
    ("attached", "written", "title"),
    [
        ("plan", [".engineering/plans/w1.md", "items/w1/docs/plan.md"], "attached"),
        ("spec", [".engineering/plans/w1.md"], "old"),
        ("spec", [".engineering/plans/w1.md", "items/w1/docs/plan.md"], "new"),
    ],
    ids=["the-attachment-wins", "older-worktree", "docs-before-older-worktree"],
)
def test_read_tasks_prefers_the_plan_attachment(tmp_path, run_dirs, attached, written, title):
    worktree = tmp_path / "wt"
    write(worktree, "docs/p.md", "## Task 1 — attached\n")
    for rel in written:
        base = run_dirs.base if rel.startswith("items/") else worktree
        write(base, rel, f"## Task 1 — {'new' if rel.startswith('items/') else 'old'}\n")

    rows = [{"kind": attached, "path": "docs/p.md"}]
    assert progress.read_tasks(run_dirs, _row(rows), worktree) == [(title, False)]


@pytest.mark.parametrize("committed", [True, False], ids=["on-its-branch", "filed-copy"])
def test_an_in_place_item_reads_its_plan_and_commits_from_its_branch(tmp_path, run_dirs, committed):
    """The checkout is on main and holds the person's own copy of the plan: an
    in-place item's tasks are the plan on its branch, else the copy filed at
    intake, and its commits are its branch's, not the checkout's HEAD's."""
    from support.harness import commit_all, git, make_repo

    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "kraft/w1")
    if committed:
        write(repo, "docs/p.md", "## Task 1 — on the branch\n")
    tip = commit_all(repo, "Task 1: the item's commit")
    git(repo, "checkout", "-q", "main")
    write(repo, "docs/p.md", "## Task 1 — the person's\n")
    write(item_paths.filed_dir(run_dirs, "w1"), "p.md", "## Task 1 — filed\n")
    plan = {"kind": "plan", "path": "docs/p.md", "source": "/old/filed/p.md"}
    row = {**_row([plan]), "branch": "kraft/w1", "base_ref": base}

    tasks = progress.read_tasks(run_dirs, row, repo, in_place=True)
    log = progress.branch_log(row, repo, in_place=True)

    assert tasks == [("on the branch" if committed else "filed", False)]
    assert log == [(tip[:7], "Task 1: the item's commit")]


@pytest.mark.parametrize("planted", [None, "symlink", "fifo"], ids=["none", "symlink", "fifo"])
def test_read_tasks_without_a_plan_file_is_empty(tmp_path, run_dirs, planted):
    """A worker writes `docs/`: a link there is not followed, and a FIFO does
    not hang the board."""
    plan = item_paths.docs_dir(run_dirs, "w1") / "plan.md"
    if planted:
        plan.parent.mkdir(parents=True)
    if planted == "symlink":
        plan.symlink_to(write(tmp_path, "secret", "## Task 1 — host file\n"))
    elif planted == "fifo":
        os.mkfifo(plan)
    assert progress.read_tasks(run_dirs, _row([]), tmp_path / "wt") == []


def _ev(type_: str, node_id: str | None = None) -> dict:
    return {"type": type_, "payload": {"node_id": node_id} if node_id else {"gate": "g"}}


def test_a_first_run_is_not_rework():
    evs = [
        _ev("node_started", "plan"),
        _ev("node_completed", "plan"),
        _ev("node_started", "implementation"),
    ]
    assert not progress.is_rework(evs, "implementation")


def test_a_rejection_straight_back_to_implementation_is_rework():
    """Kraft-hj2q9: the re-run works from the rejection note, not the plan."""
    evs = [
        _ev("node_started", "implementation"),
        _ev("node_completed", "implementation"),
        _ev("node_started", "verify"),
        _ev("gate_rejected"),
        _ev("node_started", "implementation"),
    ]
    assert progress.is_rework(evs, "implementation")


def test_a_rejection_back_to_the_plan_node_follows_the_plan_again():
    """Re-entered at or before the plan: the plan may be rewritten, and this run follows it."""
    evs = [
        _ev("node_started", "implementation"),
        _ev("node_completed", "implementation"),
        _ev("gate_rejected"),
        _ev("node_started", "plan"),
        _ev("node_completed", "plan"),
        _ev("node_started", "implementation"),
    ]
    assert not progress.is_rework(evs, "implementation")


def test_a_re_entry_without_a_rejection_is_not_rework():
    """A retry or a resume re-enters the node with no rejection: the plan still applies."""
    evs = [
        _ev("node_started", "implementation"),
        _ev("node_completed", "implementation"),
        _ev("node_started", "implementation"),
    ]
    assert not progress.is_rework(evs, "implementation")


#: The walk each `for_detail` row stands on: `+` enters a node, `.` completes
#: it, `!` rejects a gate.
_WALKS = {
    "never_started": [],
    "rewriting_the_plan": [
        "+implementation",
        ".implementation",
        "!",
        "+plan",
    ],
    "stopped_on_the_node": ["+implementation"],
    "node_completed": ["+implementation", ".implementation", "+verification"],
    "rework": ["+implementation", ".implementation", "!", "+implementation"],
}


@pytest.mark.parametrize(
    ("walk", "current", "states"),
    [
        ("never_started", None, None),
        ("rewriting_the_plan", None, None),
        ("stopped_on_the_node", 2, ["done", "current", "pending"]),
        ("node_completed", 3, ["done", "done", "done"]),
        ("rework", 3, ["done", "done", "done"]),
    ],
    ids=list(_WALKS),
)
async def test_for_detail_follows_the_implementing_node_past_its_run(
    walk, current, states, item_on, templates_dir, database
):
    """The detail's `progress` outlives the running node: the live position
    while the item is stopped on it (here, waiting on a person), every task
    done once it completed or a rejection sent it back for rework, and nothing
    before it started or while the plan is being rewritten. Each task carries
    the newest commit naming it."""
    from support.harness import git, v1_named_chain

    from kraft import events, store

    it = await item_on(v1_named_chain(templates_dir, "default"), status="needs_human")
    git(it.repo, "worktree", "add", "-q", "-b", it.id, str(it.worktree))
    base = git(it.worktree, "rev-parse", "HEAD")
    git(it.worktree, "commit", "-q", "--allow-empty", "-m", "feat: Task 1: parse")
    git(it.worktree, "commit", "-q", "--allow-empty", "-m", "fix: task 1 again")
    named = git(it.worktree, "rev-parse", "--short", "HEAD")
    git(it.worktree, "commit", "-q", "--allow-empty", "-m", "fmt: tidy")
    tasks = "# p\n\n## Task 1 — parse\n\n## Task 2 — serve\n\n## Task 3 — render\n"
    write(it.run_dirs.base, item_paths.document_name(it.id, "plan"), tasks)
    await database.write(
        lambda c: c.execute("UPDATE work_items SET base_ref = ? WHERE id = ?", (base, it.id))
    )
    for step in _WALKS[walk]:
        op, node = step[0], step[1:]
        if op == "+":
            await database.write(lambda c, n=node: store.enter_node(c, it.id, n))
        elif op == ".":
            await database.write(lambda c, n=node: store.complete_node(c, it.id, n))
        else:
            await database.write(
                lambda c: events.append(c, it.id, "gate_rejected", {"gate": "g", "note": "n"})
            )

    p = progress.for_detail(database, it.row(), it.worktree, it.run_dirs)

    if states is None:
        assert p is None
        return
    assert [t.state for t in p.tasks] == states
    assert (p.current, p.total) == (current, 3)
    assert [t.sha for t in p.tasks] == [named, None, None]
