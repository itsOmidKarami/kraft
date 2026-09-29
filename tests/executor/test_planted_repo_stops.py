"""Layer 2 of Kraft-nx4id / Kraft-69rwp at its real call sites (Kraft-pa1i8).

`stops.refuse_planted_repos`, `stops.refuse_live_sandboxed_session` and
`sandbox.planted_repos` are unit-tested in tests/worker/test_planted_repos.py.
These drive the entry points that call them -- a task's dispatch, the review
package, the straggler sweep, the diagnosis bundle -- on a sandboxed item, and
on the same item unsandboxed, so deleting any one guard fails a test here.
(The diff endpoint's guard is tests/api/test_diff.py's.)

Every fixture is benign: a plain nested repository with no config of its own.
Layer 2 is about noticing one, not about what one could do. The launch is
faked at `kraft.adapters.subprocess.run_task`, so no container ever starts.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from support.harness import entry_of

from kraft import store
from kraft.adapters import agent as agent_mod
from kraft.executor import dispatch, walk
from kraft.executor.context import CONFIG_ERROR, LaunchContext

NO_SETUP = LaunchContext(repo_entry=entry_of({"setup_command": ""}))
#: With `network:`: a claude-shaped agent is refused a sandbox without one.
_SANDBOX = {
    "kind": "docker",
    "image": "kraft/policy:1",
    "network": {"runtime": {"allow": ["x.io"]}},
}
_SANDBOXED = pytest.mark.parametrize("sandboxed", [True, False], ids=["sandboxed", "unsandboxed"])


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _nested(parent: Path, rel: str) -> None:
    """A plain repository at `parent/rel` with one commit, no config of its own
    (tests/worker/test_planted_repos.py's `_nested`)."""
    path = parent / rel
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "f").write_text("x\n")
    _git(path, "add", "f")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "n")


async def _item(item_on, task: dict, sandboxed: bool):
    """An item whose one task is `task`, its snapshot freezing a sandbox or
    not, with `base_ref` stamped at the repo's HEAD the way env setup does."""
    policy = {"policy": {"sandbox": _SANDBOX}} if sandboxed else {}
    it = await item_on([{"id": "implementation", "kind": "exec", "tasks": [task], **policy}])
    _git(it.repo, "checkout", "-qb", store.branch_for(it.row()))  # a worktree is on its branch
    head = _git(it.repo, "rev-parse", "HEAD")
    await it.database.write(lambda c: store.set_base_ref(c, it.id, head))
    return it


def _launches(monkeypatch, during=None) -> list:
    """Fake the process launch for every task kind; `during(cwd)` runs as the
    task would, to leave something in the worktree."""
    launched = []

    async def run_task(*_a, cwd, **kw):
        launched.append(kw["hook_point"])
        if during is not None:
            during(Path(cwd))
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    monkeypatch.setattr(agent_mod._subprocess, "run_task", run_task)
    return launched


async def _dispatch(it):
    node = it.chain.chain.nodes[0]
    return await dispatch.dispatch_node(
        it.database, it.run_dirs, node.steps[0].tasks[0], node, it.row(), it.repo, launch=NO_SETUP
    )


async def _co_task_live(it):
    """Another task of the item, its session still running."""
    await it.session("co", "implementation.main.co", running=(os.getpid(), 0.0))


def _log_of(it, status: str) -> str:
    (session,) = [s for s in it.sessions() if s["status"] == status]
    return Path(session["log_path"]).read_text()


def _agent(**fields) -> dict:
    return {"id": "implement", "kind": "agent", "harness": "fake", "prompt": "Do it.", **fields}


# -- dispatch.py: the per-task planted-repository check --------------------------------


@_SANDBOXED
async def test_a_planted_repository_stops_the_next_task_before_it_launches(
    item_on, monkeypatch, fake_agent, sandboxed
):
    """A repository an earlier sandboxed task left in the worktree stops the
    next task as a config error naming the path, and nothing launches. An
    unsandboxed item's nested repository is its own business."""
    it = await _item(item_on, {"id": "t", "kind": "subprocess", "command": "true"}, sandboxed)
    launched = _launches(monkeypatch)
    _nested(it.repo, "vendor/x")

    status = await _dispatch(it)

    if sandboxed:
        assert (status, launched) == (CONFIG_ERROR, [])
        assert "vendor/x" in _log_of(it, CONFIG_ERROR)
    else:
        assert (status, launched) == ("done", ["implementation.main.t"])


# -- dispatch.py: the review package waits for a live sandboxed co-task ----------------


@_SANDBOXED
async def test_the_review_package_is_not_read_while_a_sandboxed_co_task_runs(
    item_on, monkeypatch, fake_agent, sandboxed
):
    it = await _item(item_on, _agent(inputs=["review_package"]), sandboxed)
    launched = _launches(monkeypatch)
    await _co_task_live(it)

    status = await _dispatch(it)

    if sandboxed:
        assert (status, launched) == (CONFIG_ERROR, [])
        assert "the review package is available once" in _log_of(it, CONFIG_ERROR)
    else:
        assert (status, launched) == ("done", ["implementation.main.implement"])
        (package,) = it.run_dirs.results.glob("*.review.md")
        assert package.is_file()


# -- dispatch.py: the straggler sweep --------------------------------------------------


@pytest.mark.parametrize(
    ("hazard", "expected"),
    [
        pytest.param("live-co-task", "deferred: the straggler sweep is available once", id="live"),
        pytest.param("planted-repo", "skipped: the sandboxed worktree holds", id="planted"),
    ],
)
@_SANDBOXED
async def test_the_straggler_sweep_leaves_a_sandboxed_worktree_alone(
    item_on, monkeypatch, fake_agent, hazard, expected, sandboxed
):
    """The task leaves an uncommitted file behind, and either a co-task is
    still live or the task itself nested a repository in the worktree (after
    the per-task check, so only the sweep's own check can see it). Sandboxed,
    the sweep commits nothing and says why; unsandboxed, it commits the file."""
    it = await _item(item_on, _agent(), sandboxed)

    def during(cwd: Path) -> None:
        (cwd / "left.txt").write_text("left behind\n")
        if hazard == "planted-repo":
            _nested(cwd, "vendor/x")

    _launches(monkeypatch, during)
    if hazard == "live-co-task":
        await _co_task_live(it)

    assert await _dispatch(it) == "done"

    errors = [e["payload"]["error"] for e in it.events("sweep_failed")]
    committed = "left.txt" in _git(it.repo, "ls-files").splitlines()
    if sandboxed:
        assert (committed, len(errors)) == (False, 1)
        assert errors[0].startswith(expected)
    else:
        assert (committed, errors) == (True, [])


# -- walk.py: the diagnosis bundle's worktree status -----------------------------------


@_SANDBOXED
async def test_the_diagnosis_bundle_reads_no_status_while_a_sandboxed_session_runs(
    item_on, sandboxed
):
    it = await _item(item_on, _agent(), sandboxed)
    (it.repo / "left.txt").write_text("left behind\n")
    await _co_task_live(it)

    bundle = await walk._diagnosis_bundle(
        it.database, it.id, it.chain.chain.nodes[0], it.repo, NO_SETUP
    )

    if sandboxed:
        assert bundle["git_status"].startswith("(not read: the worktree status is available once")
    else:
        assert "?? left.txt" in bundle["git_status"]


# -- Kraft-ju36l: a member checkout Kraft did not make stops the item ----------------

import dataclasses  # noqa: E402

import yaml  # noqa: E402
from support.harness import make_repo_with_submodule, v1_chain, v1_walk  # noqa: E402
from support.workspace import repositories, workspace_item, workspace_target  # noqa: E402

from kraft import events  # noqa: E402
from kraft.executor import gates  # noqa: E402
from kraft.policy import (  # noqa: E402
    InstancePolicy,
    InstancePolicyInput,
    SandboxPolicy,
    TemplatePolicyOverride,
)
from kraft.worker import sandbox as sandbox_mod  # noqa: E402

_REL = "repos/pkg"
_PLAIN_SANDBOX = {"kind": "docker", "image": "img"}
_NODES = [
    {
        "id": "implementation",
        "kind": "exec",
        "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}],
    }
]
_DRIFT = "runs sandboxed, and its workspace members"


def _sandboxed_policy() -> dict:
    policy = InstancePolicy.from_input(InstancePolicyInput()).apply_template_override(
        TemplatePolicyOverride(sandbox=SandboxPolicy(**_PLAIN_SANDBOX))
    )
    return {"effective_policy": policy, "repository_policies": {"pkg": policy}}


def _swap_member(worktree: Path, rel: str) -> None:
    """What a worker can do to a member it can write: move it aside and put a
    `.git` of its own at the mount path, naming a repository it built."""
    top = worktree / Path(rel).parts[0]
    top.rename(top.with_name(top.name + "-moved"))
    (worktree / "evil").mkdir()
    _git(worktree / "evil", "init", "-q")
    (worktree / rel).mkdir(parents=True)
    (worktree / rel / ".git").write_text(f"gitdir: {worktree / 'evil' / '.git'}\n")


@_SANDBOXED
async def test_a_member_drift_stops_the_next_task_before_it_launches(
    database, run_dirs, tmp_path, monkeypatch, sandboxed
):
    """A sandboxed item's member whose checkout is no longer the one Kraft
    made stops the next task as a config error naming it, before it launches
    and before host git runs in it. An unsandboxed item's worker could do the
    same on the host; it is not checked, and no member is scanned."""
    policy = _sandboxed_policy() if sandboxed else {}
    row, node, worktree = await workspace_item(
        database, run_dirs, tmp_path, _NODES[0]["tasks"], **policy
    )
    launched = _launches(monkeypatch)
    scans = []
    real = sandbox_mod.foreign_members
    monkeypatch.setattr(sandbox_mod, "foreign_members", lambda *a: scans.append(a) or real(*a))
    _swap_member(worktree, _REL)
    launch = LaunchContext(
        repo_entry=entry_of({"setup_command": ""}), repositories=repositories(tmp_path, "pkg")
    )

    status = await dispatch.dispatch_node(
        database, run_dirs, node.steps[0].tasks[0], node, row, worktree, launch=launch
    )

    if sandboxed:
        assert (status, launched) == (CONFIG_ERROR, [])
        (log,) = database.read(
            lambda c: c.execute(
                "SELECT log_path FROM worker_sessions WHERE status = ?", (CONFIG_ERROR,)
            ).fetchone()
        )
        assert f"{_DRIFT} {_REL} are not" in Path(log).read_text()
    else:
        assert (status, launched, scans) == ("done", ["n.main.t"], [])


def _old_layout(root: Path, worktree: Path, rel: str) -> None:
    """`worktree` with member `rel` checked out as Kraft did before
    Kraft-ju36l: `submodule update --init`, its gitdir under the root's
    worktree gitdir, which a sandboxed worker writes."""
    _git(root, "worktree", "add", "-q", "-b", "kraft/old-layout", str(worktree))
    _git(worktree, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--", rel)


def _reason(evts) -> str:
    return next(e for e in reversed(evts) if e["type"] == "work_item_needs_human")["payload"][
        "reason"
    ]


def _sandboxed(materialized):
    """`materialized` with a sandbox on its item-wide policy, as an item
    filed sandboxed."""
    layer = TemplatePolicyOverride(sandbox=SandboxPolicy(**_PLAIN_SANDBOX))
    return dataclasses.replace(
        materialized, policy=materialized.policy.apply_template_override(layer)
    )


@pytest.mark.parametrize(
    ("chain", "item", "entry"),
    [
        pytest.param(
            _sandboxed(v1_chain(_NODES, repo="/r", target=workspace_target({"a": "libs/a"}))),
            {},
            {},
            id="a-workspace-snapshot-frozen-sandboxed",
        ),
        pytest.param(
            v1_chain(_NODES, repo="/r"),
            {"submodules": ["libs/a"], "root_merge_policy": "bump"},
            {"sandbox": _PLAIN_SANDBOX},
            id="a-legacy-submodules-column-under-a-live-sandbox",
        ),
    ],
)
async def test_an_old_layout_member_stops_the_walk_before_host_git_runs(
    tmp_path, run_dirs, monkeypatch, chain, item, entry
):
    """An item in flight from before Kraft-ju36l, its member's gitdir where
    the worker writes it -- including Kraft-zvqwl's legacy column -- stops
    for a person naming the member. No session, and no push, ever ran."""
    pushed = []
    monkeypatch.setattr("kraft.adapters.forge.git.push", lambda *a: pushed.append(a))
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")

    async def old_layout(_database):
        _old_layout(root, run_dirs.worktrees / "w1", "libs/a")

    status, evts, sessions, _row = await v1_walk(
        tmp_path,
        chain,
        repo=root,
        repo_entry=entry_of({"path": str(root), "setup_command": "", **entry}),
        run_dirs=run_dirs,
        after_item=old_layout,
        **item,
    )

    assert status == "needs_human"
    assert f"{_DRIFT} libs/a are not" in _reason(evts)
    assert sessions == [] and pushed == []


@pytest.fixture
def refreshed(monkeypatch):
    """Every `refresh_worktree_base` a door makes, recorded, never run."""
    calls = []

    async def fake(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr("kraft.builtins.refresh_worktree_base", fake)
    return calls


@pytest.mark.parametrize(
    ("door", "stopped"), [("retry", "needs_human"), ("resume", "paused")], ids=["retry", "resume"]
)
def test_an_old_layout_member_stops_a_door_before_it_refreshes_the_worktree(
    client, tmp_path, refreshed, door, stopped
):
    """`/retry` and `/resume` rebase the worktree before the walk, which is
    host git in it and its members. A sandbox added to a member after filing
    binds the item live (`dispatch._task_sandbox`), so the door stops it."""
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    root = root.resolve()
    assert client.post("/api/repos", json={"path": str(root), "enabled": False}).status_code == 201
    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(root), "workspace": "ws", "members": ["a"]}
        | {"autostart": False},
    )
    wid = r.json()["id"]
    from support.api import _force_node

    _force_node(wid, "spec", stopped)
    _old_layout(root, Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid, "libs/a")
    repos_yaml = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(repos_yaml.read_text())
    next(e for e in data["repos"] if e["path"] == str(root / "libs" / "a"))["sandbox"] = (
        _PLAIN_SANDBOX
    )
    repos_yaml.write_text(yaml.safe_dump(data))

    r = client.post(f"/api/work-items/{wid}/{door}", json={})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "needs_human"
    assert f"{_DRIFT} libs/a are not" in _reason(client.get(f"/api/work-items/{wid}/events").json())
    assert refreshed == []


async def test_an_old_layout_member_stops_an_escalations_self_retry_before_the_refresh(
    item_on, tmp_path, run_dirs, refreshed
):
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    it = await item_on(
        _NODES, "implementation", repo=root, target=workspace_target({"a": "libs/a"})
    )
    _old_layout(root, run_dirs.worktrees / it.id, "libs/a")
    await it.database.write(
        lambda c: store.mark_needs_human(c, it.id, "implementation", "stuck", stuck=True)
    )
    cursor = it.events()[-1]["seq"]
    request = {"node_id": "implementation", "key": None, "gate_key": None, "steer": None}
    await it.database.write(
        lambda c: events.append(c, it.id, "work_item_self_retry_requested", request)
    )
    launch = LaunchContext(repo_entry=entry_of({"path": str(root), "sandbox": _PLAIN_SANDBOX}))

    status = await gates.resume_after_escalation(
        it.database, run_dirs, work_item_id=it.id, cursor=cursor, launch=launch
    )

    assert status == "needs_human"
    assert f"{_DRIFT} libs/a are not" in _reason(it.events())
    assert refreshed == [] and not it.events("work_item_retried")
