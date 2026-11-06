"""Step outputs on a remote machine: the writer's folder is made there, held to the
cap it launched under before it is sent back, and deleted once the server has it;
a reader's snapshot arrives a file at a time."""

from __future__ import annotations

import sys
from dataclasses import replace

import pytest
from support.remote import until

from kraft import item_paths, outputs
from kraft.adapters import subprocess as sp
from kraft.paths import RunDirs
from kraft.remote import launch

WRITER = "build.main.a"


def declared(rig, sid: str = "s1", *, cap: int = 1000):
    files = rig.files(sid)
    return files, outputs.declare(files, outputs.Decl(WRITER, ("a.txt",), ("K",), cap))


def write_script(size: int) -> list[str]:
    """A command writing the declared file and variable, and `size` bytes more."""
    body = (
        "import os, pathlib\n"
        "out = pathlib.Path(os.environ['KRAFT_OUTPUT_DIR'])\n"
        "(out / 'a.txt').write_text('kept')\n"
        "(out / 'variables.env').write_text('K=1\\n')\n"
        f"(out / 'big').write_bytes(b'x' * {size})\n"
    )
    return [sys.executable, "-c", body]


async def run_writer(rig, sid: str = "s1", *, size: int = 10, cap: int = 1000, **kw):
    files, folder = declared(rig, sid, cap=cap)
    status = await sp.run_task(
        rig.database,
        rig.run_dirs,
        session_id=sid,
        work_item_id=rig.wid,
        node_id="build",
        hook_point=WRITER,
        cmd=write_script(size),
        cwd=rig.repo,
        env={"KRAFT_OUTPUT_DIR": str(folder)},
        runner="gpu",
        poll_s=0.02,
        **kw,
    )
    return status, files, rig.runs.find(rig.wid, sid)


def snapshot(rig):
    return item_paths.snapshot_dir(rig.run_dirs, rig.wid, WRITER)


async def test_jobspec_carries_cap_and_outputs_dir(rig):
    status, files, run = await run_writer(rig, cap=12345)

    assert status == "done"
    assert run.spec.outputs_max_bytes == 12345
    assert str(item_paths.writer_dir(files)) in run.spec.dirs
    assert str(item_paths.writer_dir(files)) not in run.spec.rw, "out/ is walked once"
    assert (snapshot(rig) / "a.txt").read_text() == "kept"
    assert (snapshot(rig) / "variables.env").read_bytes() == b"K=1\n"


async def test_machine_refuses_oversize_outputs_before_packing(rig):
    status, files, run = await run_writer(rig, size=5000, cap=1000)

    assert outputs.was_refused(files), "recorded beside the session, for a restart"
    assert not (item_paths.writer_dir(files) / "big").exists(), "never sent back"
    assert status == "failed"
    assert "over the size or entry limit" in files.log.read_text()
    assert not snapshot(rig).exists()


async def test_server_rechecks_cap_from_old_machine(rig, monkeypatch):
    """A machine that predates the cap sends everything back; the server's own
    check fails the writer, for a size between the cap and what a transfer holds."""
    build = launch.build
    monkeypatch.setattr(launch, "build", lambda **kw: replace(build(**kw), outputs_max_bytes=None))

    status, files, run = await run_writer(rig, size=5 << 20, cap=1 << 20)

    assert not outputs.was_refused(files)
    assert status == "failed"
    assert "limit" in files.log.read_text()
    assert not snapshot(rig).exists()


async def test_delivered_deletes_machine_outputs(rig):
    status, files, run = await run_writer(rig)
    assert status == "done"
    key = run.spec.key

    await until(lambda: not rig.machine.holds(key))

    on_machine = item_paths.session_files(RunDirs(rig.machine._area / "run"), rig.wid, "s1")
    assert not item_paths.writer_dir(on_machine).exists()


@pytest.mark.parametrize(
    "name", ["report.xml", "report final.xml", "résumé.txt"], ids=["plain", "space", "non-ascii"]
)
async def test_remote_reader_gets_one_ro_entry_per_snapshot_file(rig, name):
    folder = item_paths.snapshot_dir(rig.run_dirs, rig.wid, WRITER)
    folder.mkdir(parents=True)
    (folder / name).write_text("data")
    (folder / "variables.env").write_text("K=1\n")
    (folder / "sub").mkdir()
    (folder / "sub" / "deep.txt").write_text("deeper")
    (folder / "link").symlink_to(rig.repo)
    script = (
        "import os, pathlib\n"
        "r = pathlib.Path(os.environ['KRAFT_READS_DIR']) / " + repr(WRITER) + "\n"
        "print(sorted(p.relative_to(r).as_posix() for p in r.rglob('*') if p.is_file()))\n"
        f"print((r / {name!r}).read_text())\n"
    )

    status = await sp.run_task(
        rig.database,
        rig.run_dirs,
        session_id="s2",
        work_item_id=rig.wid,
        node_id="build",
        hook_point="build.main.b",
        cmd=[sys.executable, "-c", script],
        cwd=rig.repo,
        env={"KRAFT_READS_DIR": str(item_paths.outputs_dir(rig.run_dirs, rig.wid))},
        ro_paths=(str(folder),),
        runner="gpu",
        poll_s=0.02,
    )

    run = rig.runs.find(rig.wid, "s2")
    assert status == "done"
    ro = set(run.spec.ro)
    files = {str(p) for p in (folder / name, folder / "variables.env", folder / "sub" / "deep.txt")}
    assert files <= ro, "one entry for each regular file"
    assert not {str(folder), str(folder / "sub"), str(folder / "link")} & ro
    assert rig.files("s2").log.read_text().splitlines() == [
        str(sorted([name, "sub/deep.txt", "variables.env"])),
        "data",
    ]
