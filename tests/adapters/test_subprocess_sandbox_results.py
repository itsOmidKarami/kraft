"""`kraft.adapters.subprocess.run_task` sandboxed: what of its item's session
files its container is handed (Kraft-dni4n), and where a session started now
keeps them."""

import os
from pathlib import Path

import pytest
from support.harness import fake_docker_bin
from support.worktree import make_item
from worker.backends.test_docker import _volumes

from kraft import item_paths, store
from kraft.adapters import subprocess as sp
from kraft.worker.backends import docker as docker_backend

DOCKER = {"kind": "docker", "image": "kraft-worker:py"}


@pytest.fixture
def argv_seen(monkeypatch, tmp_path):
    """Every `docker_argv` call's `results` and keywords, the real argv built."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}:{os.environ['PATH']}")
    seen = []
    real = docker_backend.docker_argv

    def spy(cmd, cwd, sandbox, results, **kw):
        argv = real(cmd, cwd, sandbox, results, **kw)
        seen.append({"results": results, "argv": argv, **kw})
        return argv

    monkeypatch.setattr(docker_backend, "docker_argv", spy)
    return seen


def _run(database, run_dirs, cwd, sid):
    return sp.run_task(
        database,
        run_dirs,
        session_id=sid,
        work_item_id="w1",
        node_id="verify",
        hook_point="on.test.run",
        cwd=cwd,
        cmd=["true"],
        sandbox=DOCKER,
    )


async def test_a_sandboxed_launch_mounts_only_its_own_items_results(
    argv_seen, run_dirs, database, tmp_path
):
    """The results folder holds every work item's diffs and findings, so a
    sandboxed session is handed its own item's files and never the folder:
    an older session's under `results/`, a newer one's out of its folder."""
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
        await _run(database, run_dirs, tmp_path, sid)

    s1, s2 = (item_paths.session_files(run_dirs, "w1", sid) for sid in ("s1", "s2"))
    assert argv_seen[1]["results"] == [
        s1.result,
        s1.summary,
        s2.result,
        results / "s2.review.md",
        s2.summary,
    ]
    assert argv_seen[1]["result_path"] == s2.result


async def test_a_session_records_its_folder_and_mounts_only_its_out_files(
    argv_seen, run_dirs, database, tmp_path
):
    """A session started now writes its log and result in its own folder, and
    its row says so. Sandboxed, it is handed its two `out/` files and its
    item's `docs/` to write, and its folder itself is never handed over."""
    await make_item(database, "/r")

    await _run(database, run_dirs, tmp_path, "s1")

    f = item_paths.session_files(run_dirs, "w1", "s1")
    row = database.read(
        lambda c: c.execute("SELECT * FROM worker_sessions WHERE id = 's1'").fetchone()
    )
    assert (row["log_path"], row["result_path"]) == (str(f.log), str(f.result))
    assert f.log.is_file()
    (seen,) = argv_seen
    assert seen["rw_files"] == (f.result, f.summary)
    assert seen["rw_dirs"] == (item_paths.docs_dir(run_dirs, "w1"),)
    assert {f.folder, f.writable}.isdisjoint(Path(p) for p in seen["results"])


@pytest.mark.parametrize("old_home", [True, False], ids=["old-home-exists", "no-old-home"])
async def test_a_sandbox_mounts_its_item_s_home_and_nothing_kraft_keeps(
    argv_seen, run_dirs, database, tmp_path, old_home
):
    """The container's `HOME` is an older item's `sandbox-home/<id>/` while
    it exists, for the chats a paused session resumes from, else the item's
    own folder's. Of that folder it is handed only the home, `docs/` and the
    session's two files: never what was filed, earlier versions, or the
    session folder."""
    await make_item(database, "/r")
    old = run_dirs.base / "sandbox-home" / "w1"
    if old_home:
        old.mkdir(parents=True)
    item = item_paths.item_dir(run_dirs, "w1")
    for part in ("filed/spec.md", "history/spec.1.md"):
        (item / part).parent.mkdir(parents=True, exist_ok=True)
        (item / part).write_text("x")

    await _run(database, run_dirs, tmp_path, "s1")

    home = old if old_home else item / "sandbox-home"
    f = item_paths.session_files(run_dirs, "w1", "s1")
    (seen,) = argv_seen
    assert seen["home"] == home and home.is_dir()
    assert f"HOME={home}" in seen["argv"]
    assert old_home or not old.exists()
    sources = {Path(v.split(":")[0]) for v in _volumes(seen["argv"])}
    assert {p for p in sources if p.is_relative_to(item)} == {
        p for p in (home, item / "docs", f.result, f.summary) if p.is_relative_to(item)
    }


@pytest.mark.parametrize("planted", [None, "symlink"], ids=["fresh", "symlink-replaced"])
def test_a_worker_s_files_are_created_never_written_through_a_link(run_dirs, tmp_path, planted):
    """Before launch Kraft creates the result and summary a worker writes,
    for a sandbox to mount each as a file. A link an earlier turn of a thread
    left there would be mounted as what it points at: the name is replaced,
    and the file it points at is never touched."""
    f = item_paths.session_files(run_dirs, "w1", "s1")
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    if planted:
        f.writable.mkdir(parents=True)
        f.summary.symlink_to(victim)

    sp._create_worker_files(f)

    assert [(p.is_symlink(), p.read_text()) for p in (f.result, f.summary)] == [(False, "")] * 2
    assert victim.read_text() == "untouched"
