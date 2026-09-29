"""`sandbox.resources` against a real container runtime, docker and podman:
the one proof that the limits `docker_argv` asks for bite, and that an OOM
kill is read back off the runtime and recorded
(`sandbox-limits-never-silently-drop`)."""

import os
import subprocess
from pathlib import Path

import pytest

from kraft import events, store
from kraft.adapters import subprocess as sp
from kraft.paths import default_templates_dir
from kraft.worker.backends import docker

IMAGE = "docker.io/library/alpine:3"


def _unavailable(cli: str, why: str):
    # Where the runtime is required, an unreachable daemon or registry is a
    # failure, never a quiet skip past KRAFT_E2E_REQUIRE.
    if cli in os.environ.get("KRAFT_E2E_REQUIRE", "").split(","):
        pytest.fail(why)
    pytest.skip(why)


@pytest.fixture(
    params=[
        pytest.param("docker", marks=pytest.mark.e2e("docker")),
        pytest.param("podman", marks=pytest.mark.e2e("podman")),
    ]
)
def runtime(request, monkeypatch) -> docker.Runtime:
    """The runtime under test made this machine's through `sandbox.yaml`,
    rootless or not as it really is, with `alpine:3` pulled. One that cannot
    enforce a memory and a pids limit is unavailable here, as one that cannot
    pull is: a failure where KRAFT_E2E_REQUIRE names it."""
    cli = request.param
    have = subprocess.run([cli, "image", "inspect", IMAGE], capture_output=True)
    if have.returncode != 0:
        pulled = subprocess.run([cli, "pull", "-q", IMAGE], capture_output=True, text=True)
        if pulled.returncode != 0:
            _unavailable(cli, f"e2e: {cli} could not pull {IMAGE}: {pulled.stderr.strip()}")
    templates = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    templates.mkdir(parents=True, exist_ok=True)
    (templates / "sandbox.yaml").write_text(f"cli: {cli}\n")
    detected = docker.detect_runtime()
    if not {"memory", "pids"} <= detected.limits:
        _unavailable(cli, f"e2e: {detected.describe()} enforces {detected.describe_limits()}")
    monkeypatch.setattr(docker, "_RUNTIME", detected)
    return detected


async def test_a_memory_hog_is_killed_by_its_limit_and_recorded(
    runtime, database, run_dirs, tmp_path
):
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )
    work = tmp_path / "work"
    work.mkdir()

    status = await sp.run_task(
        database,
        run_dirs,
        session_id="s-hog",
        work_item_id="w1",
        node_id="verify",
        hook_point="on.test.run",
        cmd=["sh", "-c", 'x=a; while true; do x="$x$x"; done'],
        cwd=work,
        sandbox={"kind": "docker", "image": IMAGE, "resources": {"memory": "32m"}},
    )

    assert status == "config_error"
    killed = [
        e["payload"]
        for e in database.read(lambda c: events.read_after(c, 0, "w1"))
        if e["type"] == sp.SANDBOX_OOM_KILLED
    ]
    # Docker on cgroup v2 can drop its OOM flag (moby#41929): then the kill
    # is recorded unconfirmed, the stop the same.
    assert killed in (
        [{"session_id": "s-hog", "memory": "32m", "confirmed": confirmed}]
        for confirmed in (True, False)
    )
    # Asked, then removed by name: nothing is left behind without `--rm`.
    gone = subprocess.run(
        [runtime.cli, "inspect", docker.container_name("s-hog")], capture_output=True
    )
    assert gone.returncode != 0
    # Podman on cgroup v1 leaves an `oom` file where its client ran: never the
    # worktree, and gone with the session.
    assert not (work / "oom").exists() and not docker.client_dir("s-hog").exists()


@pytest.mark.parametrize(("pids", "forks"), [(16, False), (256, True)], ids=["bites", "room"])
def test_a_pids_limit_bites(runtime, tmp_path, pids, forks):
    """Forty children at once under a limit of sixteen: the shell cannot
    fork them all. The same script under a roomy limit shows it is the limit."""
    script = "for i in $(seq 40); do sleep 1 & done; wait"
    name = f"kraft-pids-{pids}-{os.getpid()}"
    argv = docker.docker_argv(
        ["sh", "-c", script],
        tmp_path,
        {"kind": "docker", "image": IMAGE, "resources": {"pids": pids}},
        None,
        name=name,
    )
    try:
        ran = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    finally:
        subprocess.run([runtime.cli, "rm", "-f", name], capture_output=True)
    assert f"--pids-limit={pids}" in argv
    assert (ran.returncode == 0 and "fork" not in ran.stderr) is forks, ran.stderr
