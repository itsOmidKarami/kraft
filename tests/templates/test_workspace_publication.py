"""A workspace work item end to end: fan-out by repository and the policy
each repository runs under, area setup before an area's test scope, and
publication order across the members and the root (Task 10).

Git is real (a root with one real submodule, `make_repo_with_submodule`);
the forge is `FakeForge`, and a task's process is a spy wherever what it ran
under is the question."""

from __future__ import annotations

from pathlib import Path

import pytest
from support import worktree as wtree
from support.harness import _git, make_repo, make_repo_with_submodule, v1_chain, workspace_target

from kraft.executor import dispatch
from kraft.executor.context import LaunchContext
from kraft.policy import InstancePolicy, InstancePolicyInput, SandboxPolicy

NO_SETUP = {"setup_command": ""}
_SANDBOX = SandboxPolicy(kind="docker", image="kraft/member:1")


def _policy(**override) -> InstancePolicy:
    base = InstancePolicy.from_input(InstancePolicyInput())
    return base.apply_template_override(override) if override else base


async def _workspace_item(database, run_dirs, tmp_path, tasks, **materialize):
    """A root with one submodule `pkg` at `repos/pkg`, filed as a workspace
    item selecting it, on one exec node of `tasks`; its checkout assembled.
    Returns `(row, node, worktree)`."""
    root, _ = make_repo_with_submodule(tmp_path)
    chain = v1_chain(
        [{"id": "n", "kind": "exec", "tasks": tasks}],
        repo=root,
        target=workspace_target({"pkg": "repos/pkg"}),
    )
    if materialize:
        chain = chain.chain.materialize(target=chain.target, **materialize)
    await wtree.make_item(database, root, materialized_chain=chain.to_json())
    worktree = await wtree.ensure(database, run_dirs, root)
    row = database.read(lambda c: c.execute("SELECT * FROM work_items").fetchone())
    return row, chain.chain.nodes[0], worktree


@pytest.fixture
def ran(monkeypatch):
    """Every subprocess task launch, as `(cwd, sandbox)`; each reports done."""
    calls: list[tuple[Path, dict | None]] = []

    async def run_task(db, run_dirs, *, cwd, sandbox=None, **_):
        calls.append((Path(cwd), sandbox))
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    return calls


def _task(id, **fields):
    return {"id": id, "kind": "subprocess", "command": "true", **fields}


LAUNCH = LaunchContext(
    repo_entry=NO_SETUP, steering_dir=None, repositories={"ws": NO_SETUP, "pkg": NO_SETUP}
)


# ── fan-out (`task-may-explicitly-fan-out-by-repository`) ──


async def test_a_task_opting_in_runs_once_per_selected_repository_and_others_once(
    database, run_dirs, tmp_path, ran
):
    """Only `scope: each_repository` fans out -- once in each selected
    repository's own checkout, root first; a task that does not opt in runs
    once, in the assembled checkout (`workspace-tasks-have-an-assembled-
    checkout`)."""
    row, node, worktree = await _workspace_item(
        database, run_dirs, tmp_path, [_task("each", scope="each_repository"), _task("once")]
    )
    each, once = node.tasks()

    assert (
        await dispatch.dispatch_node(database, run_dirs, each, node, row, worktree, launch=LAUNCH)
        == "done"
    )
    assert [cwd for cwd, _ in ran] == [worktree, worktree / "repos" / "pkg"]

    ran.clear()
    assert (
        await dispatch.dispatch_node(database, run_dirs, once, node, row, worktree, launch=LAUNCH)
        == "done"
    )
    assert [cwd for cwd, _ in ran] == [worktree]


async def test_a_fanned_out_task_fails_when_any_repository_fails(
    database, run_dirs, tmp_path, monkeypatch
):
    statuses = iter(["done", "failed"])

    async def run_task(db, run_dirs, **_):
        return next(statuses)

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    row, node, worktree = await _workspace_item(
        database, run_dirs, tmp_path, [_task("each", scope="each_repository")]
    )
    (each,) = node.tasks()
    assert (
        await dispatch.dispatch_node(database, run_dirs, each, node, row, worktree, launch=LAUNCH)
        == "failed"
    )


async def test_each_repository_binds_its_own_task_and_the_checkout_binds_all(
    database, run_dirs, tmp_path, ran
):
    """Kraft-jc39p, at launch: a task fanned out to a member runs under that
    member's frozen policy -- here, its sandbox -- and the root's run under
    the root's; the assembled checkout's task under the item's policy, the
    meet of them all."""
    row, node, worktree = await _workspace_item(
        database,
        run_dirs,
        tmp_path,
        [_task("each", scope="each_repository"), _task("once")],
        effective_policy=_policy(sandbox=_SANDBOX.model_dump()),
        repository_policies={"ws": _policy(), "pkg": _policy(sandbox=_SANDBOX.model_dump())},
    )
    each, once = node.tasks()

    await dispatch.dispatch_node(database, run_dirs, each, node, row, worktree, launch=LAUNCH)
    await dispatch.dispatch_node(database, run_dirs, once, node, row, worktree, launch=LAUNCH)

    assert [sandbox for _, sandbox in ran] == [None, _SANDBOX.model_dump(), _SANDBOX.model_dump()]


async def test_a_fanned_out_run_reads_its_own_repositorys_entry(database, run_dirs, tmp_path, ran):
    """A member's run is configured by the member's `repos.yaml` entry -- its
    live sandbox here -- never the root's."""
    member = {"setup_command": "", "sandbox": {"kind": "docker", "image": "member:live"}}
    launch = LaunchContext(
        repo_entry=NO_SETUP, steering_dir=None, repositories={"ws": NO_SETUP, "pkg": member}
    )
    row, node, worktree = await _workspace_item(
        database, run_dirs, tmp_path, [_task("each", scope="each_repository")]
    )
    (each,) = node.tasks()

    await dispatch.dispatch_node(database, run_dirs, each, node, row, worktree, launch=launch)

    assert [sandbox for _, sandbox in ran] == [None, member["sandbox"]]


# ── areas (`repository-area-can-declare-setup-and-test-scopes`) ──

_AREAS = {
    "setup_command": "",
    "test_scopes": [{"paths": ["src/**"], "command": "just test"}],
    "areas": {
        "python_api": {
            "paths": ["services/api/**"],
            "setup": "uv sync",
            "verification": {
                "test_scopes": [{"paths": ["services/api/**"], "command": "just test-api"}]
            },
        },
        "java_worker": {
            "paths": ["services/worker/**"],
            "setup": "./gradlew classes",
            "verification": {
                "test_scopes": [{"paths": ["services/worker/**"], "command": "./gradlew test"}]
            },
        },
    },
}


@pytest.fixture
def commands(monkeypatch):
    """Every command a verification task ran, in order; each reports done."""
    calls: list[list[str]] = []

    async def run_task(db, run_dirs, *, cmd, **_):
        calls.append(list(cmd))
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    return calls


async def test_a_changed_path_in_an_area_runs_its_setup_then_its_scope(
    database, run_dirs, tmp_path, commands
):
    """An area's test scopes join the repository's in one table, selected by
    changed paths the same way; before an area's scope runs, its setup runs
    (`selected-test-scope-activates-its-area-setup`). No area is ever chosen
    at intake, so this one is "unexpected" in the requirement's sense, and is
    still set up and tested (`unexpected-area-changes-are-tested`); the area
    nothing changed is neither."""
    repo = make_repo(tmp_path)
    chain = v1_chain(
        [
            {
                "id": "v",
                "kind": "exec",
                "tasks": [
                    {"id": "t", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"}
                ],
            }
        ],
        repo=repo,
    )
    await wtree.make_item(database, repo, materialized_chain=chain.to_json())
    worktree = await wtree.ensure(database, run_dirs, repo)
    (worktree / "services" / "api").mkdir(parents=True)
    (worktree / "services" / "api" / "app.py").write_text("x = 1\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-qm", "touch the api area")
    row = database.read(lambda c: c.execute("SELECT * FROM work_items").fetchone())
    node = chain.chain.nodes[0]

    status = await dispatch.dispatch_node(
        database,
        run_dirs,
        next(iter(node.tasks())),
        node,
        row,
        worktree,
        launch=LaunchContext(repo_entry=_AREAS, steering_dir=None),
    )

    assert status == "done"
    assert commands == [["uv", "sync"], ["just", "test-api"]]
