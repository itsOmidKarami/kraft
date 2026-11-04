"""Every lifecycle door against every item state: the server's half of
`lifecycle_doors.json`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.api import _cancel_task, _in_state, _spawn_never_returning

DOORS = json.loads((Path(__file__).parent / "lifecycle_doors.json").read_text())


@pytest.mark.parametrize("door", sorted(DOORS["doors"]))
def test_a_door_takes_only_the_states_this_table_lists(client, repo, monkeypatch, door):
    """The server's half of `lifecycle_doors.json`, which the web interface's
    surfaces are checked against too (`frontend/src/ng/item/status.test.ts`):
    a door either takes a state or answers 409, and a 409 writes nothing. A
    live walk (a gate's auto-review, a walk still unwinding) is refused by
    every door that would start one (Kraft-s7c04.20: `/escalate` into a
    worktree a review agent was still writing to). Nothing taken runs here:
    `spawn` closes what it is handed, and the forge's reopen is a no-op.

    One server per door, each state tried on a fresh item in turn (a server
    per state was four fifths of this grid's time). Every state that fails is
    named, not only the first."""
    from kraft.api import deps
    from kraft.api.routes import lifecycle

    monkeypatch.setattr(lifecycle.forge_mod.run, "backend_for", lambda *_: "fake")
    monkeypatch.setattr(
        lifecycle.forge_mod.run,
        "resolve",
        lambda _b: SimpleNamespace(reopen_mr=lambda **_: asyncio.sleep(0)),
    )
    monkeypatch.setattr(lifecycle, "_terminate", lambda pid, **_: None)
    spec, wrong = DOORS["doors"][door], []
    for state, row in DOORS["states"].items():
        wid = _in_state(client, repo, row)
        if row.get("walk"):
            client.portal.call(_spawn_never_returning, client.app, wid)
        try:
            # Only the door's own call runs with spawn closed: the walk above
            # registers its task through the real one.
            with monkeypatch.context() as spawn_closed:
                spawn_closed.setattr(deps, "spawn", lambda app, w, coro: deps.discard(coro))
                before = client.get(f"/api/work-items/{wid}/events").json()
                r = client.post(f"/api/work-items/{wid}/{spec['path']}", json=spec["body"])
            if state in spec["takes"]:
                assert r.status_code == 200, r.text
            else:
                assert r.status_code == 409, r.text
                assert client.get(f"/api/work-items/{wid}").json()["status"] == row["status"]
                assert client.get(f"/api/work-items/{wid}/events").json() == before
        except AssertionError as e:
            wrong.append(f"{state}: {e}")
        finally:
            if row.get("walk"):
                client.portal.call(_cancel_task, client.app, wid)
    assert not wrong, f"{door} takes the wrong states:\n" + "\n".join(wrong)
