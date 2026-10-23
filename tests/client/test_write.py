"""The write half of the agent surface: create an item, register a repo."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from support.api import run_with_app
from support.harness import connected_repo, make_repo

from kraft import client


def test_create_work_item_never_starts_it(wired, tmp_path):
    repo = connected_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("from an agent", repo=str(repo))
        return created, await client.get_work_item(created["id"])

    created, item = run_with_app(wired, scenario)
    assert created["status"] == "paused"
    # an agent cannot spend tokens unattended — design §6 rule 1
    assert item["status"] == "paused"
    assert item["current_node_id"] is None


@pytest.mark.parametrize(
    "held",
    [
        {"slots": {"busy": 1, "limit": 3}},
        {"why": "checkout", "held_by": "w2", "held_by_title": "A"},
    ],
    ids=["slots", "checkout"],
)
def test_create_work_item_keeps_what_holds_a_queued_start(tmp_path, monkeypatch, held):
    """`kraft item create --autostart` words the hold from these: dropped, an
    in-place item waiting on the checkout read `1 of 3 slots are busy`
    (Kraft-whnhx)."""

    async def post(path, payload=None, **kw):
        return 201, {"id": "w1", "status": "queued", **held}

    monkeypatch.setattr(client.transport, "_post", post)
    created = asyncio.run(client.create_work_item("t", repo=str(tmp_path)))
    assert created == {"id": "w1", "status": "queued", "title": "t", **held}


def test_create_work_item_sends_the_description(wired, tmp_path):
    repo = connected_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item(
            "short label", repo=str(repo), description="the brief"
        )
        return await client.get_work_item(created["id"])

    assert run_with_app(wired, scenario)["description"] == "the brief"


def test_create_work_item_without_a_description_stores_none(wired, tmp_path):
    repo = connected_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("short label", repo=str(repo))
        return await client.get_work_item(created["id"])

    assert run_with_app(wired, scenario)["description"] is None


def test_create_work_item_without_a_repo_or_a_context_says_so(wired, tmp_path, monkeypatch):
    """With no repo argument and no worktree, there is nothing to guess."""

    async def scenario():
        return await client.create_work_item("no repo anywhere")

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="repo"):
        run_with_app(wired, scenario)


def test_ensure_repo_registers_a_repo_kraft_has_not_seen(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        return await client.ensure_repo(str(repo))

    entry = run_with_app(wired, scenario)
    assert entry["path"] == str(Path(repo).resolve())
    assert entry["already_connected"] is False


def test_ensure_repo_is_idempotent(wired, tmp_path):
    """A 409 from POST /repos means 'already connected', which is the goal state."""
    repo = connected_repo(tmp_path)

    async def scenario():
        await client.ensure_repo(str(repo))
        return await client.ensure_repo(str(repo))

    entry = run_with_app(wired, scenario)
    assert entry["path"] == str(Path(repo).resolve())
    assert entry["already_connected"] is True


def test_ensure_repo_reports_a_path_that_is_not_a_repo(wired, tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    async def scenario():
        return await client.ensure_repo(str(plain))

    with pytest.raises(ValueError, match="400"):
        run_with_app(wired, scenario)


@pytest.mark.parametrize(
    "call",
    [
        lambda: client.ensure_repo("sub"),
        lambda: client.disconnect_repo("sub"),
        lambda: client.create_work_item("t", repo="sub"),
        lambda: client.reindex("sub"),
    ],
    ids=["connect", "disconnect", "create", "reindex"],
)
def test_a_relative_path_reaches_the_server_absolute(call, tmp_path, monkeypatch):
    """The server resolves a relative path against its own cwd, so `kraft repo
    connect .` connected the daemon's directory (Kraft-9efnk.32). Every verb
    that sends a path makes it absolute where the caller stands."""
    monkeypatch.chdir(tmp_path)
    sent = []

    async def send(method, path, **kwargs):
        for fields in (kwargs.get("json") or {}, kwargs.get("params") or {}):
            sent.extend(v for k, v in fields.items() if k in ("path", "repo"))
        if path == "/repos/probe":
            return httpx.Response(400, json={"detail": "not probed"})
        return httpx.Response(201, json={"repos": [], "id": "w"})

    monkeypatch.setattr(client.transport, "_send", send)
    asyncio.run(call())
    assert sent
    assert set(sent) == {str(Path.cwd() / "sub")}


def test_set_agent_overrides_replaces_the_whole_override_as_in_1_4(wired, tmp_path):
    """`set-overrides` and the MCP tool replace the whole override: a field
    they don't name is gone. The echo is what is stored."""
    repo = connected_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("dial it", repo=str(repo))
        await client.set_agent_overrides(effort="high", work_item_id=created["id"])
        return await client.set_agent_overrides(model="gpt-big", work_item_id=created["id"])

    # The PATCH echoes the override as stored (`update_work_item`).
    assert run_with_app(wired, scenario)["agent_overrides"] == {"model": "gpt-big"}


def test_set_agent_overrides_sends_only_the_fields_named(tmp_path, monkeypatch):
    """A field not named is left out, never sent as `null`: a 1.4 server,
    still running between `kraft admin update` and the restart, refuses a
    `null` field with a 422 (`agent_overrides 'effort' must be one of ...`),
    where it takes the named fields alone and replaces the override."""
    monkeypatch.chdir(tmp_path)
    sent = []

    async def send(method, path, **kwargs):
        sent.append((method, path, kwargs.get("json")))
        return httpx.Response(200, json={"id": "w1"})

    monkeypatch.setattr(client.transport, "_send", send)
    asyncio.run(client.set_agent_overrides(model="gpt-big", work_item_id="w1"))
    # the GET reads the stored runner to keep it; the PATCH carries only the named fields
    assert [(m, p) for m, p, _ in sent] == [("GET", "/work-items/w1"), ("PATCH", "/work-items/w1")]
    assert sent[-1][2] == {"agent_overrides": {"model": "gpt-big"}}


def test_runner_rides_on_filing_and_on_both_override_calls(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = []

    async def send(method, path, **kwargs):
        sent.append((method, path, kwargs.get("json")))
        return httpx.Response(200, json={"id": "w1", "status": "paused"})

    monkeypatch.setattr(client.transport, "_send", send)
    asyncio.run(client.create_work_item("t", repo=str(tmp_path), runner="gpu"))
    asyncio.run(client.set_agent_overrides(runner="gpu", work_item_id="w1"))
    asyncio.run(client.set_node_overrides("build", runner="gpu", work_item_id="w1"))
    assert sent[0][0:2] == ("POST", "/work-items") and sent[0][2]["runner"] == "gpu"
    assert sent[1:] == [
        ("PATCH", "/work-items/w1", {"agent_overrides": {"runner": "gpu"}}),
        ("PATCH", "/work-items/w1", {"node_overrides": {"build": {"runner": "gpu"}}}),
    ]


def test_filing_without_a_runner_does_not_send_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = []

    async def send(method, path, **kwargs):
        sent.append(kwargs.get("json"))
        return httpx.Response(200, json={"id": "w1", "status": "paused"})

    monkeypatch.setattr(client.transport, "_send", send)
    asyncio.run(client.create_work_item("t", repo=str(tmp_path)))
    assert "runner" not in sent[0]
