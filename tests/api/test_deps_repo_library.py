"""`api.deps` and a connected repository's repo library: the health key of one
that did not load, and the chain an item with no chain runs."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from support.addons import (
    AGENT,
    accept_repo_library,
    chain,
    repo_library_files,
    repo_with_library,
    with_origin,
)
from support.harness import entry_of, make_repo, make_repo_with_submodule

from kraft import intake
from kraft.api import deps


def test_health_names_a_repo_library_that_did_not_load(client, tmp_path):
    """A repo library is keyed by its repository, not by an add-on id."""
    st = client.app.state
    accept_repo_library(st.templates_dir, st.run_dirs.addons, repo_with_library(tmp_path), "app")
    deps._reload_templates(st)
    assert "app:ship" in st.library.chain_ids
    assert client.get("/api/health").json()["status"] == "ok"

    (st.templates_dir / "harnesses.yaml").write_text(yaml.safe_dump({"harnesses": {}}))
    deps._reload_templates(st)

    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert "requires harness 'codex'" in body["invalid_templates"]["repo library app"]


def test_an_opted_in_repo_takes_a_text_change_when_the_server_checks(client, tmp_path):
    st = client.app.state
    repo = repo_with_library(tmp_path)
    accept_repo_library(st.templates_dir, st.run_dirs.addons, repo, "app")
    repos = yaml.safe_load((st.templates_dir / "repos.yaml").read_text())
    repos["repos"][0]["library_auto_update"] = True
    (st.templates_dir / "repos.yaml").write_text(yaml.safe_dump(repos))
    deps._reload_templates(st)
    repo_library_files(repo, library={"tasks": {"base": {**AGENT, "prompt": "new words"}}})

    client.portal.call(deps.auto_update_addons, client.app)

    chain = st.library.resolve_chain("app:ship").chain.model_dump(mode="json")
    assert "new words" in json.dumps(chain)


class _Library:
    def __init__(self, *chains):
        self.chain_ids = chains


@pytest.mark.parametrize(
    "entry, asked, chains, stored",
    [
        pytest.param({"id": "app"}, None, ("app:default",), "app:default", id="the-repos-default"),
        pytest.param(
            {"id": "app", "default_chain": "default"},
            None,
            ("app:default",),
            "app:default",
            id="default-counts-as-unset",
        ),
        pytest.param(
            {"id": "app", "default_chain": "quick"},
            None,
            ("app:default",),
            "quick",
            id="the-entrys-own-wins",
        ),
        pytest.param({"id": "app"}, "other", ("app:default",), "other", id="asked-wins"),
        pytest.param({"id": "app"}, None, ("app:ship",), None, id="library-without-default"),
        pytest.param({}, None, ("app:default",), None, id="no-id"),
        pytest.param({"id": "app"}, None, None, None, id="no-library"),
    ],
)
def test_which_chain_an_item_with_no_chain_runs(tmp_path, entry, asked, chains, stored):
    repo_entry = entry_of({"path": str(tmp_path), **entry})
    library = None if chains is None else _Library(*chains)

    assert deps.chain_template_for(repo_entry, asked, library) == stored


#: app's repo library: the task `base`, its chain `ship`, and steering `tone`.
_APP_LIBRARY = {"tasks": {"base": AGENT}, "steering": {"tone": {"instructions": "be terse"}}}


def _two_repos(client, tmp_path, web_entry=None):
    """`app`, whose repo library is accepted, and `web`, connected as `web`
    (with `web_entry` merged into its entry) and holding no library."""
    st = client.app.state
    app = repo_with_library(tmp_path, library=_APP_LIBRARY)
    web = make_repo(tmp_path, "web")
    accept_repo_library(st.templates_dir, st.run_dirs.addons, app, "app")
    repos = yaml.safe_load((st.templates_dir / "repos.yaml").read_text())
    repos["repos"].append({"path": str(web), "id": "web", **(web_entry or {})})
    (st.templates_dir / "repos.yaml").write_text(yaml.safe_dump(repos))
    deps._reload_templates(st)
    return app, web


def _workspace(client, tmp_path):
    """A workspace whose root (`ws`) and member (`pkg`) each have an accepted
    repo library: the member connected first, then the root, which names it."""
    st = client.app.state
    root, sub = make_repo_with_submodule(tmp_path, submodule_path="libs/pkg")
    for repo in (root, sub):
        with_origin(repo)
        repo_library_files(repo)
    client.post("/api/repos", json={"path": str(sub), "test_command": "true"})
    client.post("/api/repos", json={"path": str(root), "enabled": False})
    ids = {r["path"]: r["id"] for r in client.get("/api/repos").json()["repos"]}
    for repo in (root, sub):
        repo_id = ids.get(str(repo)) or ids[str(repo.resolve())]
        accept_repo_library(st.templates_dir, st.run_dirs.addons, repo, repo_id)
    deps._reload_templates(st)
    return root, ids


def _file(client, repo, chain_template=None):
    """File a paused item on `repo`, running `chain_template` when given."""
    chosen = {"chain_template": chain_template} if chain_template else {}
    body = {"title": "t", "repo": str(repo), "autostart": False, **chosen}
    return client.post("/api/work-items", json=body)


def _set_chain(wid, chain_template):
    """A row filed before the scope rule, its chain written directly."""
    db = Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"
    with closing(sqlite3.connect(db)) as c, c:
        c.execute("UPDATE work_items SET chain_template = ? WHERE id = ?", (chain_template, wid))


def _door(door, client, app, web, caplog):
    if door == "create":
        return _file(client, web, chain_template="app:ship")
    if door == "duplicate":
        wid = _file(client, web).json()["id"]
        _set_chain(wid, "app:ship")
        return client.post(f"/api/work-items/{wid}/duplicate")
    if door == "trigger-route":
        return client.post(
            "/api/triggers", json={"title": "t", "repo": str(web), "chain": "app:ship"}
        )
    if door == "switch":
        wid = _file(client, web).json()["id"]
        r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "app:ship"})
        assert client.get(f"/api/work-items/{wid}").json()["chain_template"] is None
        return r
    if door in ("put-intake", "put-policy"):
        entry = {"cron": "0 0 * * *", "repo": str(web), "chain": "app:ship", "title": "t"}
        if door == "put-intake":
            body = {"enabled": False, "interval_s": 60, "priority_ceiling": 2, "schedules": [entry]}
            return client.put("/api/intake", json=body)
        policy = client.get("/api/policy").json()
        return client.put("/api/policy", json={**policy, "triggers": [entry]})
    if door == "auto-intake":
        entry = deps.connected_or_422(client.app.state, str(web))
        with caplog.at_level("WARNING", logger="kraft.intake"):
            wid = client.portal.call(intake._start, client.app, entry, {"id": "b-1", "title": "t"})
        assert wid is None
        (warned,) = [r.message for r in caplog.records if "visible only" in r.message]
        return SimpleNamespace(status_code=422, text=warned, json=lambda: {"detail": warned})
    raise AssertionError(door)


@pytest.mark.parametrize(
    "door, web_entry",
    [
        pytest.param("create", None, id="create-for-another-repo"),
        pytest.param("duplicate", None, id="duplicate-for-another-repo"),
        pytest.param("trigger-route", None, id="trigger-route-for-another-repo"),
        pytest.param("switch", None, id="switch-for-another-repo"),
        pytest.param("steering", {"steering": ["app:tone"]}, id="steering-of-another-repo"),
        pytest.param(
            "auto-intake", {"default_chain": "app:ship"}, id="auto-intake-for-another-repo"
        ),
        pytest.param("put-intake", None, id="put-intake-schedule-for-another-repo"),
        pytest.param("put-policy", None, id="put-policy-trigger-for-another-repo"),
        pytest.param("workspace", None, id="workspace-members-library"),
    ],
)
def test_a_repo_library_is_refused_outside_its_repo(client, tmp_path, caplog, door, web_entry):
    if door == "workspace":
        root, ids = _workspace(client, tmp_path)
        member = next(i for i in ids.values() if i != ids[str(root)])
        r = _file(client, root, chain_template=f"{member}:ship")
        where = member
    else:
        app, web = _two_repos(client, tmp_path, web_entry)
        r = _file(client, web) if door == "steering" else _door(door, client, app, web, caplog)
        where = "app"
    assert r.status_code == 422, r.text
    assert f"visible only to items in {where}" in r.json()["detail"]


_ADD = {
    "op": "add_node",
    "after": "n",
    "node": {"id": "x", "tasks": [{"id": "t", "extends": "app:base"}]},
}


@pytest.mark.parametrize(
    "case",
    ["create-own", "default-own", "switch-own", "workspace-roots-library", "draft-adds-own"],
)
def test_a_repo_library_is_used_by_its_own_repo(client, tmp_path, case):
    if case == "workspace-roots-library":
        root, ids = _workspace(client, tmp_path)
        r = _file(client, root, chain_template=f"{ids[str(root)]}:ship")
        assert r.status_code == 201, r.text
        return
    st = client.app.state
    app, web = _two_repos(client, tmp_path)
    if case == "default-own":
        repo_library_files(app, chains={"default": chain("base")})
        accept_repo_library(st.templates_dir, st.run_dirs.addons, app, "app")
        deps._reload_templates(st)
        r, stored = _file(client, app), "app:default"
    elif case == "draft-adds-own":
        wid = _file(client, app, chain_template="app:ship").json()["id"]
        r = client.put(f"/api/work-items/{wid}/draft", json={"ops": [_ADD]})
        assert (r.status_code, r.json().get("problems")) == (200, []), r.text
    elif case == "switch-own":
        wid = _file(client, app).json()["id"]
        r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "app:ship"})
        assert r.status_code == 200, r.text
    else:
        r, stored = _file(client, app, chain_template="app:ship"), "app:ship"
    if case not in ("switch-own", "draft-adds-own"):
        assert r.status_code == 201, r.text
        item = client.get(f"/api/work-items/{r.json()['id']}").json()
        assert item["chain_template"] == stored


@pytest.mark.parametrize(
    "repos_yaml, repo_id",
    [
        pytest.param(None, "app", id="single-repo"),
        pytest.param("[", None, id="poisoned-repos-yaml"),
    ],
)
def test_a_launch_carries_its_items_repository_id(client, tmp_path, repos_yaml, repo_id):
    st = client.app.state
    app, _web = _two_repos(client, tmp_path)
    if repos_yaml is not None:
        (st.templates_dir / "repos.yaml").write_text(repos_yaml)

    assert deps.launch(st, str(app)).item_repo_id == repo_id
