"""The pure half of implementation progress: what a plan's tasks are, what
commits say, and how the two signals combine. The DB/git half is exercised
through the API in test_progress_api.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kraft import progress
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

    root = Path(__file__).resolve().parents[1] / "templates"
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


def test_read_tasks_prefers_the_plan_attachment(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "p.md").write_text("## Task 1 — attached\n")
    artifact = tmp_path / ".engineering" / "plans" / "w1.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("## Task 1 — artifact\n")
    assert progress.read_tasks(tmp_path, [{"kind": "plan", "path": "docs/p.md"}], "w1") == [
        ("attached", False)
    ]
    assert progress.read_tasks(tmp_path, [{"kind": "spec", "path": "docs/p.md"}], "w1") == [
        ("artifact", False)
    ]


def test_read_tasks_without_a_plan_file_is_empty(tmp_path):
    assert progress.read_tasks(tmp_path, [], "w1") == []
