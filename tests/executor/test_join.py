"""The join: replaying a group's completed children onto the parent's branch."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import worktree as wtree
from support.harness import git, write

from kraft import store
from kraft.executor import join

NODE = SimpleNamespace(id="slices")


class Family:
    """A parent `p1` with a worktree on its own branch, and children cut from the
    same fork commit, each in a worktree of its own."""

    def __init__(self, database, run_dirs, repo, tmp_path):
        self.database, self.run_dirs, self.repo, self.tmp = database, run_dirs, repo, tmp_path
        self.fork = git(repo, "rev-parse", "HEAD")
        self.children: dict[int, dict] = {}

    async def parent(self):
        await wtree.make_item(self.database, self.repo, wid="p1")
        self.branch = wtree.branch(self.database, "p1")
        self.worktree = self.run_dirs.worktrees / "p1"
        git(self.repo, "worktree", "add", "-q", "-b", self.branch, str(self.worktree), self.fork)
        write(self.worktree, "parent_plan.md", "the parent's plan\n")
        git(self.worktree, "add", "-A")
        git(self.worktree, "commit", "-qm", "parent plan")
        self.plan_head = git(self.worktree, "rev-parse", "HEAD")
        return self

    async def child(
        self, key: int, commits: dict[str, str] | list[dict[str, str]], *, status="completed"
    ):
        wid = f"c{key}"
        await wtree.make_item(self.database, self.repo, wid=wid, title=f"slice {key}")
        branch = wtree.branch(self.database, wid)
        path = self.tmp / f"child-{key}"
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(path), self.fork)
        write(path, f".engineering/slices/{wid}/plan.md", f"slice {key} plan\n")
        git(path, "add", "-f", "-A")
        git(path, "commit", "-qm", "attach plan")
        docs = git(path, "rev-parse", "HEAD")
        for i, files in enumerate(commits if isinstance(commits, list) else [commits]):
            for name, text in files.items():
                write(path, name, text)
            git(path, "add", "-A")
            git(path, "commit", "-qm", f"Task {key}.{i}: work")
        head = git(path, "rev-parse", "HEAD")

        def link(c):
            c.execute(
                "UPDATE work_items SET parent_id='p1', slice_generation=1, slice_key=?, "
                "docs_commit=?, pinned_head=?, status=? WHERE id=?",
                (key, docs, head, status, wid),
            )

        await self.database.write(link)
        self.children[key] = {"id": wid, "path": path, "docs": docs, "head": head, "branch": branch}
        return self

    async def state(self, **kw):
        state = store.GroupState(
            group="slices", phase="joining", generation=1, fork_commit=self.fork, **kw
        )
        await self.database.write(lambda c: store.set_group_state(c, "p1", state))

    def row(self):
        return self.database.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id='p1'").fetchone()
        )

    async def join(self):
        return await join.join(self.database, self.run_dirs, self.row(), self.worktree, NODE)

    def group_state(self):
        return self.database.read(lambda c: store.group_state(c, "p1"))

    def stop(self):
        row = self.row()
        return row["status"], row["stop_kind"]

    def subjects(self):
        return git(self.worktree, "log", "--format=%s", f"{self.plan_head}..HEAD").splitlines()


@pytest.fixture
async def family(database, run_dirs, repo, tmp_path):
    return await Family(database, run_dirs, repo, tmp_path).parent()


async def test_two_slices_replay_in_slice_order_without_the_docs_commits(family):
    await family.child(2, {"b.py": "b\n"})
    await family.child(1, {"a.py": "a\n"})
    await family.state()

    assert await family.join() == "ok"

    # newest first: slice 1 was applied before slice 2
    assert family.subjects() == ["Task 2.0: work", "Task 1.0: work"]
    assert (family.worktree / "a.py").is_file() and (family.worktree / "b.py").is_file()
    assert (family.worktree / "parent_plan.md").read_text() == "the parent's plan\n"
    assert not (family.worktree / ".engineering" / "slices").exists()
    body = git(family.worktree, "log", "-1", "--format=%B", "HEAD")
    assert "(cherry picked from commit" in body
    assert family.group_state().phase == "joined"


async def test_a_commit_two_slices_both_made_is_kept_empty(family):
    await family.child(1, {"shared.py": "x\n"})
    await family.child(2, {"shared.py": "x\n"})
    await family.state()

    assert await family.join() == "ok"

    assert family.subjects() == ["Task 2.0: work", "Task 1.0: work"]
    assert git(family.worktree, "show", "--stat", "--format=", "HEAD") == ""


async def test_a_child_commit_with_a_forged_cherry_pick_line_is_refused(family):
    await family.child(1, {"a.py": "a\n"})
    path = family.children[1]["path"]
    write(path, "b.py", "b\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "sneaky\n\n(cherry picked from commit " + "a" * 40 + ")")
    head = git(path, "rev-parse", "HEAD")
    await family.database.write(
        lambda c: c.execute("UPDATE work_items SET pinned_head=? WHERE id='c1'", (head,))
    )
    await family.state()

    assert await family.join() == "needs_human"

    assert family.subjects() == []


async def test_a_child_head_not_descending_from_the_fork_is_refused(family):
    await family.child(1, {"a.py": "a\n"})
    empty = subprocess.run(
        ["git", "mktree"], input="", cwd=family.repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    stranger = git(family.repo, "commit-tree", empty, "-m", "unrelated")
    await family.database.write(
        lambda c: c.execute("UPDATE work_items SET pinned_head=? WHERE id='c1'", (stranger,))
    )
    await family.state()

    assert await family.join() == "needs_human"
    assert family.subjects() == []


async def test_a_merge_commit_in_a_child_is_refused(family):
    await family.child(1, {"a.py": "a\n"})
    path = family.children[1]["path"]
    git(path, "checkout", "-q", "-b", "side", family.fork)
    write(path, "side.py", "s\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "side")
    git(path, "checkout", "-q", family.children[1]["branch"])
    git(path, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    head = git(path, "rev-parse", "HEAD")
    await family.database.write(
        lambda c: c.execute("UPDATE work_items SET pinned_head=? WHERE id='c1'", (head,))
    )
    await family.state()

    assert await family.join() == "needs_human"
    assert family.subjects() == []


async def test_a_child_branch_moved_after_completion_is_refused(family):
    await family.child(1, {"a.py": "a\n"})
    path = family.children[1]["path"]
    write(path, "late.py", "late\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "after the pin")
    await family.state()

    assert await family.join() == "needs_human"
    assert family.subjects() == []


async def test_a_moved_lfsconfig_in_a_child_stops_the_join(family):
    await family.child(1, {".lfsconfig": "[lfs]\n\turl = https://evil.example/lfs\n"})
    await family.state()

    assert await family.join() == "needs_human"
    assert family.subjects() == []


async def _conflicting(family):
    """Slice 1 and 2 both rewrite `shared.py`, differently."""
    await family.child(1, {"shared.py": "one\n"})
    await family.child(2, {"shared.py": "two\n"})
    await family.state()


async def test_a_conflict_hands_off_and_never_resets_on_retry(family):
    await _conflicting(family)

    assert await family.join() == "needs_human"

    assert family.stop() == ("needs_human", "conflict")
    state = family.group_state()
    assert (state.slice_phase, state.joined_through) == ("handed_off", 1)
    # put back at slice 1's end, clean
    assert family.subjects() == ["Task 1.0: work"]
    assert git(family.worktree, "status", "--porcelain") == ""

    # A person resolves it by hand, with the steps the stop gave.
    child = family.children[2]
    subprocess.run(
        [
            "git",
            "cherry-pick",
            "-x",
            "--keep-redundant-commits",
            f"{child['docs']}..{child['head']}",
        ],
        cwd=family.worktree,
        capture_output=True,
    )
    write(family.worktree, "shared.py", "merged\n")
    git(family.worktree, "add", "-A")
    git(family.worktree, "-c", "core.editor=true", "cherry-pick", "--continue")
    await family.database.write(
        lambda c: c.execute("UPDATE work_items SET status='active' WHERE id='p1'")
    )

    assert await family.join() == "ok"

    assert (family.worktree / "shared.py").read_text() == "merged\n"
    assert len(family.subjects()) == 2
    assert family.group_state().phase == "joined"


async def test_a_handed_off_slice_that_is_still_missing_stops_again(family):
    await _conflicting(family)
    await family.join()
    await family.database.write(
        lambda c: c.execute("UPDATE work_items SET status='active' WHERE id='p1'")
    )

    assert await family.join() == "needs_human"

    assert family.stop() == ("needs_human", "conflict")
    assert family.subjects() == ["Task 1.0: work"]


async def test_a_crash_while_picking_puts_the_parent_back_and_picks_again(family):
    await family.child(1, {"a.py": "a\n"})
    await family.child(2, {"shared.py": "two\n"})
    await family.state()
    at = git(family.worktree, "rev-parse", "HEAD")
    # The server died inside slice 1's pick: an operation left behind, a dirty tree.
    child = family.children[1]
    git(family.worktree, "cherry-pick", "-x", f"{child['docs']}..{child['head']}")
    gitdir = Path(git(family.worktree, "rev-parse", "--absolute-git-dir"))
    (gitdir / "CHERRY_PICK_HEAD").write_text(child["head"] + "\n")
    (gitdir / "sequencer").mkdir()
    write(family.worktree, "junk.py", "dirty\n")
    git(family.worktree, "add", "junk.py")
    await family.state(slice_phase="picking", at_commit=at, recorded={1: [child["head"]]})

    status = await family.join()

    assert status == "ok", family.database.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE type='work_item_needs_human' ORDER BY seq DESC"
        ).fetchone()
    )[0]
    assert family.subjects() == ["Task 2.0: work", "Task 1.0: work"]
    assert not (family.worktree / "junk.py").exists()
    assert not (gitdir / "CHERRY_PICK_HEAD").exists()


async def test_put_back_does_not_follow_a_planted_sequencer_symlink(family, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("precious")
    gitdir = Path(git(family.worktree, "rev-parse", "--absolute-git-dir"))
    (gitdir / "sequencer").symlink_to(victim)
    (gitdir / "CHERRY_PICK_HEAD").symlink_to(victim / "keep.txt")

    join.put_back(family.worktree, family.branch, family.plan_head)

    assert (victim / "keep.txt").read_text() == "precious"
    assert not (gitdir / "sequencer").is_symlink()
    assert not (gitdir / "CHERRY_PICK_HEAD").is_symlink()


async def test_every_slice_dropped_stops_for_a_person(family):
    await family.child(1, {"a.py": "a\n"}, status="abandoned")
    await family.child(2, {"b.py": "b\n"}, status="abandoned")
    await family.state()

    assert await family.join() == "needs_human"

    assert family.stop()[0] == "needs_human"
    assert family.subjects() == []


async def test_after_the_join_replayed_branches_go_and_a_cancelled_childs_stays(family):
    await family.child(1, {"a.py": "a\n"})
    await family.child(2, {"b.py": "b\n"}, status="abandoned")
    await family.state()

    assert await family.join() == "ok"

    refs = git(family.repo, "branch", "--list", "--format=%(refname:short)").split()
    assert family.children[1]["branch"] not in refs
    assert family.children[2]["branch"] in refs
    events = [
        e["type"]
        for e in family.database.read(
            lambda c: c.execute("SELECT type FROM events WHERE work_item_id='p1'").fetchall()
        )
    ]
    assert "slices_joined" in events and "slice_dropped" in events
