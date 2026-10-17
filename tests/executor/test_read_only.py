"""`read_only` steps and nodes, verified (Kraft-q2zvw).

A read_only step is checked around all of its tasks together, a read_only node
around its own steps: HEAD, `git status --porcelain=v1 -z` and a hash of
`git diff HEAD`, per repository of the checkout. A change stops the item for a
person naming the files, and is never a failure a recovery or fix loop spends
on. The launch is faked at `kraft.adapters.subprocess.run_task`, and `during`
does what the task would have done to the worktree.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from support.harness import entry_of, git, v1_node, write
from support.workspace import workspace_item

from kraft import store
from kraft.executor import dispatch, walk
from kraft.executor.context import READ_ONLY_VIOLATED, LaunchContext
from kraft.templates import models as tm
from kraft.templates.environment import WorkItemTarget

NO_SETUP = LaunchContext(repo_entry=entry_of({"setup_command": ""}))
_IN_PLACE = WorkItemTarget.for_repository("sample", in_place=True)


def _launches(monkeypatch, during=None) -> list:
    """Fake every launch; `during(cwd)` runs as the task would."""
    launched = []

    async def run_task(*_a, cwd, **kw):
        launched.append(kw["hook_point"])
        if during is not None:
            during(Path(cwd))
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    return launched


def _agent(task_id: str = "review") -> dict:
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": "Review it."}


def _sub(task_id: str) -> dict:
    return {"id": task_id, "kind": "subprocess", "command": "true"}


async def _walk(it) -> str:
    node = it.chain.chain.nodes[0]
    return await walk.walk_node(
        it.database, it.run_dirs, it.id, node, it.row(), it.repo, launch=NO_SETUP
    )


def _reason(it) -> str:
    return it.events("work_item_needs_human")[-1]["payload"]["reason"]


# -- the check -------------------------------------------------------------------------


def _commits(cwd: Path) -> None:
    write(cwd, "calc.py", "edited\n")
    git(cwd, "commit", "-qm", "agent", "--", "calc.py")


@pytest.mark.parametrize(
    ("target", "during"),
    [(None, lambda cwd: write(cwd, "calc.py", "edited\n")), (_IN_PLACE, _commits)],
    ids=["worktree-edit-swept", "in-place-commit"],
)
async def test_a_read_only_step_whose_agent_edits_a_tracked_file_stops_naming_it(
    item_on, monkeypatch, fake_agent, target, during
):
    """The straggler sweep commits the edit, so it shows as HEAD moving; the
    step's own on_failure never runs, since no code failed. In place nothing
    is swept, and the agent's own commit moves HEAD."""
    step = {
        "id": "check",
        "read_only": True,
        "tasks": [_agent()],
        "on_failure": {"tasks": [_sub("fix")]},
    }
    it = await item_on([v1_node(steps=[step])], target=target)
    branch = store.branch_for(it.row())
    if target is None:
        git(it.repo, "checkout", "-qb", branch)  # a worktree is on its branch
    else:
        git(it.repo, "branch", branch)  # the take switches the checkout onto it
    launched = _launches(monkeypatch, during)

    assert await _walk(it) == "needs_human"

    assert launched == ["build.check.review"]
    assert git(it.repo, "show", "--name-only", "--format=", "HEAD") == "calc.py"
    assert it.status() == "needs_human"
    assert it.row()["stop_kind"] == "failed"
    assert _reason(it) == "build.check is read_only, but it changed the worktree: calc.py"
    [event] = it.events("read_only_violated")
    assert event["payload"] == {"node_id": "build", "scope": "build.check", "files": ["calc.py"]}


async def test_a_persons_edit_during_an_in_place_read_only_step_is_not_a_violation(
    item_on, monkeypatch, fake_agent
):
    """The person's own checkout: an uncommitted edit there is theirs as far as
    the check can tell, so only HEAD is compared."""
    step = {"id": "check", "read_only": True, "tasks": [_agent()]}
    it = await item_on([v1_node(steps=[step])], target=_IN_PLACE)
    git(it.repo, "branch", store.branch_for(it.row()))
    _launches(monkeypatch, lambda cwd: write(cwd, "calc.py", "the person's\n"))

    assert await _walk(it) == "ok"

    assert it.events("read_only_violated") == []
    assert git(it.repo, "status", "--porcelain") == "M calc.py"


@pytest.mark.parametrize(
    ("name", "stops"), [("new.txt", True), ("run.log", False)], ids=["untracked", "ignored"]
)
async def test_an_untracked_file_is_a_change_and_an_ignored_one_is_not(
    item_on, monkeypatch, name, stops
):
    it = await item_on([v1_node(steps=[{"id": "check", "read_only": True, "tasks": [_sub("t")]}])])
    (it.repo / ".gitignore").write_text("*.log\n")
    git(it.repo, "add", ".gitignore")
    git(it.repo, "commit", "-qm", "ignore logs")
    _launches(monkeypatch, lambda cwd: write(cwd, name, "x\n"))

    status = await _walk(it)

    if stops:
        assert status == "needs_human"
        assert _reason(it) == f"build.check is read_only, but it changed the worktree: {name}"
    else:
        assert status == "ok"
        assert it.events("read_only_violated") == []


async def test_a_recovery_writes_outside_the_check_and_the_retry_it_leads_to_inside_it(
    item_on, monkeypatch
):
    """`t` fails, its on_failure repair writes (by design, unchecked), and the
    retried `t` writes again: only the retry's file is the violation."""
    task = {**_sub("t"), "on_failure": {"tasks": [_sub("fix")]}}
    it = await item_on([v1_node(steps=[{"id": "check", "read_only": True, "tasks": [task]}])])
    statuses = iter(["failed", "done", "done"])
    writes = {"build.check.t.on_failure.main.fix": "fixed.txt"}
    launched: list[str] = []

    async def run_task(*_a, cwd, **kw):
        launched.append(kw["hook_point"])
        if len(launched) == 3:
            (Path(cwd) / "again.txt").write_text("x\n")
        elif kw["hook_point"] in writes:
            (Path(cwd) / writes[kw["hook_point"]]).write_text("x\n")
        return next(statuses)

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)

    assert await _walk(it) == "needs_human"
    assert launched == ["build.check.t", "build.check.t.on_failure.main.fix", "build.check.t"]
    assert _reason(it) == "build.check is read_only, but it changed the worktree: again.txt"


async def test_a_step_that_is_not_read_only_is_not_checked(item_on, monkeypatch):
    it = await item_on([v1_node(steps=[{"id": "check", "tasks": [_sub("t")]}])])
    _launches(monkeypatch, lambda cwd: write(cwd, "new.txt", "x\n"))

    assert await _walk(it) == "ok"
    assert it.events("read_only_violated") == []


async def test_a_read_only_node_is_checked_around_all_of_its_steps(item_on, monkeypatch):
    """The write happens in the second step; the violation is the node's."""
    steps = [{"id": "one", "tasks": [_sub("a")]}, {"id": "two", "tasks": [_sub("b")]}]
    it = await item_on([v1_node(steps=steps, read_only=True)])

    def during(cwd: Path) -> None:
        if launched[-1] == "build.two.b":
            (cwd / "calc.py").write_text("# edited\n")

    launched = _launches(monkeypatch, during)

    assert await _walk(it) == "needs_human"
    assert launched == ["build.one.a", "build.two.b"]
    assert _reason(it) == "build is read_only, but it changed the worktree: calc.py"


async def test_a_read_only_step_over_workspace_members_names_the_member_file(
    database, run_dirs, tmp_path, monkeypatch
):
    nodes = [v1_node(steps=[{"id": "check", "read_only": True, "tasks": [_sub("t")]}])]
    row, node, worktree = await workspace_item(database, run_dirs, tmp_path, [], nodes=nodes)
    _launches(monkeypatch, lambda cwd: (cwd / "repos" / "pkg" / "new.txt").write_text("x\n"))

    verdict, failed, _ = await dispatch.measure_node(
        database, run_dirs, row["id"], node, row, worktree, launch=NO_SETUP
    )

    assert (verdict, [t.path for t in failed]) == (READ_ONLY_VIOLATED, ["build.check.t"])
    events = database.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE type = 'read_only_violated'"
        ).fetchall()
    )
    assert [e["payload"] for e in events] == [
        '{"node_id": "build", "scope": "build.check", "files": ["repos/pkg/new.txt"]}'
    ]


async def test_a_sandboxed_read_only_step_that_plants_a_repository_is_not_read_by_host_git(
    item_on, monkeypatch
):
    """The planted repository is the change: named, and no host git compares
    the worktree it sits in (Kraft-nx4id)."""
    sandbox = {"policy": {"sandbox": {"kind": "docker", "image": "kraft/policy:1"}}}
    step = {"id": "check", "read_only": True, "tasks": [_sub("t")]}
    it = await item_on([v1_node(steps=[step], **sandbox)])
    head = git(it.repo, "rev-parse", "HEAD")
    await it.database.write(lambda c: store.set_base_ref(c, it.id, head))

    def plant(cwd: Path) -> None:
        nested = cwd / "vendor" / "x"
        nested.mkdir(parents=True)
        git(nested, "init", "-q", "-b", "main")

    _launches(monkeypatch, plant)

    assert await _walk(it) == "needs_human"
    reason = _reason(it)
    assert reason.startswith(
        "build.check is read_only, but it changed the worktree: (not compared:"
    )
    assert "vendor/x" in reason


# -- where read_only may be set --------------------------------------------------------


def test_a_task_level_read_only_is_refused_pointing_at_the_step():
    with pytest.raises(ValidationError, match="set read_only on the step: tasks in a step share"):
        tm.Step.model_validate({"id": "s", "tasks": [{**_sub("t"), "read_only": True}]})


def test_a_node_with_a_fix_loop_cannot_be_read_only():
    loop = {"tasks": [_sub("fix")]}
    with pytest.raises(ValidationError, match="a node with a fix_loop cannot be read_only"):
        tm.ExecNode.model_validate(
            v1_node(steps=[{"id": "s", "tasks": [_sub("t")]}], read_only=True, fix_loop=loop)
        )


@pytest.mark.parametrize("handler", ["on_failure", "fix_loop"])
def test_a_recovery_or_fix_loop_step_cannot_be_read_only(handler):
    shape = {"steps": [{"id": "repair", "read_only": True, "tasks": [_sub("fix")]}]}
    with pytest.raises(ValidationError, match="cannot be read_only: it writes by design"):
        tm.ExecNode.model_validate(
            v1_node(steps=[{"id": "s", "tasks": [_sub("t")]}], **{handler: shape})
        )


def test_git_failing_the_same_way_twice_still_counts_as_a_change(tmp_path):
    """Unverified is not read-only: identical failed reads must not compare equal."""
    from kraft.executor import read_only

    failed = (None, None, None)
    assert read_only._changed_in(tmp_path, failed, failed) == ["(git could not read it)"]


async def test_an_in_place_take_is_not_the_read_only_steps_write(item_on, monkeypatch):
    """The take switches the person's checkout to the item's branch right
    before the task: Kraft's move, read before the step's snapshot."""
    it = await item_on(
        [v1_node(steps=[{"id": "check", "read_only": True, "tasks": [_sub("t")]}])],
        target=_IN_PLACE,
    )
    branch = store.branch_for(it.row())
    git(it.repo, "checkout", "-qb", branch)
    write(it.repo, "ITEM", "on the item's branch\n")
    git(it.repo, "add", "ITEM")
    git(it.repo, "commit", "-qm", "item")
    git(it.repo, "checkout", "-q", "main")
    _launches(monkeypatch)

    assert await _walk(it) != "needs_human"

    assert git(it.repo, "symbolic-ref", "HEAD") == f"refs/heads/{branch}"
    assert it.events("read_only_violated") == []
