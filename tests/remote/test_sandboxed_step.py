"""A sandboxed step on a machine, against a real sandbox: the machine builds the item's sandbox
itself, the worker's commit and its `kraft` call reach the server through the connection, and
nothing the worker writes under its checkout's `.git` is read by the git that updates, sweeps
and bundles on the machine."""

import os
import subprocess
import sys
import uuid

import pytest
from support.harness import entry_of, git
from support.sandbox_image import build_git_image
from worker.test_egress_docker import _pulled

from kraft.adapters import subprocess as sp
from kraft.worker import channel
from kraft.worker.backends import docker, seatbelt

OS_SANDBOX = {"kind": "os", "network": {"runtime": {"allow": ["x.io"]}}}
_RUN = uuid.uuid4().hex[:8]  # a name no other run of this test shares
HOOKS = f"/tmp/kraft-e2e-evil-hooks-{_RUN}"
#: Writable by any process of the operator's and by nothing in the sandbox.
ESCAPE = f"/tmp/kraft-e2e-escape-{_RUN}"

#: The worker: a commit, a `kraft` call through the proxy, and two writes into the repository's
#: config -- from the checkout's `.git` pointer and from the common gitdir it names -- which a
#: sandbox either refuses or keeps in a private copy. Failed writes are not the step's failure.
STEP = f"""
echo made > made.txt && git add made.txt
git -c user.name=w -c user.email=w@x commit -qm "sandbox work"
kraft item progress 1 || exit 3
touch {ESCAPE} || true
git config core.hooksPath {HOOKS} || true
printf '[core]\\n\\thooksPath = {HOOKS}\\n' >> "$(git rev-parse --git-common-dir)/config" || true
tmp=$(mktemp "$KRAFT_RESULT_PATH.XXXXXX") && echo '{{"status": "done"}}' > "$tmp" &&
  mv "$tmp" "$KRAFT_RESULT_PATH" || exit 1
"""


async def _step(rig, sandbox, short_run, monkeypatch, tmp_path):
    # The machine's own state lives in a run dir of its own, short enough for a socket path.
    monkeypatch.setenv("KRAFT_RUN_DIR", str(short_run.base))
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    # What `kraft admin worker serve` does for its process: the backends find their channels here.
    monkeypatch.setattr(channel, "_CURRENT", rig.machine.channels)
    return await sp.run_task(
        rig.database,
        rig.run_dirs,
        session_id="s1",
        work_item_id=rig.wid,
        node_id="build",
        hook_point="build.main.a",
        cmd=["sh", "-c", STEP],
        cwd=rig.repo,
        repo_entry=entry_of({"setup_command": ""}),
        sandbox=sandbox,
        runner="gpu",
        poll_s=0.02,
    )


def _machine_bare(rig):
    return rig.machine._area / "items" / rig.wid / "repo.git"


def _assert_it_ran_in_the_machines_sandbox(rig, status):
    log = rig.files("s1").log.read_text()
    assert status == "done", log
    assert git(rig.repo, "log", "-1", "--format=%s", rig.branch).strip() == "sandbox work"
    assert (rig.repo / "made.txt").read_text() == "made\n"
    assert ("POST", f"/api/work-items/{rig.wid}/progress", f"{rig.wid}/s1") in rig.calls
    config = subprocess.run(
        ["git", "-C", str(_machine_bare(rig)), "config", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
    )
    assert config.stdout.strip() == "" and not os.path.exists(HOOKS)
    assert not os.path.exists(ESCAPE), "the step ran outside a sandbox"


@pytest.mark.e2e("sandbox-exec")
@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS's")
async def test_a_step_runs_in_a_seatbelt_sandbox_the_machine_built(
    rig, short_run, monkeypatch, tmp_path
):
    if reason := seatbelt.probe_darwin():
        pytest.skip(f"e2e: {reason}")
    status = await _step(rig, OS_SANDBOX, short_run, monkeypatch, tmp_path)
    _assert_it_ran_in_the_machines_sandbox(rig, status)


@pytest.mark.e2e("docker")
async def test_a_step_runs_in_a_docker_sandbox_the_machine_built(
    rig, short_run, monkeypatch, tmp_path
):
    image = build_git_image("docker", tmp_path, monkeypatch, curl=True)
    _pulled("docker", docker._forward._sandbox_host(os.environ).relay_image)  # a launch never pulls
    sandbox = {"kind": "docker", "image": image, "network": {"runtime": {"allow": ["x.io"]}}}
    status = await _step(rig, sandbox, short_run, monkeypatch, tmp_path)
    _assert_it_ran_in_the_machines_sandbox(rig, status)
