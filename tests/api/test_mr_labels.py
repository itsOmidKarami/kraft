"""POST /work-items/{wid}/mr-labels — the mechanism `mr_checks`' on_failure
repair task calls once it has decided which labels a red pipeline wants
(Kraft-xh0q layer 3)."""

from __future__ import annotations

from kraft.adapters import forge as forge_mod


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


def test_set_mr_labels_applies_and_records_them(client, repo, monkeypatch):
    wid = _completed_item(client, repo)
    fake = _fake_forge(monkeypatch)

    r = client.post(f"/api/work-items/{wid}/mr-labels", json={"labels": ["release::patch"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"work_item_id": wid, "labels": ["release::patch"]}
    assert fake.labels == ["release::patch"]

    evs = client.get(f"/api/work-items/{wid}/events").json()
    assert any(
        e["type"] == "mr_labels_set" and e["payload"]["labels"] == ["release::patch"] for e in evs
    )


def test_set_mr_labels_strips_blanks_and_refuses_an_empty_list(client, repo, monkeypatch):
    wid = _completed_item(client, repo)
    fake = _fake_forge(monkeypatch)

    r = client.post(f"/api/work-items/{wid}/mr-labels", json={"labels": ["  ", ""]})
    assert r.status_code == 422, r.text
    assert fake.labels == []


def test_set_mr_labels_404s_with_no_worktree(client):
    assert client.post("/api/work-items/nope/mr-labels", json={"labels": ["x"]}).status_code == 404


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


def test_set_mr_labels_unpins_the_stale_pipeline(client, repo, monkeypatch):
    """Labelling re-creates the pipeline, so the pipeline `on.ci.poll` pinned
    for this head sha is the red one the label was missing from. Leaving the
    pin would make the next same-sha poll re-report the identical finding and
    the fix loop call the item stuck (this item's own escalation)."""
    from kraft import store

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


def test_set_mr_labels_labels_through_the_items_own_forge(client, repo, monkeypatch):
    """Kraft-hd0gu: the route resolves the forge the item's chain runs against,
    its repo's recorded `forge` (V1 has no registry backend to pin), not a
    502 for a backend nobody resolved. The real `backend_for`, so the choice
    is the route's own."""
    wid = _completed_item(client, repo)
    assert client.post("/api/repos", json={"path": str(repo), "enabled": False}).status_code == 201
    assert client.patch(f"/api/repos?path={repo}", json={"forge": "fake"}).status_code == 200
    fake = forge_mod.FakeForge()
    monkeypatch.setattr("kraft.adapters.forge.run._DEV_FAKE", fake)

    r = client.post(f"/api/work-items/{wid}/mr-labels", json={"labels": ["release::patch"]})

    assert r.status_code == 200, r.text
    assert fake.labels == ["release::patch"]


def test_set_mr_labels_on_a_malformed_repos_yaml_is_a_clean_error(client, repo, templates_dir):
    """Kraft-nzlzb: a repos.yaml that no longer parses leaves `deps.launch`
    handing back a poisoned entry whose `.get` raises `ConfigError`. The route
    must answer with that config problem, not a 500."""
    wid = _completed_item(client, repo)
    (templates_dir / "repos.yaml").write_text("repos: [not: {a mapping")

    r = client.post(f"/api/work-items/{wid}/mr-labels", json={"labels": ["x"]})

    assert r.status_code == 422, r.text
    assert "repos.yaml" in r.json()["detail"]
