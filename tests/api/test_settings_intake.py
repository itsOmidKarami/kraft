"""Settings > Auto-intake: the saves racing the intake poller, and a read
that shows the file on disk. Moved from the retired steering-file tests."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from kraft import config
from kraft import intake as intake_mod

pytestmark = pytest.mark.api_client(default_setup=False)


def test_two_overlapping_intake_saves_leave_exactly_one_live_poller(client):
    """The swap awaits the old task's cancellation, so without a lock both
    savers read the same old task, both start a poller, and only the last
    assignment is reachable. The other ticks on past shutdown -- lifespan
    cancels `app.state.intake_task` and nothing else -- and two pollers reading
    `_known_beads` before either inserts can double-start the same bead.
    """
    app = client.app
    body = {
        "enabled": True,
        "interval_s": 60,
        "max_concurrent": 1,
        "priority_ceiling": 2,
        "repos": [],
    }
    started: list[asyncio.Task] = []

    async def never_returning_poller(_app):
        started.append(asyncio.current_task())
        # cancellation is the only way out, which is exactly what the swap owes
        # every poller it retires
        await asyncio.Event().wait()

    async def scenario():
        app.state.intake = dict(config.INTAKE_DEFAULT)
        app.state.intake_task = None
        app.state.intake_lock = asyncio.Lock()
        monkey = intake_mod.poller
        intake_mod.poller = never_returning_poller
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://kraft") as ac:
                # A poller has to already be live, or neither save reaches the
                # `await` that opens the window and the race cannot show.
                assert (await ac.put("/api/intake", json=body)).status_code == 200
                assert app.state.intake_task is not None
                a, b = await asyncio.gather(
                    ac.put("/api/intake", json=body), ac.put("/api/intake", json=body)
                )
            assert (a.status_code, b.status_code) == (200, 200)
            # let any cancellation delivered above actually land
            await asyncio.sleep(0)
            live = app.state.intake_task
            orphans = [t for t in started if t is not live and not t.done()]
            assert orphans == [], f"{len(orphans)} poller(s) left running unreachably"
            assert live is not None and not live.done()
            live.cancel()
            await asyncio.gather(live, return_exceptions=True)
        finally:
            intake_mod.poller = monkey
            # These tasks belong to this loop, not the fixture's; leaving one on
            # app.state would have lifespan shutdown gather it from the wrong one.
            app.state.intake_task = None

    asyncio.run(scenario())


def test_get_intake_reads_the_file_not_the_cached_state(client, templates_dir):
    """`intake.yaml` was hand-edited until this screen existed, so the screen
    has to show what is on disk. Returning `app.state` hides an edit made since
    boot, and the next save silently overwrites it."""
    (templates_dir / "intake.yaml").write_text(
        "enabled: false\ninterval_s: 900\nmax_concurrent: 4\npriority_ceiling: 1\nrepos: []\n"
    )
    body = client.get("/api/intake").json()
    assert body["interval_s"] == 900
    assert body["max_concurrent"] == 4
