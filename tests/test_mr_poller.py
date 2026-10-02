"""The MR-closed poller (B8): notices a merge request closed on the forge
without merging, for an item parked at an MR node, and stops it for a human."""

from __future__ import annotations

import pytest
from support.harness import v1_chain

from kraft import events, mr_poller, store


def _chain(repo, *, mr_node_id: str = "merge"):
    """A two-node chain: a plain exec node (`verify`) and one MR node whose
    one task is `kind: forge`, `target: mr.merge` -- `_is_mr_node` must find
    it by that task, not by this node's id."""
    return v1_chain(
        [
            {
                "id": "verify",
                "kind": "exec",
                "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
            },
            {
                "id": mr_node_id,
                "kind": "exec",
                "tasks": [{"id": "go", "kind": "forge", "target": "mr.merge"}],
            },
        ],
        repo=repo,
    )


def _patch_forge(monkeypatch, fake):
    monkeypatch.setattr("kraft.adapters.forge.backend_for", lambda *a, **k: "fake")
    monkeypatch.setattr("kraft.adapters.forge.resolve", lambda name: fake)


async def _opened_mr(it, fake):
    mr = await fake.open_mr(
        repo=it.worktree, branch=store.branch_for(it.row()), base="main", title="t", body="b"
    )
    await it.database.write(
        lambda c: events.append(c, it.id, "mr_opened", {"number": mr.number, "url": mr.url})
    )
    return mr


def _app(stub_app, database, run_dirs, tmp_path):
    return stub_app(templates_dir=tmp_path / "templates", skills_dir=tmp_path / "skills")


async def test_tick_stops_a_waiting_item_whose_mr_was_closed(
    item_on, stub_app, repo, tmp_path, monkeypatch
):
    from kraft.adapters import forge as forge_mod

    fake = forge_mod.FakeForge()
    _patch_forge(monkeypatch, fake)
    it = await item_on(_chain(repo), "merge")
    mr = await _opened_mr(it, fake)
    await fake.close_mr(repo=it.worktree, mr=mr)
    await it.database.write(
        lambda c: store.mark_waiting(c, it.id, "merge", "2000-01-01T00:00:00+00:00")
    )
    app = _app(stub_app, it.database, it.run_dirs, tmp_path)

    stopped = await mr_poller.tick(app)

    assert stopped == [it.id]
    row = it.row()
    assert row["status"] == "needs_human"
    assert row["stop_kind"] == "mr_closed"
    payload = it.events("work_item_needs_human")[-1]["payload"]
    assert payload["kind"] == "mr_closed"
    assert payload["facts"] == {"ref": mr.number, "url": mr.url}
    assert payload["reason"] == mr_poller.REASON


async def test_tick_leaves_a_merged_or_open_mr_untouched(
    item_on, stub_app, repo, tmp_path, monkeypatch
):
    from kraft.adapters import forge as forge_mod

    fake = forge_mod.FakeForge()
    _patch_forge(monkeypatch, fake)
    it = await item_on(_chain(repo), "merge")
    mr = await _opened_mr(it, fake)
    await it.database.write(
        lambda c: store.mark_needs_human(c, it.id, "merge", "a task failed", kind="failed")
    )
    app = _app(stub_app, it.database, it.run_dirs, tmp_path)

    # open
    assert await mr_poller.tick(app) == []
    assert it.row()["stop_kind"] == "failed"

    # merged
    await fake.merge(repo=it.worktree, branch=store.branch_for(it.row()), mr=mr)
    assert await mr_poller.tick(app) == []
    assert it.row()["stop_kind"] == "failed"


async def test_tick_skips_a_non_mr_node_without_calling_find_mr(
    item_on, stub_app, repo, tmp_path, monkeypatch
):
    from kraft.adapters import forge as forge_mod

    fake = forge_mod.FakeForge()
    _patch_forge(monkeypatch, fake)
    it = await item_on(_chain(repo), "verify")  # not the MR node
    await _opened_mr(it, fake)
    await it.database.write(
        lambda c: store.mark_waiting(c, it.id, "verify", "2000-01-01T00:00:00+00:00")
    )
    calls = []
    real_find_mr = fake.find_mr

    async def _counting_find_mr(**kw):
        calls.append(kw)
        return await real_find_mr(**kw)

    monkeypatch.setattr(fake, "find_mr", _counting_find_mr)
    app = _app(stub_app, it.database, it.run_dirs, tmp_path)

    assert await mr_poller.tick(app) == []
    assert calls == []
    assert it.row()["status"] == "waiting"


async def test_tick_skips_an_item_neither_waiting_nor_needs_human(
    item_on, stub_app, repo, tmp_path, monkeypatch
):
    from kraft.adapters import forge as forge_mod

    fake = forge_mod.FakeForge()
    _patch_forge(monkeypatch, fake)
    it = await item_on(_chain(repo), "merge")
    await _opened_mr(it, fake)
    calls = []
    real_find_mr = fake.find_mr

    async def _counting_find_mr(**kw):
        calls.append(kw)
        return await real_find_mr(**kw)

    monkeypatch.setattr(fake, "find_mr", _counting_find_mr)
    app = _app(stub_app, it.database, it.run_dirs, tmp_path)

    # make_item leaves the item active by default
    assert it.row()["status"] == "active"
    assert await mr_poller.tick(app) == []
    assert calls == []


@pytest.mark.parametrize(
    "move",
    [
        lambda c, wid: store.mark_reentered(c, wid),
        lambda c, wid: store.enter_node(c, wid, "verify"),
    ],
    ids=["resumed", "moved-to-another-node"],
)
async def test_tick_does_not_stop_an_item_that_moved_after_it_was_read(
    item_on, stub_app, repo, tmp_path, monkeypatch, move
):
    """The forge call sits between the poller's read of the row and its write:
    an item resumed or moved on in that window must keep what it moved to,
    not be clobbered by a stop measured against the row it used to be."""
    from kraft.adapters import forge as forge_mod
    from kraft.api.routes import board

    fake = forge_mod.FakeForge()
    _patch_forge(monkeypatch, fake)
    it = await item_on(_chain(repo), "merge")
    mr = await _opened_mr(it, fake)
    await fake.close_mr(repo=it.worktree, mr=mr)
    await it.database.write(
        lambda c: store.mark_waiting(c, it.id, "merge", "2000-01-01T00:00:00+00:00")
    )
    real_mr_state = board._mr_state

    async def moved_while_asking(st, row):
        state = await real_mr_state(st, row)
        await st.db.write(lambda c: move(c, row["id"]))
        return state

    monkeypatch.setattr(board, "_mr_state", moved_while_asking)
    before = it.row()
    app = _app(stub_app, it.database, it.run_dirs, tmp_path)

    assert await mr_poller.tick(app) == []
    row = it.row()
    assert (row["status"], row["current_node_id"]) != (
        before["status"],
        before["current_node_id"],
    )
    assert row["stop_kind"] != "mr_closed"
    assert it.events("work_item_needs_human") == []


async def test_tick_leaves_an_item_untouched_on_a_forge_error(
    item_on, stub_app, repo, tmp_path, monkeypatch, caplog
):
    from kraft.adapters import forge as forge_mod

    fake = forge_mod.FakeForge()
    _patch_forge(monkeypatch, fake)
    it = await item_on(_chain(repo), "merge")
    await _opened_mr(it, fake)
    await it.database.write(
        lambda c: store.mark_waiting(c, it.id, "merge", "2000-01-01T00:00:00+00:00")
    )

    async def _boom(**kw):
        raise forge_mod.ForgeError("gh: rate limited")

    monkeypatch.setattr(fake, "find_mr", _boom)
    app = _app(stub_app, it.database, it.run_dirs, tmp_path)

    def logged():
        return [
            r for r in caplog.records if r.name == "kraft.mr_poller" and "rate limited" in r.message
        ]

    with caplog.at_level("WARNING", logger="kraft.mr_poller"):
        assert await mr_poller.tick(app) == []
        assert it.row()["status"] == "waiting"
        # Logged once, recorded until the outcome changes.
        assert app.state.mr_poll_errors == {it.id: "gh: rate limited"}
        assert len(logged()) == 1

        # A second tick with the same error does not re-log -- it does leave
        # the item untouched again.
        assert await mr_poller.tick(app) == []
        assert app.state.mr_poll_errors == {it.id: "gh: rate limited"}
        assert len(logged()) == 1
        assert it.row()["status"] == "waiting"
