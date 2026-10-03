"""client.py against the real app, over ASGI — no server, no MCP client.

A test that needs the app takes the `wired` fixture (`tests/client/conftest.py`)
and hands its whole scenario, one `async def`, to `support.api.run_with_app`.
`httpx.ASGITransport` does not run the app's lifespan the way `TestClient`
does, so `run_with_app` enters it, and runs the scenario in the same event
loop, because the Database the lifespan opens is bound to the loop that opened
it. The tests here are sync functions for that reason, each handing one
coroutine to `run_with_app`; the few with no app to reach (`base_url`, a
`health` answer) call the client directly, or through `asyncio.run` for the
one coroutine among them.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from support.api import run_with_app
from support.harness import connected_repo

from kraft import client


def test_base_url_prefers_loopback_over_a_wildcard_bind(monkeypatch, tmp_path):
    access = tmp_path / "templates"
    access.mkdir()
    (access / "access.yaml").write_text("bind: 0.0.0.0\nport: 9999\n")
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(access))
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    monkeypatch.delenv("KRAFT_PORT", raising=False)
    # 0.0.0.0 is an address to listen on, never one to connect to
    assert client.base_url() == "http://127.0.0.1:9999"


def test_base_url_brackets_an_ipv6_bind(monkeypatch, tmp_path):
    """`http://::1:8765` is not a URL: httpx reads the colons as a port."""
    access = tmp_path / "templates"
    access.mkdir()
    (access / "access.yaml").write_text("bind: '::1'\nport: 9999\n")
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(access))
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    monkeypatch.delenv("KRAFT_PORT", raising=False)
    assert client.base_url() == "http://[::1]:9999"
    assert httpx.URL(client.base_url()).port == 9999


async def _create(repo, title="read me") -> str:
    async with client.transport.http() as http:
        response = await http.post(
            "/api/work-items", json={"autostart": True, "title": title, "repo": str(repo)}
        )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_list_work_items_is_trimmed(wired, tmp_path):
    repo = connected_repo(tmp_path)

    async def scenario():
        await _create(repo)
        return await client.list_work_items()

    items = run_with_app(wired, scenario)
    assert [i["title"] for i in items] == ["read me"]
    # the board's full chain definition is not something an agent asked for
    assert set(items[0]) == {
        "id",
        "title",
        "repo",
        "status",
        "current_node_id",
        "pending_gate",
        "progress",
    }


def test_list_work_items_filters_by_status(wired, tmp_path):
    repo = connected_repo(tmp_path)

    async def scenario():
        await _create(repo)
        return await client.list_work_items(status="no-such-status")

    assert run_with_app(wired, scenario) == []


def test_cancelled_and_archived_items_are_reachable_on_request(wired, tmp_path):
    """Cancel stores `abandoned`, which the board leaves off. The flag (or
    asking for that status, as the MCP tool does) brings it back, and
    `work_item_ids` -- doctor's list of every row -- has archived items too.
    Through the real transport: the flag once rode in the path, and httpx
    dropped it for the empty `params` beside it."""
    repo = connected_repo(tmp_path)

    async def scenario():
        open_id, cancelled, archived = [await _create(repo, title=t) for t in "abc"]
        async with client.transport.http() as http:
            for wid in (cancelled, archived):
                r = await http.post(f"/api/work-items/{wid}/cancel", json={"reason": "r"})
                assert r.status_code == 200, r.text
            r = await http.post(f"/api/work-items/{archived}/archive")
            assert r.status_code == 200, r.text
        lists = [
            await client.list_work_items(),
            await client.list_work_items(include_abandoned=True),
            await client.list_work_items(status="abandoned"),
        ]
        return (open_id, cancelled, archived), lists, await client.work_item_ids()

    (open_id, cancelled, archived), lists, ids = run_with_app(wired, scenario)
    board, with_abandoned, only_abandoned = ([i["id"] for i in items] for items in lists)
    assert board == [open_id]
    assert with_abandoned == [open_id, cancelled]
    assert only_abandoned == [cancelled]
    assert ids == {open_id, cancelled, archived}


def test_get_work_item_defaults_to_the_resolved_context(wired, tmp_path, monkeypatch):
    repo = connected_repo(tmp_path)

    async def scenario():
        wid = await _create(repo, title="mine")
        # the executor injects this into every session it starts
        monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
        return wid, await client.get_work_item()

    wid, item = run_with_app(wired, scenario)
    assert item["id"] == wid
    assert item["title"] == "mine"
    # the detail endpoint's worker_sessions and usage rollup are not forwarded
    assert "worker_sessions" not in item


def test_get_work_item_without_a_context_says_so(wired):
    with pytest.raises(ValueError, match="no work item"):
        run_with_app(wired, client.get_work_item)


def test_search_passes_the_query_through(wired):
    out = run_with_app(wired, lambda: client.search("anything"))
    assert out["query"] == "anything"
    assert isinstance(out["results"], list)


def test_an_http_error_becomes_a_readable_message(wired):
    with pytest.raises(ValueError, match="404"):
        run_with_app(wired, lambda: client.get_work_item("no-such-item"))


def test_get_work_item_names_the_next_node(wired, tmp_path):
    """`kraft show --json` trims the chain away, so nothing in the CLI's output
    said what comes next — which is the one thing a status report needs
    (Kraft-9rs). A NULL current node means "not started": node zero is next, the
    same rule resume follows."""
    repo = connected_repo(tmp_path)

    async def scenario():
        async with client.transport.http() as http:
            created = await http.post(
                "/api/work-items",
                json={"title": "chain me", "repo": str(repo), "autostart": False},
            )
            assert created.status_code == 201, created.text
            wid = created.json()["id"]
            full = (await http.get(f"/api/work-items/{wid}")).json()
        return full, await client.get_work_item(wid)

    full, item = run_with_app(wired, scenario)
    node_ids = [node["id"] for node in full["chain_definition"]["nodes"]]
    assert full["current_node_id"] is None
    assert item["next_node_id"] == node_ids[0]
    # the chain itself is still trimmed away: one string, not the whole chain
    assert "chain_definition" not in item


def test_the_last_node_has_no_next_node(wired, tmp_path):
    """There is no route that parks an item on its last node without running the
    whole chain, so this calls the helper directly on a doctored copy of the
    item's own chain definition."""
    repo = connected_repo(tmp_path)

    async def scenario():
        async with client.transport.http() as http:
            created = await http.post(
                "/api/work-items",
                json={"title": "nearly done", "repo": str(repo), "autostart": False},
            )
            assert created.status_code == 201, created.text
            wid = created.json()["id"]
            full = (await http.get(f"/api/work-items/{wid}")).json()
        full["current_node_id"] = full["chain_definition"]["nodes"][-1]["id"]
        return client.reads._next_node_id(full)

    assert run_with_app(wired, scenario) is None


def test_health_rejects_a_server_that_omits_run_dir(monkeypatch, tmp_path):
    """A payload missing run_dir must not be waved through as this instance --
    that is exactly how an older kraft daemon (or anything else on the port)
    passed itself off as this one (Kraft-kquf)."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))

    async def fake_get(path, **params):
        return {"status": "ok"}

    monkeypatch.setattr(client.reads.transport, "_get", fake_get)

    with pytest.raises(ValueError, match="older kraft daemon"):
        asyncio.run(client.reads.health())
