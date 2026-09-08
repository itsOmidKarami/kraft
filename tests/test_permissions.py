"""Kraft answers a worker's permission prompts from its node's grant (Kraft-oor).

The policy is the hook binding's `allowed_tools` (Kraft-3tw); the audit trail
is a `permission_decision` event, which is the only way the orchestrator ever
learns what a worker decided it was allowed to do.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd

from kraft import client as client_mod
from kraft import store

_HOOK = "on.implementation.start"


def _templates(tmp_path, **binding_extra):
    d = fake_templates_dir(tmp_path, "claude")
    registry = yaml.safe_load((d / "registry.yaml").read_text())
    registry["hooks"][_HOOK].update(binding_extra)
    (d / "registry.yaml").write_text(yaml.safe_dump(registry))
    return d


def _client(tmp_path, monkeypatch, templates_dir):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    return TestClient(api.app, client=("127.0.0.1", 54321))


def _seed_session(sid="s1", wid="w1", node="implementation"):
    """A work item and one pending session, written straight to the database:
    the endpoint under test reads a row and a binding, and needs no chain."""
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.row_factory = sqlite3.Row
    try:
        store.create_work_item(
            conn,
            id=wid,
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
        store.create_session(
            conn,
            id=sid,
            work_item_id=wid,
            node_id=node,
            hook_point=_HOOK,
            log_path="/tmp/kraft-test.log",
            result_path="/tmp/kraft-test.json",
        )
        conn.commit()
    finally:
        conn.close()


def _ask(client, tool_name="Bash", sid="s1", **extra):
    return client.post(
        f"/worker-sessions/{sid}/permission",
        json={"tool_name": tool_name, "input": {"command": "ls"}, **extra},
    )


def test_permission_request_allows_when_the_binding_declares_no_allowlist(tmp_path, monkeypatch):
    """`--permission-mode auto` resolves these asks silently today. A default
    that denied would make a pure-observability change a behaviour change on
    every node at once."""
    with _client(tmp_path, monkeypatch, _templates(tmp_path)) as client:
        _seed_session()
        body = _ask(client).json()
    assert body == {"behavior": "allow", "updatedInput": {"command": "ls"}}


def test_permission_request_denies_a_tool_outside_the_node_allowlist(tmp_path, monkeypatch):
    templates = _templates(tmp_path, allowed_tools=["Read", "Grep"])
    with _client(tmp_path, monkeypatch, templates) as client:
        _seed_session()
        denied = _ask(client, "Bash").json()
        allowed = _ask(client, "Read").json()
    assert denied["behavior"] == "deny"
    assert "implementation" in denied["message"], "a denial has to name the node"
    assert "Bash" in denied["message"]
    assert allowed == {"behavior": "allow", "updatedInput": {"command": "ls"}}


def test_permission_decision_is_recorded_as_an_event(tmp_path, monkeypatch):
    templates = _templates(tmp_path, allowed_tools=["Read"])
    with _client(tmp_path, monkeypatch, templates) as client:
        _seed_session()
        _ask(client, "Bash")
        _ask(client, "Read")
        events = [
            e
            for e in client.get("/work-items/w1/events").json()
            if e["type"] == "permission_decision"
        ]
    assert [e["payload"]["decision"] for e in events] == ["deny", "allow"]
    assert events[0]["payload"] == {
        "session_id": "s1",
        "node_id": "implementation",
        "tool": "Bash",
        "decision": "deny",
        "reason": events[0]["payload"]["reason"],
    }


def test_permission_request_on_an_unknown_session_is_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, _templates(tmp_path)) as client:
        assert _ask(client, sid="nope").status_code == 404


def test_the_worker_side_fails_closed(monkeypatch):
    """A Kraft restart while an adopted worker runs on must not become a
    permission grant. Denying is exactly today's behaviour for an unanswered
    ask, so it regresses nothing."""

    async def dead(*_a, **_kw):
        raise ValueError("no Kraft server at http://127.0.0.1:8765 — start one with `kraft`")

    monkeypatch.setenv("KRAFT_SESSION_ID", "s1")
    monkeypatch.setattr(client_mod, "_post", dead)
    answer = asyncio.run(client_mod.permission_request("Bash", {"command": "ls"}))
    assert answer["behavior"] == "deny"
    assert "no Kraft server" in answer["message"]


def test_the_worker_side_denies_when_it_is_not_a_kraft_session(monkeypatch):
    monkeypatch.delenv("KRAFT_SESSION_ID", raising=False)
    answer = asyncio.run(client_mod.permission_request("Bash", {}))
    assert answer["behavior"] == "deny"
