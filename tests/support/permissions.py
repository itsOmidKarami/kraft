"""Shared set-up for the permission gate's tests: one pending worker session
on a V1 work item, and the gate asked about it."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from kraft import store
from support.harness import (
    fake_harness_home,
    fake_templates_dir,
    v1_chain,
    write_harness_profiles,
)

#: A real V1 hook point: the canonical path dispatch writes to
#: `worker_sessions.hook_point` (`executor/dispatch.py`), never a legacy hook.
PATH = "implementation.main.work"


def templates(tmp_path, monkeypatch):
    """The `client` fixture's templates: a `fake` harness profile on `true`."""
    d = fake_templates_dir(tmp_path, "claude")
    monkeypatch.setenv("KRAFT_HOME", str(fake_harness_home(tmp_path, ["true"])))
    write_harness_profiles(d, {"fake": {"provider": "fake"}})
    return d


def seed_session(sid="s1", wid="w1", hook_point=PATH, harness="fake", policy=None):
    """A V1 work item whose chain has one agent task at `PATH`, and one
    pending session on `hook_point`, written straight to the database.
    `policy` is the node scope's own `policy:`."""
    chain = v1_chain(
        [
            {
                "id": "implementation",
                "kind": "exec",
                **({"policy": policy} if policy is not None else {}),
                "tasks": [
                    {"id": "work", "kind": "agent", "harness": harness, "prompt": "Do it."},
                    {"id": "check", "kind": "subprocess", "command": "true"},
                ],
            }
        ],
        repo="/r",
    )
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.row_factory = sqlite3.Row
    try:
        store.create_work_item(
            conn,
            id=wid,
            bead_id="B",
            title="t",
            repo="/r",
            chain_template=chain.chain.id,
            chain_definition="{}",
            materialized_chain=chain.to_json(),
        )
        store.create_session(
            conn,
            id=sid,
            work_item_id=wid,
            node_id="implementation",
            hook_point=hook_point,
            log_path="/tmp/kraft-test.log",
            result_path="/tmp/kraft-test.json",
        )
        conn.commit()
    finally:
        conn.close()


def ask(client, tool_name="Bash", sid="s1", input=None, **extra):
    """POST one ask to the gate; `input` defaults to `{"command": "ls"}`."""
    return client.post(
        f"/api/worker-sessions/{sid}/permission",
        json={
            "tool_name": tool_name,
            "input": {"command": "ls"} if input is None else input,
            **extra,
        },
    )


def events_of(client, type_, wid="w1"):
    """The payloads of work item `wid`'s events of type `type_`, in order."""
    return [
        e["payload"]
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == type_
    ]
