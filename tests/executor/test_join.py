"""The join: replaying a group's completed children onto the parent's branch."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import worktree as wtree
from support.harness import git, write

from kraft import events, store
from kraft.executor import join
from kraft.vocab import WorkItemEvent

NODE = SimpleNamespace(id="slices", on_conflict=())


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
            git(path, "commit", "-q", "--allow-empty", "-m", f"Task {key}.{i}: work")
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

    async def join(self, node=NODE):
        return await join.join(self.database, self.run_dirs, self.row(), self.worktree, node)

    def group_state(self):
        return self.database.read(lambda c: store.group_state(c, "p1"))

    def stop(self):
        row = self.row()
        return row["status"], row["stop_kind"]

    def reason(self) -> str:
        """The reason the parent's latest stop gave a person."""
        found = self.database.read(
            lambda c: c.execute(
                "SELECT payload FROM events WHERE work_item_id='p1' "
                "AND type='work_item_needs_human' ORDER BY seq DESC"
            ).fetchone()
        )
        return json.loads(found["payload"])["reason"]

    async def pin(self, key: int, head: str):
        await self.database.write(
            lambda c: c.execute("UPDATE work_items SET pinned_head=? WHERE id=?", (head, f"c{key}"))
        )

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


@pytest.mark.parametrize(
    "second",
    [
        pytest.param({"shared.py": "x\n"}, id="emptied-by-the-replay"),
        pytest.param({}, id="empty-in-the-child"),
    ],
)
async def test_a_commit_two_slices_both_made_is_kept_empty(family, second):
    await family.child(1, {"shared.py": "x\n"})
    await family.child(2, second)
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
    await family.pin(1, head)
    await family.state()

    assert await family.join() == "needs_human"

    assert family.subjects() == []


async def test_a_child_head_not_descending_from_the_fork_is_refused(family):
    await family.child(1, {"a.py": "a\n"})
    empty = subprocess.run(
        ["git", "mktree"], input="", cwd=family.repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    stranger = git(family.repo, "commit-tree", empty, "-m", "unrelated")
    # The branch moves with the pin, so the moved-branch check passes.
    git(family.repo, "update-ref", "refs/heads/" + family.children[1]["branch"], stranger)
    await family.pin(1, stranger)
    await family.state()

    assert await family.join() == "needs_human"
    assert family.subjects() == []
    assert "c1" in family.reason() and "fork" not in family.reason()


async def test_a_child_branch_reset_past_its_docs_commit_is_refused(family):
    """Squashed onto the fork commit, the child's head no longer holds its docs
    commit, and `docs..head` would replay its slice plan into the parent."""
    await family.child(1, {"a.py": "a\n"})
    path = family.children[1]["path"]
    git(path, "reset", "-q", "--soft", family.fork)
    git(path, "commit", "-qm", "Task 1: squashed")
    await family.pin(1, git(path, "rev-parse", "HEAD"))
    await family.state()

    assert await family.join() == "needs_human"

    assert family.subjects() == []
    assert "c1" in family.reason() and "reset or squashed" in family.reason()
    assert not (family.worktree / ".engineering" / "slices").exists()


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
    await family.pin(1, head)
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


async def _resolve_by_hand(family):
    """A person resolves slice 2 with the steps the stop gave, then retries."""
    child = family.children[2]
    subprocess.run(
        [
            "git",
            "cherry-pick",
            "-x",
            "--allow-empty",
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


async def test_a_conflict_hands_off_and_never_resets_on_retry(family):
    await _conflicting(family)

    assert await family.join() == "needs_human"

    assert family.stop() == ("needs_human", "conflict")
    state = family.group_state()
    assert (state.slice_phase, state.joined_through) == ("handed_off", 1)
    # put back at slice 1's end, clean
    assert family.subjects() == ["Task 1.0: work"]
    assert git(family.worktree, "status", "--porcelain") == ""

    await _resolve_by_hand(family)

    assert await family.join() == "ok"

    assert (family.worktree / "shared.py").read_text() == "merged\n"
    assert len(family.subjects()) == 2
    assert family.group_state().phase == "joined"


#: A group whose library had `resolve_join_conflict`; the handler itself is faked
#: at `dispatch.run_recovery`, the seam every recovery handler runs through.
HANDLED = SimpleNamespace(id="slices", on_conflict=("resolve_join_conflict",))


def _handler(monkeypatch, family, act, verdict):
    """Fake the handler: it does `act` in the parent's worktree and answers
    `verdict`, after the straggler sweep every agent task gets, at the handler
    task's own path. Returns the notes it was sent; `family.phases` gets the
    slice phase each turn ran under."""
    from kraft.executor import dispatch

    notes = []
    family.phases = []

    async def run_recovery(db, run_dirs, wid, node, row, worktree, *, handler, note, **kw):
        assert (wid, Path(worktree), handler) == ("p1", family.worktree, HANDLED.on_conflict)
        notes.append(note)
        family.phases.append(family.group_state().slice_phase)
        await act(family)
        await dispatch.sweep_stragglers(
            db,
            row,
            worktree,
            node_id="slices",
            task_path="slices.on_conflict.main.resolve_join_conflict",
            launch=None,
            sandboxed=False,
            run_dirs=run_dirs,
        )
        return verdict, [], []

    monkeypatch.setattr(dispatch, "run_recovery", run_recovery)
    return notes


async def test_the_conflict_handler_resolves_and_the_join_goes_on(family, monkeypatch):
    await _conflicting(family)
    await family.child(3, {"other.py": "three\n"})
    notes = _handler(monkeypatch, family, _resolve_by_hand, "ok")

    assert await family.join(HANDLED) == "ok"

    assert (family.worktree / "shared.py").read_text() == "merged\n"
    assert family.subjects() == ["Task 3.0: work", "Task 2.0: work", "Task 1.0: work"]
    state = family.group_state()
    assert (state.phase, state.joined_through) == ("joined", 3)
    [note] = notes
    child = family.children[2]
    assert "slice 2 (c2)" in note and "in shared.py" in note
    assert f"--keep-redundant-commits {child['docs'][:12]}..{child['head'][:12]}" in note
    assert "Never `git commit -m`" in note and "never `--skip`" in note
    assert "Commit only through `git cherry-pick --continue`" in note
    assert "no untracked file" in note


def _ignore_logs(family):
    exclude = Path(git(family.repo, "rev-parse", "--absolute-git-dir"), "info", "exclude")
    exclude.parent.mkdir(exist_ok=True)
    exclude.write_text("*.log\n")


@pytest.mark.parametrize(
    ("leave", "removed", "kept"),
    [
        pytest.param({"shared.py.orig": "<<<<<<<\n"}, ["shared.py.orig"], [], id="untracked"),
        pytest.param({"handler.log": "notes\n"}, [], ["handler.log"], id="ignored-stays"),
    ],
)
async def test_a_handler_that_resolves_but_leaves_files_untracked_joins(
    family, monkeypatch, leave, removed, kept
):
    """No sweep commits what the handler left (that commit would fail the
    applied-check): the join removes what is untracked and not ignored, names
    it, and joins the slice."""
    await _conflicting(family)
    _ignore_logs(family)

    async def resolve_and_leave(family):
        await _resolve_by_hand(family)
        for name, text in leave.items():
            write(family.worktree, name, text)

    _handler(monkeypatch, family, resolve_and_leave, "ok")

    assert await family.join(HANDLED) == "ok"

    assert family.subjects() == ["Task 2.0: work", "Task 1.0: work"], "a wip commit was swept"
    assert [n for n in leave if (family.worktree / n).exists()] == kept
    found = family.database.read(
        lambda c: [
            e["payload"]
            for e in events.read_after(c, 0, "p1")
            if e["type"] == WorkItemEvent.JOIN_LEFTOVERS_REMOVED
        ]
    )
    assert found == ([{"child": "c2", "slice_key": 2, "paths": removed}] if removed else [])
    notes = family.group_state().skipped.get(2, [])
    assert all(any(p in n for n in notes) for p in removed) and (bool(notes) == bool(removed))


@pytest.mark.parametrize(
    ("task_path", "swept"),
    [
        pytest.param("slices.main.implement", True, id="a-task"),
        pytest.param("slices.main.implement.on_failure.main.fix", True, id="a-recovery"),
        pytest.param("slices.on_base_changed.on_conflict.main.fix", True, id="a-rebase-conflict"),
        pytest.param("slices.on_conflict.main.resolve_join_conflict", False, id="the-joins"),
    ],
)
async def test_every_agent_task_is_swept_but_the_joins_conflict_handler(family, task_path, swept):
    """What the join's handler leaves, the join clears and names itself: a
    sweep commit carries no `cherry picked from` line for the applied-check."""
    from kraft.executor import dispatch

    write(family.worktree, "left.txt", "left behind\n")

    await dispatch.sweep_stragglers(
        family.database,
        family.row(),
        family.worktree,
        node_id="slices",
        task_path=task_path,
        launch=None,
        sandboxed=False,
        run_dirs=family.run_dirs,
    )

    assert (git(family.worktree, "rev-parse", "HEAD") != family.plan_head) == swept


async def _start_the_pick(family):
    child = family.children[2]
    subprocess.run(
        ["git", "cherry-pick", "-x", f"{child['docs']}..{child['head']}"],
        cwd=family.worktree,
        capture_output=True,
    )


async def _nothing(family):
    pass


async def _resolve_but_edit_tracked(family):
    """A resolution whose last edit was never committed: maybe part of it, so
    never discarded to get the slice joined."""
    await _resolve_by_hand(family)
    write(family.worktree, "shared.py", "merged, and then some\n")
    write(family.worktree, "shared.py.orig", "<<<<<<<\n")


@pytest.mark.parametrize(
    ("act", "verdict"),
    [
        pytest.param(_start_the_pick, "failed", id="it-fails"),
        pytest.param(_resolve_by_hand, "failed", id="it-fails-having-picked-it-all"),
        pytest.param(_nothing, "ok", id="it-says-ok-having-done-nothing"),
        pytest.param(_start_the_pick, "ok", id="it-says-ok-mid-pick"),
        pytest.param(_resolve_but_edit_tracked, "ok", id="it-says-ok-leaving-a-tracked-edit"),
    ],
)
async def test_a_conflict_the_handler_does_not_join_stops_for_a_person(
    family, monkeypatch, act, verdict
):
    await _conflicting(family)
    _handler(monkeypatch, family, act, verdict)

    assert await family.join(HANDLED) == "needs_human"

    assert family.stop() == ("needs_human", "conflict")
    reason = family.reason()
    assert "conflict handler did not join it" in reason
    named = "it left shared.py.orig, which the join removed" in reason
    assert named == (act is _resolve_but_edit_tracked)
    assert "`git cherry-pick -x --allow-empty --keep-redundant-commits" in reason
    state = family.group_state()
    assert (family.phases, state.slice_phase, state.joined_through) == (
        ["handling"],
        "handed_off",
        1,
    )
    # put back where the join stopped, as a person would have found it with no handler
    assert family.subjects() == ["Task 1.0: work"]
    assert git(family.worktree, "status", "--porcelain") == ""
    gitdir = Path(git(family.worktree, "rev-parse", "--absolute-git-dir"))
    assert not (gitdir / "CHERRY_PICK_HEAD").exists()


async def _cancelled_mid_pick(family):
    """What a pause does mid-handler: it kills the session and cancels the walk,
    so the join never resumes and the worktree stays as the handler left it."""
    await _start_the_pick(family)
    write(family.worktree, "shared.py.orig", "<<<<<<<\n")
    raise asyncio.CancelledError


def _resumed(family):
    return family.database.read(
        lambda c: [
            e["payload"]["outcome"]
            for e in events.read_after(c, 0, "p1")
            if e["type"] == WorkItemEvent.JOIN_HANDLER_RESUMED
        ]
    )


@pytest.mark.parametrize(
    "act",
    [
        pytest.param(_cancelled_mid_pick, id="a-pause-cancels-the-walk"),
        pytest.param(_start_the_pick, id="a-stop-it-answers"),
    ],
)
async def test_a_handler_cut_short_gets_a_fresh_turn_on_resume(family, monkeypatch, act):
    """A pause (or a rate limit, a cap) ends the handler's turn but gives no
    verdict: the slice stays `handling`, and the resume puts the parent back,
    picks the slice again and hands the conflict to the handler afresh."""
    await _conflicting(family)
    _handler(monkeypatch, family, act, "paused")

    if act is _cancelled_mid_pick:
        with pytest.raises(asyncio.CancelledError):
            await family.join(HANDLED)
    else:
        assert await family.join(HANDLED) == "paused"
        assert family.subjects() == ["Task 1.0: work"], "put back where the join stopped"
        assert git(family.worktree, "status", "--porcelain") == ""

    assert family.group_state().slice_phase == "handling"
    notes = _handler(monkeypatch, family, _resolve_by_hand, "ok")

    assert await family.join(HANDLED) == "ok"

    assert (len(notes), family.phases) == (1, ["handling"])
    assert family.subjects() == ["Task 2.0: work", "Task 1.0: work"]
    assert family.row()["status"] != "needs_human"
    assert _resumed(family) == ["rerun"]
    assert not (family.worktree / "shared.py.orig").exists()


async def _crashed_in_the_handler(family, act):
    """The server died while the handler had slice 2: slice 1 joined, the slice
    `handling`, the worktree as `act` and a stray `.orig` left it."""
    await _conflicting(family)
    one, two = family.children[1], family.children[2]
    git(family.worktree, "cherry-pick", "-x", f"{one['docs']}..{one['head']}")
    at = git(family.worktree, "rev-parse", "HEAD")
    recorded = git(family.repo, "rev-list", "--reverse", f"{two['docs']}..{two['head']}").split()
    await family.state(
        joined_through=1, at_commit=at, slice_phase="handling", recorded={2: recorded}
    )
    await act(family)
    write(family.worktree, "shared.py.orig", "<<<<<<<\n")


@pytest.mark.parametrize(
    ("act", "runs", "outcome"),
    [
        pytest.param(_start_the_pick, 1, "rerun", id="mid-pick"),
        pytest.param(_resolve_by_hand, 0, "joined", id="after-it-finished"),
    ],
)
async def test_a_restart_mid_handler_reruns_it_unless_its_work_is_whole(
    family, monkeypatch, act, runs, outcome
):
    await _crashed_in_the_handler(family, act)
    notes = _handler(monkeypatch, family, _resolve_by_hand, "ok")

    assert await family.join(HANDLED) == "ok"

    assert (len(notes), _resumed(family)) == (runs, [outcome])
    assert family.subjects() == ["Task 2.0: work", "Task 1.0: work"]
    assert not (family.worktree / "shared.py.orig").exists()
    gitdir = Path(git(family.worktree, "rev-parse", "--absolute-git-dir"))
    assert not (gitdir / "CHERRY_PICK_HEAD").exists()
    assert family.group_state().phase == "joined"


@pytest.mark.parametrize("cut_short", [False, True], ids=["at-its-verdict", "on-its-resume"])
async def test_a_handler_that_leaves_another_operation_names_where_to_reset(
    family, monkeypatch, cut_short
):
    """`put_back` clears only a cherry-pick: anything else the handler left stops
    the parent with the commit to reset to, since a retry never resets. So too
    when the join finds it on resuming a handler a pause or a restart cut short."""
    await _conflicting(family)
    seen = {}

    async def merge_left(family):
        at = seen["at"] = git(family.worktree, "rev-parse", "HEAD")  # where the join stopped
        await _start_the_pick(family)
        gitdir = Path(git(family.worktree, "rev-parse", "--absolute-git-dir"))
        (gitdir / "MERGE_HEAD").write_text(at + "\n")
        if cut_short:
            raise asyncio.CancelledError

    notes = _handler(monkeypatch, family, merge_left, "failed")
    if cut_short:
        with pytest.raises(asyncio.CancelledError):
            await family.join(HANDLED)

    assert await family.join(HANDLED) == "needs_human"

    assert len(notes) == 1

    assert family.stop() == ("needs_human", "checkout")
    reason = family.reason()
    assert f"`git reset --hard {seen['at'][:12]}` on {family.branch}" in reason
    assert "`git cherry-pick -x --allow-empty --keep-redundant-commits" in reason


async def test_a_handed_off_slice_that_is_still_missing_stops_again(family):
    await _conflicting(family)
    await family.join()
    await family.database.write(
        lambda c: c.execute("UPDATE work_items SET status='active' WHERE id='p1'")
    )

    assert await family.join() == "needs_human"

    assert family.stop() == ("needs_human", "conflict")
    assert family.subjects() == ["Task 1.0: work"]


def _dirty(family):
    write(family.worktree, "shared.py", "half done\n")
    return lambda: (family.worktree / "shared.py").read_text() == "half done\n"


def _operation(family):
    planted = Path(git(family.worktree, "rev-parse", "--absolute-git-dir")) / "CHERRY_PICK_HEAD"
    planted.write_text(family.children[2]["head"] + "\n")
    return planted.exists


def _off_branch(family):
    git(family.worktree, "checkout", "-q", "--detach")
    return lambda: git(family.worktree, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"


@pytest.mark.parametrize(
    "leave",
    [
        pytest.param(_dirty, id="dirty-tree"),
        pytest.param(_operation, id="operation-in-progress"),
        pytest.param(_off_branch, id="head-off-the-branch"),
    ],
)
async def test_a_handed_off_slice_resolved_but_unsettled_stops_without_a_reset(family, leave):
    """Every commit is there, but the worktree is not one the applied-check can
    trust: the parent stops again and leaves it exactly as the person left it."""
    await _conflicting(family)
    await family.join()
    await _resolve_by_hand(family)
    head = git(family.worktree, "rev-parse", "HEAD")
    still_there = leave(family)

    assert await family.join() == "needs_human"

    assert family.stop() == ("needs_human", "conflict")
    assert "slice 2 (c2)" in family.reason()
    assert family.group_state().slice_phase == "handed_off"
    assert git(family.worktree, "rev-parse", "HEAD") == head
    assert still_there()


async def test_a_child_completed_by_hand_before_it_started_has_nothing_to_replay(family):
    await family.child(1, {"a.py": "a\n"})
    await wtree.make_item(family.database, family.repo, wid="c2", title="slice 2")

    def by_hand(c):
        c.execute(
            "UPDATE work_items SET parent_id='p1', slice_generation=1, slice_key=2, "
            "status='completed' WHERE id='c2'"
        )
        events.append(c, "c2", WorkItemEvent.MANUALLY_COMPLETED, {"reason": "not needed"})

    await family.database.write(by_hand)
    await family.state()

    assert await family.join() == "ok"

    assert family.subjects() == ["Task 1.0: work"]
    state = family.group_state()
    assert state.phase == "joined"
    assert state.skipped[2] == ["completed by hand before it started: nothing replayed"]


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
