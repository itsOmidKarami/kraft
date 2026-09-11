"""client.py against the real app, over ASGI — no server, no MCP client.

Async tests follow the suite's existing shape: a sync test function wrapping an
inner coroutine with `asyncio.run` (see tests/test_adapters_subprocess.py).

`httpx.ASGITransport` does not run the app's lifespan the way `TestClient` does,
so `run_with_app` enters it explicitly. Everything a test does lives in one
coroutine, and therefore one event loop, because the Database opened by the
lifespan is bound to the loop that opened it.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest
from support.harness import fake_templates_dir, isolated_bd, make_repo

from kraft import client

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """The app, with client.transport.http() pointed at it in-process."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    import kraft.api as api

    monkeypatch.setattr(
        client.transport,
        "http",
        lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://kraft"
        ),
    )
    return api


def test_base_url_prefers_loopback_over_a_wildcard_bind(monkeypatch, tmp_path):
    access = tmp_path / "templates"
    access.mkdir()
    (access / "access.yaml").write_text("bind: 0.0.0.0\nport: 9999\n")
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(access))
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    monkeypatch.delenv("KRAFT_PORT", raising=False)
    # 0.0.0.0 is an address to listen on, never one to connect to
    assert client.base_url() == "http://127.0.0.1:9999"


def run_with_app(api, scenario):
    """Run one coroutine with the app's lifespan active."""

    async def wrapper():
        async with api.app.router.lifespan_context(api.app):
            return await scenario()

    return asyncio.run(wrapper())


async def _create(repo, title="read me") -> str:
    async with client.transport.http() as http:
        response = await http.post("/api/work-items", json={"title": title, "repo": str(repo)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_list_work_items_is_trimmed(wired, tmp_path):
    repo = make_repo(tmp_path)

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
    repo = make_repo(tmp_path)

    async def scenario():
        await _create(repo)
        return await client.list_work_items(status="no-such-status")

    assert run_with_app(wired, scenario) == []


def test_get_work_item_defaults_to_the_resolved_context(wired, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)

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
    repo = make_repo(tmp_path)

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
    repo = make_repo(tmp_path)

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
