"""`kraft.worker.reattach` and a sandbox's memory limit: a session a restart
orphaned is asked whether the limit killed it before its container goes,
whether it is adopted or found dead."""

import os
import subprocess
import sys

import psutil
import pytest
from support.harness import fake_docker_bin

from kraft.worker import reattach

_CHAIN = """
- id: implementation
  kind: exec
  tasks: [{id: implement, kind: agent, harness: fake, prompt: p}]
"""
IMPLEMENT = "implementation.main.implement"
#: A definitely-dead pid, with a start time.
DEAD = (2_000_000_000, 123.0)


@pytest.fixture
def oom_killed(tmp_path, monkeypatch):
    """A fake `docker` whose container `inspect` says a 32m limit killed it,
    until an `rm` removes the container."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}{os.pathsep}{os.environ['PATH']}")
    answer = tmp_path / "inspect"
    answer.write_text("true 33554432\n")
    monkeypatch.setenv("FAKE_DOCKER_INSPECT", str(answer))


def _killed(item):
    return [e["payload"] for e in item.events("sandbox_oom_killed")]


async def test_a_session_found_dead_after_its_limit_killed_it_stops_naming_the_limit(
    item_on, database, run_dirs, oom_killed
):
    """No result file and no word from the container would page a person with
    "no result"; the runtime's own answer names what to change."""
    item = await item_on(_CHAIN, "implementation")
    await item.session("s1", IMPLEMENT, running=DEAD, sandbox="docker")

    summary, _ = await reattach.reattach(database, run_dirs)

    assert summary.resolved_from_file == ["s1"]
    assert [r["status"] for r in item.sessions()] == ["config_error"]
    assert _killed(item) == [{"session_id": "s1", "memory": "32m"}]


async def test_an_adopted_session_its_limit_killed_stops_naming_the_limit(
    item_on, database, run_dirs, oom_killed
):
    item = await item_on(_CHAIN, "implementation")
    # Lives until its stdin closes, so it is still there to adopt.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        start_new_session=True,
        stdin=subprocess.PIPE,
    )
    try:
        await item.session(
            "s1",
            IMPLEMENT,
            running=(proc.pid, psutil.Process(proc.pid).create_time()),
            sandbox="docker",
        )
        _, adopted = await reattach.reattach(database, run_dirs)
        assert list(adopted) == ["s1"]
    finally:
        proc.stdin.close()
        proc.wait()
    await adopted["s1"]

    assert [r["status"] for r in item.sessions()] == ["config_error"]
    assert _killed(item) == [{"session_id": "s1", "memory": "32m"}]
