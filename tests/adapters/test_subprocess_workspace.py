"""`kraft.adapters.subprocess.run_task` sandboxed in a workspace item's
checkout: every repository of it mounted and published (Kraft-ju36l)."""

import os
import subprocess

import pytest
from support.harness import fake_docker_bin
from support.workspace import member_checkout, workspace_item
from support.worktree import make_item

from kraft import store
from kraft.adapters import subprocess as sp
from kraft.worker import refstore
from kraft.worker.backends import docker as docker_backend

DOCKER = {"kind": "docker", "image": "kraft-worker:py"}


@pytest.fixture
def docker(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}:{os.environ['PATH']}")


async def test_run_task_publishes_every_member_branch_a_sandboxed_session_committed(
    docker, run_dirs, database, tmp_path, monkeypatch
):
    """Kraft-ju36l: a sandboxed session in a workspace commits through one
    store per repository, and each repository's item branch is published out
    of its own -- here from a run fanned out into the member, which mounts
    the whole checkout and starts in the member: the member's own path is
    never resolved on the host, where the worker could have made it a
    symlink."""
    argvs = []
    real = docker_backend.docker_argv
    monkeypatch.setattr(
        docker_backend, "docker_argv", lambda *a, **kw: argvs.append(real(*a, **kw)) or argvs[-1]
    )
    await make_item(database, tmp_path / "unused")
    branch = database.read(lambda c: store.branch_of(c, "w1"))
    ws = member_checkout(tmp_path / "side", branch)
    admins = {
        ws.root: (ws.root / ".git" / "worktrees" / "wt").resolve(),
        ws.m: ws.checkout.members["repos/pkg"][1],
    }
    work, script = {}, ["true"]
    for repo, admin in admins.items():
        tree = subprocess.check_output(["git", "rev-parse", "main^{tree}"], cwd=repo, text=True)
        work[repo] = subprocess.check_output(
            ["git", "commit-tree", tree.strip(), "-p", "main", "-m", "w"], cwd=repo, text=True
        ).strip()
        loose = refstore.shadow_dir(run_dirs.base, admin) / "refs" / "heads" / branch
        script.append(f"mkdir -p {loose.parent} && echo {work[repo]} > {loose}")

    status = await sp.run_task(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        cmd=["sh", "-c", " && ".join(script)],
        node_id="n",
        hook_point="on.test.run",
        sandbox=DOCKER,
        cwd=ws.wt / "repos" / "pkg",
        checkout=ws.checkout,
    )

    assert status == "done"
    published = {
        repo: subprocess.check_output(["git", "rev-parse", branch], cwd=repo, text=True).strip()
        for repo in admins
    }
    assert published == work
    wt = ws.wt.resolve()
    (argv,) = argvs
    assert f"{wt}:{wt}" in argv
    assert argv[argv.index("-w") + 1] == str(wt / "repos" / "pkg")


async def test_a_sandboxed_launch_not_given_its_members_checkout_never_starts(
    docker, database, run_dirs, tmp_path
):
    """Whichever caller forgot the checkout: the members' `.git` and admin
    files would be the worker's to rewrite, so nothing launches."""
    task = {"id": "t", "kind": "subprocess", "command": "true"}
    row, _, worktree = await workspace_item(database, run_dirs, tmp_path, [task])
    status = await sp.run_task(
        database,
        run_dirs,
        session_id="s1",
        work_item_id=row["id"],
        cmd=["true"],
        node_id="n",
        hook_point="on.test.run",
        cwd=worktree,
        sandbox=DOCKER,
    )
    assert status == "config_error"
    assert "repos/pkg" in (run_dirs.logs / "s1.log").read_text()


async def test_run_task_records_the_repository_a_fanned_out_run_is_for(
    run_dirs, database, tmp_path
):
    """The session row names the repository it ran for, so a node's view can
    say where each run happened; a run that is not fanned out names none."""
    await make_item(database, tmp_path / "unused")
    for sid, kw in (("s1", {"repository": "pkg"}), ("s2", {})):
        await sp.run_task(
            database,
            run_dirs,
            session_id=sid,
            work_item_id="w1",
            cmd=["true"],
            node_id="n",
            hook_point="on.test.run",
            cwd=tmp_path,
            **kw,
        )

    rows = database.read(
        lambda c: c.execute("SELECT id, repository FROM worker_sessions").fetchall()
    )
    assert {r["id"]: r["repository"] for r in rows} == {"s1": "pkg", "s2": None}
