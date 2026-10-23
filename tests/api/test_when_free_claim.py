"""What an update's claim does to a start: a gate decision answers 503 and
changes nothing, and a start already past its door closes its coroutine."""

from __future__ import annotations

import asyncio
import gc
import warnings
from types import SimpleNamespace

import pytest
from support.api import _poll_events, _post_default

from kraft import when_free
from kraft.api import deps


async def _idle():
    await asyncio.sleep(3600)


def test_approve_during_claim(client, repo, monkeypatch):
    """An update holds the moment: an approval answers 503 and changes nothing,
    so the gate is still there to approve once the server is back."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    monkeypatch.setattr(when_free, "_claimed", True)

    r = client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")

    assert (r.status_code, r.json()["detail"]) == (503, when_free.MESSAGE)
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] == "needs_human"
    events = client.get(f"/api/work-items/{wid}/events").json()
    assert not any(e["type"] == "gate_approved" for e in events)

    monkeypatch.setattr(when_free, "_claimed", False)
    assert client.post(f"/api/work-items/{wid}/gates/spec_approval/approve").status_code == 200


async def test_start_under_claim():
    """An update holds the moment: a start that got past its door closes its
    coroutine, raises `Claimed` and leaves `app.state.tasks` as it was."""
    app = SimpleNamespace(state=SimpleNamespace(tasks={}))
    when_free._claimed = True
    coro = _idle()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(when_free.Claimed):
            deps.spawn(app, "w1", coro)
        gc.collect()
    assert not any("was never awaited" in str(w.message) for w in caught)
    assert app.state.tasks == {}
    assert coro.cr_frame is None  # closed, not left to be collected

    when_free.release_claim()
    task = deps.spawn(app, "w1", _idle())
    assert app.state.tasks == {"w1": task}
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
