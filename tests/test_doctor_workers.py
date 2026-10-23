"""`kraft admin doctor` on registered machines."""

from __future__ import annotations

import asyncio

from kraft import client, doctor

# `app` fixture: tests/conftest.py. It wires client.transport.http() to the ASGI app.


def _rows(tmp_path):
    return {
        r["name"]: r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("worker")
    }


def test_no_workers_file_adds_no_rows(app, tmp_path):
    assert _rows(tmp_path) == {}


def test_an_offline_machine_is_a_warning_naming_the_reason_not_a_failure(app, tmp_path):
    asyncio.run(client.add_worker("home"))
    rows = _rows(tmp_path)
    assert rows["workers.yaml"]["ok"]
    row = rows["worker: home"]
    assert row["ok"] and row["warn"] and "never connected" in row["detail"]


def test_an_ssh_machine_that_cannot_connect_names_the_servers_own_attempt(
    app, tmp_path, monkeypatch
):
    async def unreachable(self, name, entry):  # no real ssh in a unit test
        entry.reason = "ssh: Permission denied (publickey)"
        entry.outcome.set()

    monkeypatch.setattr("kraft.remote.hub.Hub._dial_ssh", unreachable)
    asyncio.run(client.add_worker("boxed", ssh="boxed"))
    row = _rows(tmp_path)["worker: boxed"]
    assert row["ok"] and row["warn"] and "server's own ssh" in row["detail"]


def test_a_broken_workers_file_fails_the_file_row(app, tmp_path):
    (tmp_path / "templates" / "workers.yaml").write_text("workers:\n  a:\n    capacity: 1\n")
    row = _rows(tmp_path)["workers.yaml"]
    assert not row["ok"] and "exactly one of" in row["detail"]
