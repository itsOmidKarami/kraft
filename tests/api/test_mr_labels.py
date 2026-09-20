"""POST /work-items/{wid}/mr-labels — the mechanism `mr_checks`' on_failure
repair task calls once it has decided which labels a red pipeline wants
(Kraft-xh0q layer 3)."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

from kraft.adapters import forge as forge_mod

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    import kraft.api as api

    return TestClient(api.app, client=("127.0.0.1", 54321))


def _completed_item(client, repo):
    import time

    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "make it pass", "chain_template": "quick-task"},
    ).json()["id"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        evs = client.get(f"/api/work-items/{wid}/events").json()
        if any(e["type"] == "work_item_completed" for e in evs):
            return wid
        time.sleep(0.2)
    raise AssertionError("work item never completed")


def _fake_forge(monkeypatch):
    """Every backend resolves to one shared FakeForge, so the test can read
    back what `set_labels` was called with."""
    fake = forge_mod.FakeForge()
    monkeypatch.setattr("kraft.adapters.forge.backend_for", lambda *a, **k: "fake")
    monkeypatch.setattr("kraft.adapters.forge.resolve", lambda name: fake)
    return fake


def test_set_mr_labels_applies_and_records_them(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        fake = _fake_forge(monkeypatch)

        r = client.post(f"/api/work-items/{wid}/mr-labels", json={"labels": ["release::patch"]})
        assert r.status_code == 200, r.text
        assert r.json() == {"work_item_id": wid, "labels": ["release::patch"]}
        assert fake.labels == ["release::patch"]

        evs = client.get(f"/api/work-items/{wid}/events").json()
        assert any(
            e["type"] == "mr_labels_set" and e["payload"]["labels"] == ["release::patch"]
            for e in evs
        )


def test_set_mr_labels_strips_blanks_and_refuses_an_empty_list(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        fake = _fake_forge(monkeypatch)

        r = client.post(f"/api/work-items/{wid}/mr-labels", json={"labels": ["  ", ""]})
        assert r.status_code == 422, r.text
        assert fake.labels == []


def test_set_mr_labels_404s_with_no_worktree(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert (
            client.post("/api/work-items/nope/mr-labels", json={"labels": ["x"]}).status_code == 404
        )


def test_set_mr_labels_is_not_a_self_action(tmp_path, monkeypatch):
    """A worker fixing its own item's merge request is the point (design §6
    rule 2 is about gates, not this), so the client helper must not refuse it
    the way `retry`/`approve_gate` refuse a worker acting on itself."""
    from kraft import client as client_mod
    from kraft.client import transport

    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    (tmp_path / "run" / "worktrees").mkdir(parents=True)
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "mine")

    posted = {}

    async def fake_act(path, payload=None):
        posted["path"], posted["payload"] = path, payload
        return {"work_item_id": "mine", "labels": payload["labels"]}

    monkeypatch.setattr(transport, "_act", fake_act)

    import asyncio

    result = asyncio.run(client_mod.mr_labels(["release::patch"]))
    assert result["labels"] == ["release::patch"]
    assert posted["path"] == "/work-items/mine/mr-labels"


def test_set_mr_labels_unpins_the_stale_pipeline(tmp_path, monkeypatch):
    """Labelling re-creates the pipeline, so the pipeline `on.ci.poll` pinned
    for this head sha is the red one the label was missing from. Leaving the
    pin would make the next same-sha poll re-report the identical finding and
    the fix loop call the item stuck (this item's own escalation)."""
    from kraft import store

    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        fake = _fake_forge(monkeypatch)
        import kraft.api as api

        db = api.app.state.db
        client.portal.call(db.write, lambda c: store.set_ci_pipeline_ref(c, wid, "deadbeef:111"))

        r = client.post(f"/api/work-items/{wid}/mr-labels", json={"labels": ["x"]})
        assert r.status_code == 200, r.text
        assert fake.labels == ["x"]
        row = client.portal.call(
            db.write,
            lambda c: c.execute(
                "SELECT ci_pipeline_ref FROM work_items WHERE id = ?", (wid,)
            ).fetchone(),
        )
        assert not row["ci_pipeline_ref"]
