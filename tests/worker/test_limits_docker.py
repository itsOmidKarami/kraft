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
    rootless or not as it really is, with `alpine:3` pulled. Skips where it
    cannot enforce a memory and a pids limit: that host refuses a limited
    launch, which the unit tier pins."""
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
        pytest.skip(f"e2e: {detected.describe()} enforces {detected.describe_limits()}")
    monkeypatch.setattr(docker, "_RUNTIME", detected)
    return detected


async def test_a_memory_hog_is_killed_by_its_limit_and_recorded(
    runtime, database, run_dirs, tmp_path
):
    if runtime.podman and "v1" in subprocess.check_output(
        [runtime.cli, "info", "--format", "{{.Host.CgroupsVersion}}"], text=True
    ):
        # Seen with podman 4.9.3, conmon 2.1.10 and runc on the dev container:
        # the limit kills the hog, and State.OOMKilled still reads false.
        pytest.skip("e2e: podman on cgroup v1 never reports State.OOMKilled")
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
    assert killed == [{"session_id": "s-hog", "memory": "32m"}]
    # Asked, then removed by name: nothing is left behind without `--rm`.
    gone = subprocess.run(
        [runtime.cli, "inspect", docker.container_name("s-hog")], capture_output=True
    )
    assert gone.returncode != 0


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
