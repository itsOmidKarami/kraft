"""PATCH /work-items/{id} `attachments` (Kraft-s7c04.28, .29): a not-yet-started
item's spec or plan is replaced, added or dropped in place, re-snapshotted, and
its chain re-materialized, so revising a document is not a re-file."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest


def _doc(repo: Path, rel: str, text: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return rel


def _file(client, repo, **body) -> str:
    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "autostart": False, **body},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _item(client, wid) -> dict:
    return client.get(f"/api/work-items/{wid}").json()


def _nodes(item) -> list[str]:
    return [n["id"] for n in json.loads(item["materialized_chain"])["chain"]["nodes"]]


def _start(wid) -> None:
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.execute("UPDATE work_items SET current_node_id = 'implementation' WHERE id = ?", (wid,))
    conn.commit()
    conn.close()


def test_a_revised_spec_replaces_the_snapshot_and_keeps_the_plan(client, repo):
    spec = _doc(repo, "specs/s.md", "# spec v1\n")
    plan = _doc(repo, "plans/p.md", "# plan\n")
    wid = _file(
        client, repo, attachments=[{"kind": "spec", "path": spec}, {"kind": "plan", "path": plan}]
    )
    before = {a["kind"]: a for a in _item(client, wid)["attachments"]}
    revised = _doc(repo, "specs/s2.md", "# spec v2\n")

    r = client.patch(f"/api/work-items/{wid}", json={"attachments": {"spec": revised}})

    assert r.status_code == 200, r.text
    after = {a["kind"]: a for a in _item(client, wid)["attachments"]}
    assert after["spec"]["path"] == revised
    assert Path(after["spec"]["source"]).read_text() == "# spec v2\n"
    assert after["plan"] == before["plan"]
    # The superseded snapshot does not linger beside the one the row names.
    stored = client.app.state.run_dirs.attachments / wid
    assert sorted(p.name for p in stored.iterdir()) == sorted(
        Path(a["source"]).name for a in after.values()
    )
    evs = [e for e in client.get(f"/api/work-items/{wid}/events").json()]
    changed = [e["payload"] for e in evs if e["type"] == "attachments_changed"]
    assert changed == [{"from": ["plan", "spec"], "to": ["plan", "spec"]}]


def test_adding_a_spec_trims_its_gate_and_dropping_it_restores_the_gate(client, repo):
    """.29: the trim follows the attachments both ways, so a gate trimmed at
    intake comes back when its document goes."""
    wid = _file(client, repo)
    assert "spec_approval" in _nodes(_item(client, wid))
    spec = _doc(repo, "specs/s.md", "# spec\n")

    added = client.patch(f"/api/work-items/{wid}", json={"attachments": {"spec": spec}})
    assert added.status_code == 200, added.text
    trimmed = _nodes(_item(client, wid))
    assert "spec" not in trimmed and "spec_approval" not in trimmed

    dropped = client.patch(f"/api/work-items/{wid}", json={"attachments": {"spec": None}})
    assert dropped.status_code == 200, dropped.text
    item = _item(client, wid)
    assert item["attachments"] == []
    assert _nodes(item)[:2] == ["spec", "spec_approval"]
    assert not any((client.app.state.run_dirs.attachments / wid).iterdir())


def test_an_attachment_patch_keeps_the_nodes_skipped_at_intake(client, repo):
    wid = _file(client, repo, skip_nodes=["local_review"])
    spec = _doc(repo, "specs/s.md", "# spec\n")

    r = client.patch(f"/api/work-items/{wid}", json={"attachments": {"spec": spec}})

    assert r.status_code == 200, r.text
    nodes = _nodes(_item(client, wid))
    assert "local_review" not in nodes
    assert "spec_approval" not in nodes


@pytest.mark.parametrize(
    "change", [{"spec": "specs/new.md"}, {"spec": None}], ids=["replace", "drop"]
)
def test_a_started_item_refuses_an_attachment_change_and_keeps_its_snapshot(client, repo, change):
    """A started item's worktree already holds the documents it was filed with,
    committed on its branch, and its chain is fixed: 409, nothing touched."""
    spec = _doc(repo, "specs/s.md", "# spec\n")
    _doc(repo, "specs/new.md", "# new\n")
    wid = _file(client, repo, attachments=[{"kind": "spec", "path": spec}])
    before = _item(client, wid)
    _start(wid)

    r = client.patch(f"/api/work-items/{wid}", json={"attachments": change})

    assert r.status_code == 409, r.text
    assert "started" in r.json()["detail"]
    after = _item(client, wid)
    assert after["attachments"] == before["attachments"]
    assert after["materialized_chain"] == before["materialized_chain"]
    assert [p.name for p in (client.app.state.run_dirs.attachments / wid).iterdir()] == [
        Path(before["attachments"][0]["source"]).name
    ]


@pytest.mark.parametrize(
    ("path", "detail"),
    [("specs/missing.md", "not found"), ("../outside.md", "escapes the repo")],
    ids=["missing", "escape"],
)
def test_an_unreadable_attachment_is_refused_and_changes_nothing(client, repo, path, detail):
    spec = _doc(repo, "specs/s.md", "# spec\n")
    wid = _file(client, repo, attachments=[{"kind": "spec", "path": spec}])
    before = _item(client, wid)

    r = client.patch(f"/api/work-items/{wid}", json={"attachments": {"spec": path}})

    assert r.status_code == 422, r.text
    assert detail in r.json()["detail"]
    after = _item(client, wid)
    assert after["attachments"] == before["attachments"]
    assert after["materialized_chain"] == before["materialized_chain"]


def test_a_walk_that_starts_mid_patch_keeps_the_snapshot_it_was_filed_with(
    client, repo, monkeypatch
):
    """The row read at the top of the PATCH said not started; the walk starts
    while the new copy is being taken. The write refuses, and the copy the
    worktree reads is still the old one, byte for byte."""
    from kraft.executor import entry

    spec = _doc(repo, "specs/s.md", "# filed\n")
    wid = _file(client, repo, attachments=[{"kind": "spec", "path": spec}])
    filed = _item(client, wid)["attachments"]
    revised = _doc(repo, "specs/s2.md", "# revised\n")
    real = entry.replace_attachments

    def racing(*args, **kwargs):
        stored = real(*args, **kwargs)
        _start(wid)
        return stored

    monkeypatch.setattr(entry, "replace_attachments", racing)

    r = client.patch(f"/api/work-items/{wid}", json={"attachments": {"spec": revised}})

    assert r.status_code == 409, r.text
    assert _item(client, wid)["attachments"] == filed
    assert [p.name for p in (client.app.state.run_dirs.attachments / wid).iterdir()] == [
        Path(filed[0]["source"]).name
    ]
    assert Path(filed[0]["source"]).read_text() == "# filed\n"
