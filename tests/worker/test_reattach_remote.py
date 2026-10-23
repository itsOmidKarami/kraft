import asyncio
import sys

import psutil

from kraft import ids, store
from kraft.worker import reattach

DEAD = (2_000_000_000, 123.0)


async def test_a_remote_session_whose_standin_died_gets_a_new_one_that_resumes_at_the_logs_size(
    rig, database, run_dirs
):
    script = (
        "import os, time\nfor i in range(1, 6):\n"
        "    print(f'line{i}', flush=True); time.sleep(0.3)\n"
        "open(os.environ['KRAFT_RESULT_PATH'], 'w').write('{\"status\": \"done\"}')"
    )
    run = await rig.run([sys.executable, "-c", script])
    f = rig.files("s1")
    await database.write(
        lambda c: store.create_session(
            c,
            id="s1",
            work_item_id=rig.wid,
            node_id="build",
            hook_point="build.main.a",
            log_path=str(f.log),
            result_path=str(f.result),
            runner="gpu",
        )
    )
    await database.write(lambda c: store.session_running(c, "s1", *DEAD, work_item_id=rig.wid))
    while run.offset < 12:
        await asyncio.sleep(0.05)
    f.log.write_bytes(run.spool.read_bytes()[:12])  # what the dead stand-in had written
    summary, adopted = await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)
    assert summary.adopted == [f"{rig.wid}/s1"]
    new_pid = database.read(
        lambda c: c.execute("SELECT pid FROM worker_sessions WHERE id='s1'").fetchone()[0]
    )
    assert new_pid != DEAD[0]
    await asyncio.wait_for(adopted[ids.qualified(rig.wid, "s1")], 60)
    assert f.log.read_text() == "".join(f"line{i}\n" for i in range(1, 6)) == run.spool.read_text()
    assert (
        not psutil.pid_exists(new_pid) or psutil.Process(new_pid).status() == psutil.STATUS_ZOMBIE
    )
