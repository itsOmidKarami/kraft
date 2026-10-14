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
from kraft.worker.backends import docker

_CHAIN = """
- id: implementation
  kind: exec
  tasks: [{id: implement, kind: agent, harness: fake, prompt: p}]
"""
IMPLEMENT = "implementation.main.implement"
#: A session id minted before per-item ids.
OLD = "8cbfe6e27c1044b3e445c0f0d726357d"
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
    # Listed by the startup sweep, as real docker lists an exited container.
    listed = tmp_path / "ps"
    listed.write_text("kraft-w1-s1\n")
    monkeypatch.setenv("FAKE_DOCKER_PS", str(listed))


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
    assert _killed(item) == [{"session_id": "s1", "memory": "32m", "confirmed": True}]


@pytest.mark.parametrize(
    ("inspect", "paused", "confirmed"),
    [
        ("true 33554432 137", False, True),
        ("false 33554432 137", False, False),
        ("false 33554432 137", True, None),
        ("true 33554432 137", True, True),
    ],
    ids=["confirmed", "unconfirmed", "kraft-stopped-it", "kraft-stopped-it-but-flagged"],
)
async def test_an_adopted_session_its_limit_killed_stops_naming_the_limit(
    item_on, database, run_dirs, oom_killed, tmp_path, monkeypatch, inspect, paused, confirmed
):
    """Docker can drop its OOM flag, so exit 137 under a limit counts
    unconfirmed -- unless Kraft stopped the session itself, which marks the
    row `paused` before it signals: then 137 may be Kraft's own doing."""
    monkeypatch.setattr(docker, "OOM_FLAG_GRACE_S", 0)
    (tmp_path / "inspect").write_text(inspect + "\n")
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
        assert list(adopted) == ["w1-s1"]
        if paused:
            await database.write(
                lambda c: c.execute("UPDATE worker_sessions SET status = 'paused'")
            )
    finally:
        proc.stdin.close()
        proc.wait()
    await adopted["w1-s1"]

    recorded = {"session_id": "s1", "memory": "32m", "confirmed": confirmed}
    assert _killed(item) == ([] if confirmed is None else [recorded])
    assert item.sessions()[0]["status"] == ("paused" if paused else "config_error")


@pytest.mark.parametrize(
    ("sid", "container"),
    [("p3n8w2", "kraft-w1-p3n8w2"), (OLD, f"kraft-{OLD}")],
    ids=["per-item-session", "older-session"],
)
async def test_an_adopted_session_is_found_under_the_name_it_started_with(
    item_on, database, run_dirs, oom_killed, tmp_path, monkeypatch, sid, container
):
    """A restart across the upgrade: the startup sweep keeps the container
    a live session started under, the OOM check asks it and the close
    removes it -- its item and its id, or a session's from before per-item
    ids, its id alone. Another item's session with the same id is not it."""
    other = "kraft-b4t1z8re-p3n8w2"
    (tmp_path / "ps").write_text(f"{container}\n{other}\n")
    calls = tmp_path / "calls"
    monkeypatch.setenv("FAKE_DOCKER_CALLS", str(calls))
    item = await item_on(_CHAIN, "implementation")
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        start_new_session=True,
        stdin=subprocess.PIPE,
    )
    try:
        live = (proc.pid, psutil.Process(proc.pid).create_time())
        await item.session(sid, IMPLEMENT, running=live, sandbox="docker")
        _, adopted = await reattach.reattach(database, run_dirs)
        assert list(adopted) == [container.removeprefix("kraft-")]
    finally:
        proc.stdin.close()
        proc.wait()
    await adopted[container.removeprefix("kraft-")]

    made = calls.read_text().splitlines()
    removed = [c.removeprefix("rm -f ") for c in made if c.startswith("rm -f ")]
    assert removed == [other, container]
    assert any(c.startswith("inspect ") and c.endswith(f" {container}") for c in made)
