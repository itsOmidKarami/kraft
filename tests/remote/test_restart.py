import asyncio
import sys

from kraft import store
from kraft.remote import standin


async def _row(rig, sid="s1"):
    f = rig.files(sid)
    await rig.database.write(
        lambda c: store.create_session(
            c,
            id=sid,
            work_item_id=rig.wid,
            node_id="build",
            hook_point="build.main.a",
            log_path=str(f.log),
            result_path=str(f.result),
            runner="gpu",
        )
    )
    await rig.database.write(lambda c: store.session_running(c, sid, 1, 0.0, work_item_id=rig.wid))


async def test_a_step_survives_the_server_restarting_and_its_log_is_whole(rig, tmp_path):
    script = (
        "import time\nfor i in range(1, 7):\n    print(f'line{i}', flush=True); time.sleep(0.25)"
    )
    run = await rig.run([sys.executable, "-c", script])
    await _row(rig)
    log = tmp_path / "log"
    with log.open("wb") as out:
        proc = await asyncio.create_subprocess_exec(
            *standin.argv(rig.run_dirs.base, rig.wid, "s1"), stdout=out
        )
        while run.offset < 6 or run.spec.key not in rig.listener.attached:
            await asyncio.sleep(0.05)
        await rig.restart_server()
        assert await asyncio.wait_for(proc.wait(), 30) == 0
    assert log.read_text() == "".join(f"line{i}\n" for i in range(1, 7))
    assert rig.hub.slots_used("gpu") == 0


async def test_a_step_the_server_cannot_recover_is_told_so(rig):
    assert await rig.runs.recover(rig.wid, "never-launched") is None
