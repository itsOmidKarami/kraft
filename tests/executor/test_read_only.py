"""`read_only` steps and nodes, verified (Kraft-q2zvw).

A read_only step is checked around all of its tasks together, a read_only node
around its own steps: HEAD, `git status --porcelain=v1 -z` and a hash of
`git diff HEAD`, per repository of the checkout. A change stops the item for a
person naming the files, and is never a failure a recovery or fix loop spends
on. The launch is faked at `kraft.adapters.subprocess.run_task`, and `during`
does what the task would have done to the worktree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from support.harness import entry_of
from support.workspace import workspace_item

from kraft import store
from kraft.adapters import agent as agent_mod
from kraft.executor import dispatch, walk
from kraft.executor.context import READ_ONLY_VIOLATED, LaunchContext
from kraft.templates import models as tm

NO_SETUP = LaunchContext(repo_entry=entry_of({"setup_command": ""}), steering_dir=None)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _launches(monkeypatch, during=None) -> list:
    """Fake every launch; `during(cwd)` runs as the task would."""
    launched = []

    async def run_task(*_a, cwd, **kw):
        launched.append(kw["hook_point"])
        if during is not None:
            during(Path(cwd))
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    monkeypatch.setattr(agent_mod._subprocess, "run_task", run_task)
    return launched


def _agent(task_id: str = "review") -> dict:
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": "Review it."}


def _sub(task_id: str) -> dict:
    return {"id": task_id, "kind": "subprocess", "command": "true"}


def _node(steps: list[dict], **fields) -> dict:
    return {"id": "build", "kind": "exec", "steps": steps, **fields}


async def _walk(it) -> str:
    node = it.chain.chain.nodes[0]
    return await walk.walk_node(
        it.database, it.run_dirs, it.id, node, it.row(), it.repo, launch=NO_SETUP
    )


def _reason(it) -> str:
    return it.events("work_item_needs_human")[-1]["payload"]["reason"]


def _write(name: str, text: str = "x\n"):
    return lambda cwd: (cwd / name).write_text(text)


# -- the check -------------------------------------------------------------------------


async def test_a_read_only_step_whose_agent_edits_a_tracked_file_stops_naming_it(
    item_on, monkeypatch, fake_agent
):
    """The straggler sweep commits the edit, so it shows as HEAD moving; the
    step's own on_failure never runs, since no code failed."""
    step = {
        "id": "check",
        "read_only": True,
        "tasks": [_agent()],
        "on_failure": {"tasks": [_sub("fix")]},
    }
    it = await item_on([_node([step])])
    launched = _launches(monkeypatch, _write("calc.py", "edited\n"))

    assert await _walk(it) == "needs_human"

    assert launched == ["build.check.review"]
    assert _git(it.repo, "show", "--name-only", "--format=", "HEAD") == "calc.py"
    assert it.status() == "needs_human"
    assert _reason(it) == "build.check is read_only, but it changed the worktree: calc.py"
    [event] = it.events("read_only_violated")
    assert event["payload"] == {"node_id": "build", "scope": "build.check", "files": ["calc.py"]}


@pytest.mark.parametrize(
    ("name", "stops"), [("new.txt", True), ("run.log", False)], ids=["untracked", "ignored"]
)
async def test_an_untracked_file_is_a_change_and_an_ignored_one_is_not(
    item_on, monkeypatch, name, stops
):
    it = await item_on([_node([{"id": "check", "read_only": True, "tasks": [_sub("t")]}])])
    (it.repo / ".gitignore").write_text("*.log\n")
    _git(it.repo, "add", ".gitignore")
    _git(it.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore logs")
    _launches(monkeypatch, _write(name))

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
    it = await item_on([_node([{"id": "check", "read_only": True, "tasks": [task]}])])
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
    it = await item_on([_node([{"id": "check", "tasks": [_sub("t")]}])])
    _launches(monkeypatch, _write("new.txt"))

    assert await _walk(it) == "ok"
    assert it.events("read_only_violated") == []


async def test_a_read_only_node_is_checked_around_all_of_its_steps(item_on, monkeypatch):
    """The write happens in the second step; the violation is the node's."""
    steps = [{"id": "one", "tasks": [_sub("a")]}, {"id": "two", "tasks": [_sub("b")]}]
    it = await item_on([_node(steps, read_only=True)])

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
    nodes = [_node([{"id": "check", "read_only": True, "tasks": [_sub("t")]}])]
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
    it = await item_on([_node([step], **sandbox)])
    head = _git(it.repo, "rev-parse", "HEAD")
    await it.database.write(lambda c: store.set_base_ref(c, it.id, head))

    def plant(cwd: Path) -> None:
        nested = cwd / "vendor" / "x"
        nested.mkdir(parents=True)
        _git(nested, "init", "-q", "-b", "main")

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
            _node([{"id": "s", "tasks": [_sub("t")]}], read_only=True, fix_loop=loop)
        )


@pytest.mark.parametrize("handler", ["on_failure", "fix_loop"])
def test_a_recovery_or_fix_loop_step_cannot_be_read_only(handler):
    shape = {"steps": [{"id": "repair", "read_only": True, "tasks": [_sub("fix")]}]}
    with pytest.raises(ValidationError, match="cannot be read_only: it writes by design"):
        tm.ExecNode.model_validate(_node([{"id": "s", "tasks": [_sub("t")]}], **{handler: shape}))
