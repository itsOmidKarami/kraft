"""The running server's own wait scheduler: started by the lifespan, and able to
pick up a wait that was parked before the server (re)started. Every other wait
test drives `waits.tick` directly, so without this nothing fails when startup
stops launching `waits.poller` and every waiting item hangs (Kraft-bgtg0)."""

from __future__ import annotations

import asyncio
import time

import pytest
from support.harness import v1_chain, v1_item

from kraft import db, store, waits
from kraft.adapters import forge
from kraft.paths import RunDirs
from kraft.templates.models import WaitBounds

TASK = "ci.main.ci"


@pytest.fixture
def parked_before_restart(tmp_path, repo, monkeypatch):
    """A work item left `waiting` on its CI by a server that has since
    stopped: its wait open (started, one pending observation) and already
    due. Listed before `client`, so it is on disk when the lifespan starts,
    and the scheduler ticks fast enough for a test to see it."""
    monkeypatch.setattr(waits, "_INTERVAL_S", 0.05)
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge.run, "_DEV_FAKE", fake)
    monkeypatch.setattr(forge.run, "backend_for", lambda backend, repo_forge: "fake")
    chain = v1_chain(
        [{"id": "ci", "kind": "exec", "tasks": [{"id": "ci", "kind": "forge", "target": "mr.ci"}]}],
        repo=repo,
    )

    async def seed():
        database = await db.Database.open(RunDirs(tmp_path / "run").ensure().db)
        try:
            await v1_item(database, chain, repo=repo)
            await database.write(lambda c: store.enter_node(c, "w1", "ci"))
            await database.write(
                lambda c: waits.observe(
                    c,
                    "w1",
                    node_id="ci",
                    task=TASK,
                    kind="mr.ci",
                    bounds=WaitBounds.from_seconds(timeout=3600, initial=30, maximum=300),
                    condition="ci",
                    state="pending",
                    result="pending",
                )
            )
            await database.write(
                lambda c: store.mark_waiting(c, "w1", "ci", "2000-01-01T00:00:00+00:00")
            )
        finally:
            await database.close()

    asyncio.run(seed())
    return fake


def test_the_server_s_scheduler_observes_a_wait_parked_before_it_started(
    parked_before_restart, client
):
    deadline = time.monotonic() + 30
    while (item := client.get("/api/work-items/w1").json())["status"] != "completed":
        assert time.monotonic() < deadline, f"never woken: {item['status']}"
        time.sleep(0.05)

    evts = client.get("/api/work-items/w1/events").json()
    trail = [
        (e["type"], e["payload"].get("observation"))
        for e in evts
        if e["type"].startswith("external_wait_") and e["payload"]["task"] == TASK
    ]
    # One instance across the restart: its second observation, not a new wait.
    assert trail == [
        ("external_wait_started", None),
        ("external_wait_observed", 1),
        ("external_wait_observed", 2),
        ("external_wait_ended", None),
    ]
