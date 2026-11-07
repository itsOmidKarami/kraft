from __future__ import annotations

from kraft import item_paths
from kraft.slices_docs import slice_attachments, worktree_path

_PLAN = (
    "## Slices\n\n- **Slice 1: A.** Task 1.\n- **Slice 2: B.** Task 2.\n\n"
    "### Task 1: a\n\n### Task 2: b\n"
)


def test_slice_attachment_paths_do_not_exist_at_the_fork_commit(run_dirs, repo):
    records = slice_attachments(
        run_dirs, "c1", spec_text="# spec\n", plan_text=_PLAN, keep=frozenset({1}), repo=repo
    )

    assert [r["kind"] for r in records] == ["spec", "plan"]
    for record in records:
        assert not (repo / record["path"]).exists()
        assert record["path"] == worktree_path("c1", record["kind"])
        assert record["source"].startswith(str(item_paths.filed_dir(run_dirs, "c1")))
    plan = next(r for r in records if r["kind"] == "plan")
    text = open(plan["source"]).read()
    assert "## Slices" not in text
    assert "### Task 2: b [DONE]" in text


def test_a_child_without_a_spec_is_filed_with_its_plan_only(run_dirs, repo):
    records = slice_attachments(
        run_dirs, "c1", spec_text=None, plan_text=_PLAN, keep=frozenset({1}), repo=repo
    )

    assert [r["kind"] for r in records] == ["plan"]
