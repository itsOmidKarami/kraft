"""`kraft.adapters.subprocess.run_task` sandboxed: what of the shared results
folder its container is handed (Kraft-dni4n)."""

import os

from support.harness import fake_docker_bin
from support.worktree import make_item

from kraft import store
from kraft.adapters import subprocess as sp
from kraft.worker.backends import docker as docker_backend

DOCKER = {"kind": "docker", "image": "kraft-worker:py"}


async def test_a_sandboxed_launch_mounts_only_its_own_items_results(
    monkeypatch, run_dirs, database, tmp_path
):
    """The results folder holds every work item's diffs and findings, so a
    sandboxed session is handed its own item's files and never the folder."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}:{os.environ['PATH']}")
    seen = []
    real = docker_backend.docker_argv

    def spy(cmd, cwd, sandbox, results, **kw):
        seen.append((results, kw["result_path"]))
        return real(cmd, cwd, sandbox, results, **kw)

    monkeypatch.setattr(docker_backend, "docker_argv", spy)
    results = run_dirs.results
    await make_item(database, "/r")
    await make_item(database, "/other", "w2")
    await database.write(
        lambda c: store.create_session(
            c,
            id="theirs",
            work_item_id="w2",
            node_id="verify",
            hook_point="on.test.run",
            log_path="/l/theirs",
            result_path=str(results / "theirs.json"),
        )
    )
    for name in ("theirs.json", "theirs.review.md", "s2.review.md"):
        (results / name).write_text("x")

    for sid in ("s1", "s2"):
        await sp.run_task(
            database,
            run_dirs,
            session_id=sid,
            work_item_id="w1",
            node_id="verify",
            hook_point="on.test.run",
            cwd=tmp_path,
            cmd=["true"],
            sandbox=DOCKER,
        )

    own = results / "s2.json"
    assert seen[1] == ([results / "s1.json", own, results / "s2.review.md"], own)
