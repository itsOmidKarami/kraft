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
_SANDBOX = {"kind": "docker", "image": "kraft/policy:1"}
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
