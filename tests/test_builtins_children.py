"""A slice's child worktree: cut at its fork commit, with its slice plan in one commit."""

import json

from support import worktree as wtree
from support.harness import commit_all, git, write

from kraft import builtins as kraft_builtins

_PLAN = (
    "## Slices\n\n- **Slice 1: A.** Task 1.\n- **Slice 2: B.** Task 2.\n\n"
    "### Task 1: a\n\n### Task 2: b\n"
)


async def _child(database, run_dirs, repo, fork, key=2):
    """A child of `p1`, cut at `fork`, filed with its slice's documents."""
    from kraft.slices_docs import slice_attachments

    attachments = slice_attachments(
        run_dirs, "c1", spec_text="# spec\n", plan_text=_PLAN, keep=frozenset({key}), repo=repo
    )
    await wtree.make_item(database, repo, wid="p1")
    await wtree.make_item(database, repo, wid="c1", attachments=attachments)

    def link(c):
        c.execute(
            "UPDATE work_items SET parent_id = 'p1', slice_generation = 1, slice_key = ?, "
            "base_ref = ? WHERE id = 'c1'",
            (key, fork),
        )

    await database.write(link)
    return await _child_worktree(database, run_dirs, repo)


def _child_worktree(database, run_dirs, repo):
    """`c1`'s worktree, made with the attachments it was filed with."""
    row = database.read(lambda c: c.execute("SELECT * FROM work_items WHERE id='c1'").fetchone())
    return kraft_builtins.ensure_worktree(
        database,
        run_dirs,
        repo=str(repo),
        work_item_id="c1",
        repo_entry=wtree.NO_SETUP,
        attachments=json.loads(row["attachments"]),
    )


async def test_a_child_worktree_starts_at_its_fork_commit(database, run_dirs, repo):
    fork = git(repo, "rev-parse", "HEAD")
    write(repo, "moved.txt", "the repo moves on after the fork")
    commit_all(repo, "moved")

    worktree = await _child(database, run_dirs, repo, fork)

    assert git(worktree, "rev-parse", "HEAD~1") == fork
    assert not (worktree / "moved.txt").exists()


async def test_a_child_worktree_holds_its_slice_plan_in_one_recorded_commit(
    database, run_dirs, repo
):
    from kraft import progress

    fork = git(repo, "rev-parse", "HEAD")

    worktree = await _child(database, run_dirs, repo, fork, key=2)

    row = database.read(lambda c: c.execute("SELECT * FROM work_items WHERE id='c1'").fetchone())
    assert row["docs_commit"] == git(worktree, "rev-parse", "HEAD")
    assert git(worktree, "rev-list", "--count", f"{fork}..HEAD") == "1"
    tasks = progress.read_tasks(run_dirs, row, worktree)
    assert [done for _, done in tasks] == [True, False]
    assert not (worktree / ".engineering" / "plans").exists()


async def test_a_child_with_nothing_to_copy_records_the_fork_commit_as_its_docs_commit(
    database, run_dirs, repo
):
    fork = git(repo, "rev-parse", "HEAD")
    await wtree.make_item(database, repo, wid="p1")
    await wtree.make_item(database, repo, wid="c1")
    await database.write(
        lambda c: c.execute(
            "UPDATE work_items SET parent_id = 'p1', slice_generation = 1, slice_key = 1, "
            "base_ref = ? WHERE id = 'c1'",
            (fork,),
        )
    )

    await kraft_builtins.ensure_worktree(
        database, run_dirs, repo=str(repo), work_item_id="c1", repo_entry=wtree.NO_SETUP
    )

    row = database.read(
        lambda c: c.execute("SELECT docs_commit FROM work_items WHERE id='c1'").fetchone()
    )
    assert row["docs_commit"] == fork


async def test_a_child_worktree_made_again_keeps_its_first_docs_commit(database, run_dirs, repo):
    """A worktree removed and made again checks the child's branch out with its
    work at HEAD: that is not the commit the replay starts after."""
    fork = git(repo, "rev-parse", "HEAD")
    worktree = await _child(database, run_dirs, repo, fork)
    docs = git(worktree, "rev-parse", "HEAD")
    write(worktree, "work.py", "work\n")
    commit_all(worktree, "Task 2: work")
    git(repo, "worktree", "remove", "--force", str(worktree))

    again = await _child_worktree(database, run_dirs, repo)

    assert git(again, "rev-parse", "HEAD") != docs
    row = database.read(
        lambda c: c.execute("SELECT docs_commit FROM work_items WHERE id='c1'").fetchone()
    )
    assert row["docs_commit"] == docs
