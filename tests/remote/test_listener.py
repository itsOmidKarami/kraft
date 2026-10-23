import asyncio
import signal
import sys

import pytest

from kraft.remote import standin
from kraft.remote.transport import StreamTransport


async def _standin(rig, log, sid="s1"):
    with log.open("wb") as out:
        return await asyncio.create_subprocess_exec(
            *standin.argv(rig.run_dirs.base, rig.wid, sid), stdout=out
        )


async def _attached(rig, run):
    while run.spec.key not in rig.listener.attached:
        await asyncio.sleep(0.05)


async def test_the_standin_writes_the_machines_log_to_its_stdout_and_exits_with_the_remote_code(
    rig, tmp_path
):
    await rig.run([sys.executable, "-c", "print('hello'); raise SystemExit(3)"])
    log = tmp_path / "log"
    proc = await _standin(rig, log)
    assert await asyncio.wait_for(proc.wait(), 30) == 3
    assert log.read_text() == "hello\n"


async def test_a_standin_started_before_the_listener_is_there_keeps_trying(rig, tmp_path):
    await rig.listener.close()
    await rig.run([sys.executable, "-c", "print('late')"])
    log = tmp_path / "log"
    proc = await _standin(rig, log)
    await asyncio.sleep(1.5)
    assert proc.returncode is None
    await rig.listener.start()
    assert await asyncio.wait_for(proc.wait(), 30) == 0
    assert log.read_text() == "late\n"


@pytest.mark.parametrize(
    ("sig", "code", "said"),
    [(signal.SIGINT, 130, "got-int"), (signal.SIGTERM, 143, None)],
    ids=["pause-sends-int", "cancel-sends-kill"],
)
async def test_sigint_and_sigterm_cross_to_the_machine(rig, tmp_path, sig, code, said):
    run = await rig.run(["sh", "-c", "trap 'echo got-int; exit 130' INT; sleep 30 & wait"])
    log = tmp_path / "log"
    proc = await _standin(rig, log)
    await _attached(rig, run)
    while run.state != "running":
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.3)
    proc.send_signal(sig)
    assert await asyncio.wait_for(proc.wait(), 30) == code
    if said:
        assert said in log.read_text()


async def test_a_wrong_key_is_refused_and_a_run_the_server_does_not_hold_is_gone(rig):
    port = int((standin.state_dir(rig.run_dirs.base) / standin.PORT_FILE).read_text())
    key = (standin.state_dir(rig.run_dirs.base) / standin.KEY_FILE).read_text().strip()
    for given, session, reason in (
        ("wrong", "s1", "refused"),
        (key, "nobody", f"this server holds no step {rig.wid}/nobody on a machine"),
    ):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        t = StreamTransport(reader, writer)
        await t.send(
            {"t": "attach", "key": given, "item": rig.wid, "session": session, "offset": 0}
        )
        assert await t.recv() == {"t": "gone", "reason": reason}
        await t.close()


async def test_a_config_error_from_the_machine_is_exit_code_78_with_the_reason_in_the_log(
    rig, tmp_path
):
    await rig.run(["claude-not-installed"])
    log = tmp_path / "log"
    proc = await _standin(rig, log)
    assert await asyncio.wait_for(proc.wait(), 30) == 78
    assert "claude-not-installed" in log.read_text()
