"""Cancel preview, closing the MR on cancel (B4), and reopening an MR an item
stopped on when it was closed externally (B8). A sibling of
test_lifecycle.py."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from support.api import _force_node, _set_status

# --- D: cancel preview and close the MR on cancel (B4) ----------------------


def _fake_forge_cancel(monkeypatch):
    from kraft.adapters import forge as forge_mod

    fake = forge_mod.FakeForge()
    monkeypatch.setattr("kraft.adapters.forge.backend_for", lambda *a, **k: "fake")
    monkeypatch.setattr("kraft.adapters.forge.resolve", lambda name: fake)
    return fake


def _stopped_item_with_mr(client, repo, fake):
    """A `needs_human` item on a forced node (`_force_node` skips the real
    walk, as the terminal-action tests do), with an open merge request
    registered on `fake` under the item's own branch -- the shape `_mr_ref`
    and `find_mr` both need to agree the ref is this item's."""
    from kraft import events, store

    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]
    _force_node(wid, "verification", "needs_human")
    db = client.app.state.db
    row = client.portal.call(
        db.read, lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    worktree = client.app.state.run_dirs.worktrees / wid

    async def seed():
        mr = await fake.open_mr(
            repo=worktree, branch=store.branch_for(row), base="main", title="t", body="b"
        )
        await db.write(
            lambda c: events.append(c, wid, "mr_opened", {"number": mr.number, "url": mr.url})
        )

    client.portal.call(seed)
    return wid


def test_cancel_preview_of_a_running_item_names_its_session(client, repo, monkeypatch):
    from kraft import store

    fake = _fake_forge_cancel(monkeypatch)
    wid = _stopped_item_with_mr(client, repo, fake)
    db = client.app.state.db
    client.portal.call(
        db.write,
        lambda c: store.create_session(
            c,
            id="s-running",
            work_item_id=wid,
            node_id="verification",
            hook_point="verification.implement",
            log_path="/l",
            result_path="/r",
        ),
    )
    client.portal.call(db.write, lambda c: store.session_running(c, "s-running", 4242, 1.0))
    _set_status(wid, "active")

    r = client.get(f"/api/work-items/{wid}/cancel-preview")

    assert r.status_code == 200, r.text
    body = r.json()
    started_at = body["running"].pop("started_at")
    assert started_at, "the card says how long the attempt it would stop has run"
    assert body["running"] == {
        "node": "verification",
        "task": "verification.implement",
        "attempt": 1,
    }
    assert body["mr"]["state"] == "open"


def test_cancel_preview_of_a_stopped_item_names_its_mr_and_what_cancel_would_keep(
    client, repo, monkeypatch
):
    from kraft import store

    fake = _fake_forge_cancel(monkeypatch)
    wid = _stopped_item_with_mr(client, repo, fake)

    r = client.get(f"/api/work-items/{wid}/cancel-preview")

    assert r.status_code == 200, r.text
    body = r.json()
    row = client.portal.call(
        client.app.state.db.read,
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone(),
    )
    assert body["running"] is None
    assert body["kept"]["branch"] == store.branch_for(row)
    assert body["mr"] == {"ref": 1, "url": "http://fake.forge/1", "state": "open"}
    assert body["spend"]["spent_usd"] == 0.0

    client.portal.call(
        client.app.state.db.write,
        lambda c: c.execute(
            "UPDATE work_items SET bead_id = ?, implements_beads = ? WHERE id = ?",
            ("kraft-own", '["kraft-impl", "kraft-own"]', wid),
        ),
    )
    # The beads a hand completion would close: its own, then those it implements, none twice.
    assert client.get(f"/api/work-items/{wid}/cancel-preview").json()["beads"] == [
        "kraft-own",
        "kraft-impl",
    ]


def test_cancel_preview_survives_a_forge_that_cannot_answer(client, repo, monkeypatch):
    from kraft.adapters import forge as forge_mod

    fake = _fake_forge_cancel(monkeypatch)
    wid = _stopped_item_with_mr(client, repo, fake)

    async def _boom(*, repo, branch):
        raise forge_mod.ForgeError("gh: rate limited")

    monkeypatch.setattr(fake, "find_mr", _boom)

    r = client.get(f"/api/work-items/{wid}/cancel-preview")

    assert r.status_code == 200, r.text
    assert r.json()["mr"] == {"ref": 1, "url": "http://fake.forge/1", "state": None}


def test_cancel_preview_of_an_ended_item_answers_409(client, repo, monkeypatch):
    fake = _fake_forge_cancel(monkeypatch)
    wid = _stopped_item_with_mr(client, repo, fake)
    assert client.post(f"/api/work-items/{wid}/cancel", json={"reason": "no"}).status_code == 200

    assert client.get(f"/api/work-items/{wid}/cancel-preview").status_code == 409


def test_cancel_preview_of_an_unknown_item_answers_404(client):
    assert client.get("/api/work-items/nope/cancel-preview").status_code == 404


def test_cancel_with_close_mr_closes_the_open_merge_request_and_emits_the_event(
    client, repo, monkeypatch
):
    fake = _fake_forge_cancel(monkeypatch)
    wid = _stopped_item_with_mr(client, repo, fake)

    r = client.post(
        f"/api/work-items/{wid}/cancel", json={"reason": "no longer needed", "close_mr": True}
    )

    assert r.status_code == 200, r.text
    assert r.json()["close_mr"] == {"ok": True}
    assert fake.closed == [1]
    evs = [
        e["payload"]
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == "mr_closed"
    ]
    assert evs == [{"ref": 1, "url": "http://fake.forge/1", "by": "cancel"}]


def test_cancel_with_close_mr_but_no_open_mr_reports_the_error_and_still_cancels(
    client, repo, monkeypatch
):
    _fake_forge_cancel(monkeypatch)
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]
    _force_node(wid, "verification", "needs_human")

    r = client.post(f"/api/work-items/{wid}/cancel", json={"reason": "no", "close_mr": True})

    assert r.status_code == 200, r.text
    assert r.json()["close_mr"] == {"ok": False, "error": "no open merge request"}
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "abandoned"


def test_cancel_with_close_mr_still_cancels_when_the_forge_call_fails(client, repo, monkeypatch):
    from kraft.adapters import forge as forge_mod

    fake = _fake_forge_cancel(monkeypatch)
    wid = _stopped_item_with_mr(client, repo, fake)

    async def _boom(*, repo, mr):
        raise forge_mod.ForgeError("gh: rate limited")

    monkeypatch.setattr(fake, "close_mr", _boom)

    r = client.post(f"/api/work-items/{wid}/cancel", json={"reason": "no", "close_mr": True})

    assert r.status_code == 200, r.text
    assert r.json()["close_mr"] == {"ok": False, "error": "gh: rate limited"}
    assert r.json()["status"] == "abandoned"
    assert all(e["type"] != "mr_closed" for e in client.get(f"/api/work-items/{wid}/events").json())


def test_cancel_without_close_mr_behaves_as_today(client, repo, monkeypatch):
    """`close_mr` defaults off: cancel never touches the forge on its own."""
    fake = _fake_forge_cancel(monkeypatch)
    wid = _stopped_item_with_mr(client, repo, fake)

    r = client.post(f"/api/work-items/{wid}/cancel", json={"reason": "no"})

    assert r.status_code == 200, r.text
    assert "close_mr" not in r.json()
    assert fake.closed == []


# --- E: MR closed externally (B8) --------------------------------------------


def _set_stop_kind(wid: str, stop_kind: str) -> None:
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute("UPDATE work_items SET stop_kind = ? WHERE id = ?", (stop_kind, wid))
        conn.commit()
    finally:
        conn.close()


def _stopped_item_with_closed_mr(client, repo, fake, node_id: str = "merge"):
    """A `needs_human` item on `node_id`, stopped with `stop_kind = 'mr_closed'`
    -- what `mr_poller.tick` writes -- with its merge request already closed
    on `fake` too, the shape `/reopen-mr` needs."""
    from kraft import events, store

    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]
    _force_node(wid, node_id, "needs_human")
    db = client.app.state.db
    row = client.portal.call(
        db.read, lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    worktree = client.app.state.run_dirs.worktrees / wid

    async def seed():
        mr = await fake.open_mr(
            repo=worktree, branch=store.branch_for(row), base="main", title="t", body="b"
        )
        await db.write(
            lambda c: events.append(c, wid, "mr_opened", {"number": mr.number, "url": mr.url})
        )
        await fake.close_mr(repo=worktree, mr=mr)

    client.portal.call(seed)
    _set_stop_kind(wid, "mr_closed")
    return wid


def test_reopen_mr_on_an_mr_closed_stop_reopens_emits_and_retries(client, repo, monkeypatch):
    fake = _fake_forge_cancel(monkeypatch)
    wid = _stopped_item_with_closed_mr(client, repo, fake)

    r = client.post(f"/api/work-items/{wid}/reopen-mr")

    assert r.status_code == 200, r.text
    assert fake.reopened == [1]
    evs = [
        e["payload"]
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == "mr_reopened"
    ]
    assert evs == [{"ref": 1, "url": "http://fake.forge/1"}]
    # The retry's own response shape (`_retry`, not a copy of it).
    body = r.json()
    assert body["id"] == wid
    assert body["node_id"] == "merge"


def test_reopen_mr_on_any_other_stop_answers_409(client, repo, monkeypatch):
    """A merge request on file (so a 409 can only come from the `stop_kind`
    check, not from `_mr_ref` finding none) but stopped for an unrelated
    reason."""
    fake = _fake_forge_cancel(monkeypatch)
    wid = _stopped_item_with_closed_mr(client, repo, fake)
    _set_stop_kind(wid, "failed")

    assert client.post(f"/api/work-items/{wid}/reopen-mr").status_code == 409
    assert fake.reopened == []


def test_reopen_mr_with_a_forge_failure_answers_502_and_the_item_stays_stopped(
    client, repo, monkeypatch
):
    from kraft.adapters import forge as forge_mod

    fake = _fake_forge_cancel(monkeypatch)
    wid = _stopped_item_with_closed_mr(client, repo, fake)

    async def _boom(*, repo, mr):
        raise forge_mod.ForgeError("gh: rate limited")

    monkeypatch.setattr(fake, "reopen_mr", _boom)

    r = client.post(f"/api/work-items/{wid}/reopen-mr")

    assert r.status_code == 502, r.text
    assert "rate limited" in r.json()["detail"]
    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["status"] == "needs_human"
    assert detail["stop"]["kind"] == "mr_closed"
