"""An ssh machine, dialled by the server's own `ssh` (a stub that runs here), runs a step."""

import sys

from support.remote import make_rig, stop_daemon
from support.stub_ssh import stub_ssh

from kraft.remote import registry
from kraft.remote import runs as runs_mod


async def test_an_ssh_machine_runs_a_step_through_the_servers_own_ssh(
    tmp_path, monkeypatch, database, run_dirs, item_on
):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "machine-home"))
    stub = stub_ssh(tmp_path / "bin")
    rig = await make_rig(
        tmp_path,
        database,
        run_dirs,
        item_on,
        online=False,
        hub_kwargs={
            "ssh_command": (str(stub / "ssh"),),
            "remote_command": (
                sys.executable,
                "-m",
                "kraft",
                "admin",
                "worker",
                "serve",
                "--stdio",
            ),
        },
        workers=registry.WorkersFile.model_validate(
            {"workers": {"gpu": {"ssh": "gpu", "capacity": 2}}}
        ),
    )
    try:
        await rig.hub.wait_online("gpu")
        run = await rig.done(await rig.run([sys.executable, "-c", "print('over ssh')"]), timeout=60)
        assert run.exit_code == 0 and run.spool.read_text() == "over ssh\n"
        assert (tmp_path / "machine-home" / "remote" / "srv1").is_dir()  # one area per server
    finally:
        stop_daemon(tmp_path / "machine-home")
        await rig.listener.close()
        runs_mod.install(None)
        await rig.hub.close()
