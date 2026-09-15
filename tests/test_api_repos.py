"""Repo CRUD (design 5a) and the `config` probe/load/save helpers it is built on."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from support.api_settings import _client
from support.harness import fake_templates_dir, isolated_bd, make_repo, make_repo_with_submodule

from kraft import config, events, store, templates
from kraft import db as kdb
from kraft.paths import RunDirs

_FAKE_AGENT = Path(__file__).resolve().parents[0] / "support" / "fake_agent.py"


@pytest.fixture
def templates_dir(tmp_path):
    return fake_templates_dir(tmp_path, "claude")


@pytest.fixture
def client(tmp_path, monkeypatch, templates_dir):
    with _client(tmp_path, monkeypatch, templates_dir) as c:
        yield c


_GATED_CHAIN = {
    "nodes": [
        {
            "id": "review",
            "tasks": ["on.human_review.requested"],
            "gate_after": "human_review_approval",
        },
    ]
}


def test_probe_reads_the_repo_without_touching_it(tmp_path, client):
    repo = make_repo(tmp_path)
    before = sorted(p.name for p in repo.iterdir())

    body = client.post("/api/repos/probe", json={"path": str(repo)}).json()
    assert body["path"] == str(repo.resolve())
    assert body["branch"]
    assert body["submodules"] == []
    assert sorted(p.name for p in repo.iterdir()) == before

    assert client.post("/api/repos/probe", json={"path": str(tmp_path / "nope")}).status_code == 400
    plain = tmp_path / "plain"
    plain.mkdir()
    assert client.post("/api/repos/probe", json={"path": str(plain)}).status_code == 400


def test_probe_finds_submodules_and_a_test_command(tmp_path, client):
    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    (repo / ".gitmodules").write_text(
        '[submodule "libs/a"]\n\tpath = libs/a\n\turl = ../a.git\n'
        '[submodule "libs/b"]\n\tpath = libs/b\n\turl = ../b.git\n'
    )
    body = client.post("/api/repos/probe", json={"path": str(repo)}).json()
    assert body["submodules"] == ["libs/a", "libs/b"]
    assert body["test_command"] == "uv run pytest -q"


def test_repo_crud_round_trips_through_the_yaml(tmp_path, client, templates_dir):
    repo = make_repo(tmp_path)
    created = client.post(
        "/api/repos",
        json={"path": str(repo), "default_chain_template": "default", "enabled": False},
    )
    assert created.status_code == 201
    path = created.json()["path"]

    on_disk = yaml.safe_load((templates_dir / "repos.yaml").read_text())
    assert on_disk["repos"][0]["path"] == path
    assert client.get("/api/repos").json()["repos"][0]["default_chain_template"] == "default"

    # connecting the same repo twice is a conflict, not a duplicate row
    assert client.post("/api/repos", json={"path": str(repo)}).status_code == 409

    patched = client.patch(f"/api/repos?path={path}", json={"enabled": False, "name": "renamed"})
    assert patched.json()["enabled"] is False and patched.json()["name"] == "renamed"
    assert client.patch("/api/repos?path=/nope", json={"enabled": False}).status_code == 404

    # POST stores git's resolved toplevel, so the path a client connected with is
    # not always the path stored — patch and delete must still find it.
    assert (
        client.patch(
            f"/api/repos?path={repo}", json={"enabled": True, "test_command": "pytest"}
        ).status_code
        == 200
    )

    assert client.delete(f"/api/repos?path={path}").status_code == 204
    assert client.get("/api/repos").json()["repos"] == []
    assert client.delete(f"/api/repos?path={path}").status_code == 404


def test_add_repo_round_trips_default_model_and_steering(tmp_path, client, templates_dir):
    (templates_dir / "steering").mkdir(exist_ok=True)
    (templates_dir / "steering" / "house-style.md").write_text("# House style\nBe direct.\n")
    repo = make_repo(tmp_path)
    created = client.post(
        "/api/repos",
        json={
            "path": str(repo),
            "enabled": False,
            "default_model": "anything-at-all",
            "deny_tools": ["WebFetch"],
            "steering": ["house-style"],
        },
    )
    assert created.status_code == 201, created.text

    on_disk = yaml.safe_load((templates_dir / "repos.yaml").read_text())
    assert on_disk["repos"][0]["default_model"] == "anything-at-all"
    assert on_disk["repos"][0]["deny_tools"] == ["WebFetch"]
    assert on_disk["repos"][0]["steering"] == ["house-style"]

    fetched = client.get("/api/repos").json()["repos"][0]
    assert fetched["default_model"] == "anything-at-all"
    assert fetched["steering"] == ["house-style"]


def test_add_repo_with_a_missing_steering_name_is_refused(tmp_path, client, templates_dir):
    """Regression guard (task-3 fix round 1): the write side must validate
    before persisting, or a bad POST bricks every later GET /repos."""
    repos_yaml = templates_dir / "repos.yaml"
    assert not repos_yaml.exists()
    repo = make_repo(tmp_path)
    r = client.post("/api/repos", json={"path": str(repo), "steering": ["does-not-exist"]})
    assert 400 <= r.status_code < 500, r.text
    # a rejected write never got persisted
    assert not repos_yaml.exists()
    assert client.get("/api/repos").json()["repos"] == []


def test_patch_repo_with_a_missing_steering_name_is_refused(tmp_path, client, templates_dir):
    repo = make_repo(tmp_path)
    client.post("/api/repos", json={"path": str(repo), "enabled": False})
    before = (templates_dir / "repos.yaml").read_text()

    r = client.patch(f"/api/repos?path={repo}", json={"steering": ["does-not-exist"]})
    assert 400 <= r.status_code < 500, r.text
    assert (templates_dir / "repos.yaml").read_text() == before

    (entry,) = client.get("/api/repos").json()["repos"]
    assert entry["steering"] == []


def test_connecting_a_workspace_auto_connects_its_submodules_disabled(tmp_path, client):
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    client.post("/api/repos", json={"path": str(root)})

    repos = {r["path"]: r for r in client.get("/api/repos").json()["repos"]}
    child = repos[str(root / "libs/a")]
    assert child["enabled"] is False
    # detected, nobody has looked at it yet
    assert child["managed"] is False
    # the human typed the workspace path, so it is a decision from the start
    assert repos[str(root)]["managed"] is True


def test_a_hand_added_repo_is_managed_even_when_left_disabled(tmp_path, client):
    repo = make_repo(tmp_path)
    body = client.post("/api/repos", json={"path": str(repo), "enabled": False}).json()
    assert body["enabled"] is False
    assert body["managed"] is True


def test_auto_connect_never_overwrites_an_existing_entry(tmp_path, client):
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    child = str(root / "libs/a")
    client.post("/api/repos", json={"path": child, "test_command": "cargo test"})
    client.post("/api/repos", json={"path": str(root)})

    repos = {r["path"]: r for r in client.get("/api/repos").json()["repos"]}
    # the hand-connected child keeps its command and its latch
    assert repos[child]["test_command"] == "cargo test"
    assert repos[child]["managed"] is True


def test_patching_a_detected_child_latches_it_managed(tmp_path, client):
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    client.post("/api/repos", json={"path": str(root)})
    child = str(root / "libs/a")

    # editing a field without enabling is still a touch
    client.patch(f"/api/repos?path={child}", json={"test_command": "cargo test"})
    repos = {r["path"]: r for r in client.get("/api/repos").json()["repos"]}
    assert repos[child]["managed"] is True
    assert repos[child]["enabled"] is False


def test_managed_never_returns_to_false(tmp_path, client):
    repo = make_repo(tmp_path)
    client.post("/api/repos", json={"path": str(repo)})
    client.patch(f"/api/repos?path={repo}", json={"enabled": False})
    repos = {r["path"]: r for r in client.get("/api/repos").json()["repos"]}
    assert repos[str(repo)]["managed"] is True


def test_add_repo_stores_probed_forge(client, tmp_path):
    repo = make_repo(tmp_path, name="ghrepo")
    _set_origin(repo, "git@github.com:owner/repo.git")
    r = client.post("/api/repos", json={"path": str(repo), "enabled": False})
    assert r.status_code == 201
    assert r.json()["forge"] == "github"
    assert r.json()["project"] == "owner/repo"
    assert "gitlab_project" not in r.json()


def test_patch_repo_overrides_forge(client, tmp_path):
    repo = make_repo(tmp_path, name="patchrepo")
    client.post("/api/repos", json={"path": str(repo), "enabled": False})
    r = client.patch(f"/api/repos?path={repo}", json={"forge": "gitea", "project": "t/r"})
    assert r.status_code == 200
    (entry,) = [
        x for x in client.get("/api/repos").json()["repos"] if x["path"] == str(repo.resolve())
    ]
    assert entry["forge"] == "gitea"
    assert entry["project"] == "t/r"


def test_patch_repo_can_clear_a_field_with_an_explicit_null(client, tmp_path):
    """PATCH must distinguish an omitted key (leave alone) from an explicit
    `null` (clear) — the RepoDetail draft sends the whole Repo back, nulls
    included, when e.g. the forge is set back to 'none'."""
    repo = make_repo(tmp_path, name="clearrepo")
    _set_origin(repo, "git@github.com:owner/repo.git")
    client.post("/api/repos", json={"path": str(repo), "enabled": False})
    r = client.patch(
        f"/api/repos?path={repo}",
        json={"forge": None, "project": None, "default_model": None},
    )
    assert r.status_code == 200, r.text
    (entry,) = [
        x for x in client.get("/api/repos").json()["repos"] if x["path"] == str(repo.resolve())
    ]
    assert entry["forge"] is None
    assert entry["project"] is None
    assert entry["default_model"] is None


def test_add_repo_with_a_test_command_still_records_probed_scopes(tmp_path, client):
    """Kraft-k4mx: an explicit test_command used to skip test_scopes probing
    entirely and for good -- there was no field to add scopes back with after
    connecting. This repo's own entry ran `pytest --testmon` against
    frontend-only diffs for weeks because of exactly this."""
    repo = make_repo(tmp_path, name="scoperepo")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}")

    r = client.post(
        "/api/repos",
        json={"path": str(repo), "enabled": False, "test_command": "just test"},
    )
    assert r.status_code == 201, r.text
    scopes = r.json()["test_scopes"]
    assert scopes is not None, "test_command must no longer suppress probing"
    by_paths = {tuple(s["paths"]): s["command"] for s in scopes}
    # the explicit test_command replaces the *root* scope's command...
    assert "just test" in by_paths.values()
    # ...but the nested frontend scope _probe_test_scopes found on its own
    # survives untouched, not silently dropped by the override.
    assert by_paths[("frontend/**",)] == "npm test"


def test_add_repo_without_nested_scopes_does_not_persist_a_root_scope(tmp_path, client):
    """A single-stack repo has no nested scopes to probe, so the only thing
    _probe_test_scopes finds is a root `["**"]` scope that just repeats
    test_command. Persisting it would shadow every later test_command edit
    forever -- the stale-override bug `_normalize_test_scopes`'s docstring
    describes (Kraft-9wzy) -- so add_repo must leave test_scopes unset here."""
    repo = make_repo(tmp_path, name="plainscoperepo")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    r = client.post("/api/repos", json={"path": str(repo), "enabled": False})
    assert r.status_code == 201, r.text
    assert r.json()["test_scopes"] is None


def test_add_repo_keeps_a_lone_nested_scope(tmp_path, client):
    """A repo whose only test marker is nested probes to exactly one scope --
    a real nested one. Counting scopes would discard it and run the command
    against every diff (verify finding on this item)."""
    repo = make_repo(tmp_path, name="frontendonly")
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}")

    r = client.post("/api/repos", json={"path": str(repo), "enabled": False})
    assert r.status_code == 201, r.text
    assert r.json()["test_scopes"] == [{"paths": ["frontend/**"], "command": "npm test"}]


def test_patch_repo_sets_test_scopes_on_an_existing_entry(client, tmp_path):
    """Kraft-k4mx: the missing half. Editing scopes after connecting used to
    have no field to write through."""
    repo = make_repo(tmp_path, name="patchscopes")
    client.post("/api/repos", json={"path": str(repo), "enabled": False})
    scopes = [{"paths": ["frontend/**"], "command": "just test-ui"}]
    r = client.patch(f"/api/repos?path={repo}", json={"test_scopes": scopes})
    assert r.status_code == 200, r.text
    assert r.json()["test_scopes"] == scopes
    (entry,) = [
        x for x in client.get("/api/repos").json()["repos"] if x["path"] == str(repo.resolve())
    ]
    assert entry["test_scopes"] == scopes


def test_patch_repo_can_clear_test_scopes_with_an_explicit_null(client, tmp_path):
    repo = make_repo(tmp_path, name="clearscopes")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    client.post("/api/repos", json={"path": str(repo), "enabled": False})
    client.patch(
        f"/api/repos?path={repo}",
        json={"test_scopes": [{"paths": ["**"], "command": "uv run pytest -q"}]},
    )
    assert client.get("/api/repos").json()["repos"][0]["test_scopes"] is not None
    r = client.patch(f"/api/repos?path={repo}", json={"test_scopes": None})
    assert r.status_code == 200, r.text
    assert r.json()["test_scopes"] is None


def _set_origin(repo, url):
    subprocess.run(["git", "remote", "add", "origin", url], cwd=repo, check=True)


def test_probe_survives_a_repo_whose_remote_was_removed(tmp_path):
    """Distinct from test_probe_detects_no_forge_without_remote: make_repo never
    adds an origin, so without this the `git remote remove` was a silent no-op
    and both tests exercised the same never-had-a-remote state."""
    repo = make_repo(tmp_path)
    _set_origin(repo, "git@gitlab.com:group/repo.git")
    subprocess.run(["git", "remote", "remove", "origin"], cwd=repo, check=True)
    probed = config.probe_repo(repo)
    assert probed["forge"] is None
    assert probed["project"] is None
    assert "gitlab_project" not in probed
    assert Path(probed["path"]) == repo.resolve()


def test_probe_survives_a_gitmodules_that_is_not_utf8(tmp_path):
    """`.gitmodules` is read best-effort — a repo whose submodule list cannot be
    parsed still probes, with no submodules. read_text() raises UnicodeDecodeError,
    a ValueError, which `except OSError` does not catch."""
    repo = make_repo(tmp_path)
    (repo / ".gitmodules").write_bytes(b'[submodule "\xff\xfe libs/x"]\n\tpath = libs/x\n')
    assert config.probe_repo(repo)["submodules"] == []


def test_an_expected_git_failure_logs_at_debug(caplog):
    """`remote get-url origin` failing is a normal probe outcome — _detect_forge
    returns (None, None) and the probe succeeds — so it must not warn. git_read's
    warning stays for failures no caller handles."""
    import logging

    with caplog.at_level(logging.DEBUG, logger="kraft.config"):
        assert (
            config.git_read(Path.cwd(), "remote", "get-url", "nope", expected_failure=True) is None
        )
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert any(r.levelno == logging.DEBUG for r in caplog.records)


def test_an_unhandled_git_failure_still_warns(caplog):
    import logging

    with caplog.at_level(logging.DEBUG, logger="kraft.config"):
        assert config.git_read(Path.cwd(), "remote", "get-url", "nope") is None
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_an_explicit_project_survives_a_legacy_key_beside_it():
    """A hand-edited half-migrated entry: `project` set, `forge` absent, and the
    pre-rename `gitlab_project` still present. The explicit value wins."""
    repo = {"path": "/r", "project": "group/kept", "gitlab_project": "group/legacy"}
    config._normalize_forge(repo)
    assert repo["project"] == "group/kept"
    assert repo["forge"] is None
    assert "gitlab_project" not in repo


def test_the_legacy_key_still_migrates_when_nothing_else_is_set():
    repo = {"path": "/r", "gitlab_project": "group/legacy"}
    config._normalize_forge(repo)
    assert repo == {"path": "/r", "forge": "gitlab", "project": "group/legacy"}


def test_probe_detects_no_forge_without_remote(tmp_path):
    repo = make_repo(tmp_path)
    probed = config.probe_repo(repo)
    assert probed["forge"] is None
    assert probed["project"] is None
    assert "gitlab_project" not in probed


def test_probe_detects_gitlab_ssh(tmp_path):
    repo = make_repo(tmp_path)
    _set_origin(repo, "git@gitlab.com:group/repo.git")
    probed = config.probe_repo(repo)
    assert probed["forge"] == "gitlab"
    assert probed["project"] == "group/repo"


def test_probe_detects_gitlab_https(tmp_path):
    repo = make_repo(tmp_path)
    _set_origin(repo, "https://gitlab.com/group/sub/repo.git")
    probed = config.probe_repo(repo)
    assert probed["forge"] == "gitlab"
    assert probed["project"] == "group/sub/repo"


def test_probe_detects_github_ssh(tmp_path):
    repo = make_repo(tmp_path)
    _set_origin(repo, "git@github.com:owner/repo.git")
    probed = config.probe_repo(repo)
    assert probed["forge"] == "github"
    assert probed["project"] == "owner/repo"


def test_probe_detects_github_https(tmp_path):
    repo = make_repo(tmp_path)
    _set_origin(repo, "https://github.com/owner/repo")
    probed = config.probe_repo(repo)
    assert probed["forge"] == "github"
    assert probed["project"] == "owner/repo"


def test_probe_unknown_host_is_not_an_error(tmp_path):
    repo = make_repo(tmp_path)
    _set_origin(repo, "git@git.example.com:team/repo.git")
    probed = config.probe_repo(repo)
    assert probed["forge"] is None
    assert probed["project"] is None
    assert probed["name"] == "sample"


def test_load_repos_reads_legacy_gitlab_project(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "gitlab_project": "group/repo"}]})
    (repo,) = config.load_repos(path)
    assert repo["forge"] == "gitlab"
    assert repo["project"] == "group/repo"
    assert "gitlab_project" not in repo


def test_load_repos_passes_through_new_shape(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "forge": "github", "project": "o/r"}]})
    (repo,) = config.load_repos(path)
    assert repo["forge"] == "github"
    assert repo["project"] == "o/r"


def test_load_repos_sandbox_defaults_to_none(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r"}]})
    (repo,) = config.load_repos(path)
    assert repo["sandbox"] is None


def test_load_repos_passes_through_a_well_formed_sandbox(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(
        path,
        {"repos": [{"path": "/r", "sandbox": {"kind": "docker", "image": "kraft-worker:node"}}]},
    )
    (repo,) = config.load_repos(path)
    assert repo["sandbox"] == {"kind": "docker", "image": "kraft-worker:node"}


def test_load_repos_passes_through_an_explicit_sandbox_off(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "sandbox": False}]})
    (repo,) = config.load_repos(path)
    assert repo["sandbox"] is False


def test_load_repos_rejects_a_malformed_sandbox(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "sandbox": {"kind": "docker"}}]})
    with pytest.raises(config.ConfigError, match="image"):
        config.load_repos(path)


def test_load_repos_new_shape_wins_over_legacy(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(
        path,
        {"repos": [{"path": "/r", "forge": "github", "project": "o/r", "gitlab_project": "g/r"}]},
    )
    (repo,) = config.load_repos(path)
    assert repo["forge"] == "github"
    assert repo["project"] == "o/r"
    assert "gitlab_project" not in repo


def test_load_repos_defaults_both_to_none(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r"}]})
    (repo,) = config.load_repos(path)
    assert repo["forge"] is None
    assert repo["project"] is None


def test_load_repos_keeps_unknown_forge(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "forge": "gitea", "project": "t/r"}]})
    (repo,) = config.load_repos(path)
    assert repo["forge"] == "gitea"


def test_load_repos_rejects_a_missing_steering_file(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "steering": ["missing"]}]})
    with pytest.raises(config.ConfigError):
        config.load_repos(path)


def test_save_repos_round_trip_drops_legacy_key(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "gitlab_project": "group/repo"}]})
    config.save_repos(path, config.load_repos(path))
    assert "gitlab_project" not in path.read_text()
    assert "forge: gitlab" in path.read_text()


def _seed_active_work_item(
    run_dir: Path, *, wid: str, node_id: str, gate: str | None = None
) -> None:
    """Write a work item straight into a fresh run_dir's DB, bypassing the API,
    so it is already there — 'active', at `node_id` — before a server ever
    boots against this `run_dir` and its own `lifespan` runs reattach."""
    rd = RunDirs(run_dir).ensure()

    async def seed():
        database = await kdb.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=wid,
                    bead_id="B-1",
                    title="t",
                    repo="/r",
                    chain_template="quick-task",
                    chain_definition=json.dumps(_GATED_CHAIN),
                    status="active",
                )
            )
            await database.write(lambda c: store.load_chain(c, wid, node_id))
            if gate:
                await database.write(
                    lambda c: events.append(
                        c, wid, "gate_requested", {"gate": gate, "node_id": node_id}
                    )
                )
        finally:
            await database.close()

    asyncio.run(seed())


def _broken_repos_yaml(templates_dir: Path) -> None:
    """A repo naming a steering file that does not exist — an operator's hand
    edit, or a steering file deleted after the fact. Written directly, bypassing
    `POST /repos`'s own validation, which would refuse this on the way in."""
    (templates_dir / "repos.yaml").write_text(
        yaml.safe_dump({"repos": [{"path": "/r", "steering": ["deleted"]}]})
    )


def test_a_broken_repos_yaml_does_not_prevent_startup(tmp_path, monkeypatch):
    """`_launch` runs on the reattach path too: `lifespan` calls it for every
    work item still 'active' at boot (crash recovery). A malformed repos.yaml,
    or a steering file an operator deleted, must degrade — like `invalid_policy`
    already does for a bad policy.yaml — not crash the whole server and lock the
    operator out of the Settings UI that would let them fix it."""
    templates_dir = fake_templates_dir(tmp_path, "claude")
    _broken_repos_yaml(templates_dir)
    run_dir = tmp_path / "run"
    _seed_active_work_item(run_dir, wid="w-active", node_id="review")

    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    with TestClient(api.app, client=("127.0.0.1", 54321)) as client:  # must not raise
        assert client.get("/api/health").status_code == 200


def test_a_broken_repos_yaml_does_not_500_the_approve_path(tmp_path, monkeypatch):
    """Same broken repos.yaml, through a request instead of startup:
    `approve_gate` builds `_launch(st, row['repo'])` eagerly while constructing
    the `executor.run` coroutine — outside `_guard`'s try/except. It must
    degrade, not 500."""
    templates_dir = fake_templates_dir(tmp_path, "claude")
    _broken_repos_yaml(templates_dir)
    run_dir = tmp_path / "run"
    _seed_active_work_item(run_dir, wid="w-gated", node_id="review", gate="human_review_approval")

    with _client(tmp_path, monkeypatch, templates_dir) as client:
        r = client.post("/api/work-items/w-gated/gates/human_review_approval/approve")
        assert r.status_code == 200


def test_connected_repos_default_model_reaches_the_agent_launch(tmp_path, monkeypatch):
    """Pins the `_connected` wiring the executor tests bypass by constructing
    `LaunchContext` by hand: connect a repo through the real API (its path
    round-trips through git's symlink-resolving `--show-toplevel`, the whole
    reason `_connected` exists over `r["path"] == repo`), then post a work item
    against the same, unresolved path and check `--model` reaches the agent."""
    templates_dir = fake_templates_dir(tmp_path, f"{sys.executable} {_FAKE_AGENT}")
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    repo = make_repo(tmp_path)

    with _client(tmp_path, monkeypatch, templates_dir) as client:
        added = client.post(
            "/api/repos",
            json={"path": str(repo), "default_model": "haiku", "test_command": "pytest"},
        )
        assert added.status_code == 201

        # quick-task: the only agent hook this fixture binds is
        # `on.implementation.start`, and on the default chain that sits behind
        # three gates the item never gets past (`on.spec.requested` is a noop
        # here), so no agent would ever launch to inspect.
        wid = client.post(
            "/api/work-items",
            json={"title": "x", "repo": str(repo), "chain_template": "quick-task"},
        ).json()["id"]

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not argv_log.exists():
            time.sleep(0.2)
        assert argv_log.exists(), f"agent never launched for {wid}"

    argv = json.loads(argv_log.read_text().splitlines()[0])
    assert argv[argv.index("--model") + 1] == "haiku"


def test_connected_repos_steering_reaches_the_agent_launch(tmp_path, monkeypatch):
    """The `_launch` -> `resolve_invocation` seam for steering, not just
    `--model`: connect a repo with `steering: ["house"]` through the real API,
    post a work item, and check the house body reaches the agent's
    `--append-system-prompt`. Both executor launch tests pass `steering_dir=None`
    and never exercise this join; this is the sibling that does."""
    templates_dir = fake_templates_dir(tmp_path, f"{sys.executable} {_FAKE_AGENT}")
    (templates_dir / "steering").mkdir(exist_ok=True)
    (templates_dir / "steering" / "house.md").write_text("Prefer tabs over spaces.")
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    repo = make_repo(tmp_path)

    with _client(tmp_path, monkeypatch, templates_dir) as client:
        added = client.post(
            "/api/repos", json={"path": str(repo), "steering": ["house"], "test_command": "pytest"}
        )
        assert added.status_code == 201, added.text

        # quick-task: the only agent hook this fixture binds is
        # `on.implementation.start`, and on the default chain that sits behind
        # three gates the item never gets past (`on.spec.requested` is a noop
        # here), so no agent would ever launch to inspect.
        wid = client.post(
            "/api/work-items",
            json={"title": "x", "repo": str(repo), "chain_template": "quick-task"},
        ).json()["id"]

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not argv_log.exists():
            time.sleep(0.2)
        assert argv_log.exists(), f"agent never launched for {wid}"

    argv = json.loads(argv_log.read_text().splitlines()[0])
    prompt = argv[argv.index("--append-system-prompt") + 1]
    assert "Prefer tabs over spaces." in prompt


def test_get_repos_with_a_deleted_steering_file_does_not_lock_out_the_screen(tmp_path, monkeypatch):
    """A steering file deleted after the fact (an operator's `rm`, since there is
    no Settings screen for steering files) must not 422 the only screen that
    could fix it. `GET /repos` reads without steering validation and returns the
    entry as-is; `PATCH /repos` clearing the bad name must succeed too — that is
    how an operator actually recovers, short of hand-editing the YAML."""
    templates_dir = fake_templates_dir(tmp_path, "claude")
    _broken_repos_yaml(templates_dir)

    with _client(tmp_path, monkeypatch, templates_dir) as client:
        got = client.get("/api/repos")
        assert got.status_code == 200
        assert got.json()["repos"][0]["steering"] == ["deleted"]

        patched = client.patch("/api/repos?path=/r", json={"steering": []})
        assert patched.status_code == 200
        assert patched.json()["steering"] == []
        assert client.get("/api/repos").json()["repos"][0]["steering"] == []


def test_probe_from_a_worktree_reports_the_main_checkout(tmp_path):
    """An agent's cwd IS a linked worktree, and `/kraft:handoff` tells it to call
    `ensure_repo()` every time. Without this, every handoff registers the
    worktree as a repo of its own — observed live in repos.yaml."""
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(worktree), "-b", "wt-branch"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    assert Path(config.probe_repo(worktree)["path"]) == repo.resolve()


def test_probe_of_a_submodule_stays_the_submodule(tmp_path):
    """A submodule's common dir is `<super>/.git/modules/<path>`, whose parent is
    `<super>/.git/modules` — not a repo at all. The `.git` guard keeps a
    submodule on the `--show-toplevel` answer it has always had."""
    lib = make_repo(tmp_path, name="lib")
    super_repo = make_repo(tmp_path, name="super")
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(lib),
            "libs/sub",
        ],
        cwd=super_repo,
        check=True,
        capture_output=True,
    )
    assert (
        Path(config.probe_repo(super_repo / "libs" / "sub")["path"])
        == (super_repo / "libs" / "sub").resolve()
    )


def test_load_repos_defaults_root_merge_policy(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r"}]})
    (repo,) = config.load_repos(path)
    assert repo["default_root_merge_policy"] == "bump"


def test_load_repos_defaults_managed_true_for_pre_existing_entries(tmp_path):
    # Anything already in a repos.yaml was connected by a human -- auto-connect
    # did not exist when it was written. Defaulting to False here would hide
    # every repo a user has behind the Detected section on first load.
    path = tmp_path / "repos.yaml"
    path.write_text(yaml.safe_dump({"repos": [{"path": "/a"}]}))
    assert config.load_repos(path)[0]["managed"] is True


def test_load_repos_keeps_an_explicit_managed_false(tmp_path):
    path = tmp_path / "repos.yaml"
    path.write_text(yaml.safe_dump({"repos": [{"path": "/a", "managed": False}]}))
    assert config.load_repos(path)[0]["managed"] is False


def test_load_repos_rejects_a_non_boolean_managed(tmp_path):
    path = tmp_path / "repos.yaml"
    path.write_text(yaml.safe_dump({"repos": [{"path": "/a", "managed": "yes"}]}))
    with pytest.raises(config.ConfigError, match="'managed' must be a boolean"):
        config.load_repos(path)


def test_a_configured_submodule_edge_becomes_a_child_repo_entry(tmp_path):
    path = tmp_path / "repos.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "repos": [
                    {
                        "path": "/ws",
                        "name": "ws",
                        "submodules": [
                            {
                                "path": "libs/a",
                                "enabled": True,
                                "test_command": "cargo test",
                                "chain_override": "quick",
                            },
                        ],
                    }
                ]
            }
        )
    )
    repos = config.load_repos(path)
    assert [r["path"] for r in repos] == ["/ws", "/ws/libs/a"]
    child = repos[1]
    assert child["name"] == "a"
    assert child["enabled"] is True
    assert child["test_command"] == "cargo test"
    assert child["default_chain_template"] == "quick"
    # a human had set these values, so the child is a decision, not noise
    assert child["managed"] is True


def test_an_all_default_submodule_edge_is_dropped(tmp_path):
    # Carries no human decision, so there is nothing to preserve. Task 3's
    # auto-connect re-creates it as managed: false.
    path = tmp_path / "repos.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "repos": [
                    {
                        "path": "/ws",
                        "submodules": [{"path": "libs/a", "enabled": False}],
                    }
                ]
            }
        )
    )
    repos = config.load_repos(path)
    assert [r["path"] for r in repos] == ["/ws"]


def test_an_existing_child_entry_wins_over_a_legacy_edge(tmp_path):
    path = tmp_path / "repos.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "repos": [
                    {"path": "/ws", "submodules": [{"path": "libs/a", "test_command": "stale"}]},
                    {"path": "/ws/libs/a", "test_command": "real"},
                ]
            }
        )
    )
    repos = config.load_repos(path)
    assert [r["path"] for r in repos] == ["/ws", "/ws/libs/a"]
    assert repos[1]["test_command"] == "real"


def test_migration_drops_submodules_and_allow_cross_repo_keys(tmp_path):
    path = tmp_path / "repos.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "repos": [
                    {
                        "path": "/ws",
                        "allow_cross_repo": True,
                        "submodules": [{"path": "libs/a", "enabled": True}],
                    }
                ]
            }
        )
    )
    for entry in config.load_repos(path):
        assert "submodules" not in entry
        assert "allow_cross_repo" not in entry


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "repos.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "repos": [
                    {
                        "path": "/ws",
                        "submodules": [{"path": "libs/a", "enabled": True}],
                    }
                ]
            }
        )
    )
    once = config.load_repos(path)
    config.save_repos(path, once)
    assert config.load_repos(path) == once


def test_load_repos_rejects_unknown_root_merge_policy(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "default_root_merge_policy": "nope"}]})
    with pytest.raises(config.ConfigError):
        config.load_repos(path)


def test_add_repo_enabled_with_no_test_command_is_refused(tmp_path, monkeypatch):
    # `noop_verify=True` leaves the registry's `on.test.run` without a
    # `command` too, so this actually exercises "nothing to run anywhere" —
    # the default `client` fixture's `on.test.run` is a real subprocess
    # command, which the repo is now allowed to fall back on.
    no_fallback_templates_dir = fake_templates_dir(tmp_path, "claude", noop_verify=True)
    with _client(tmp_path, monkeypatch, no_fallback_templates_dir) as client:
        repo = make_repo(tmp_path)  # sample_repo has no pyproject.toml/package.json marker
        r = client.post("/api/repos", json={"path": str(repo), "enabled": True})
        assert r.status_code == 422, r.text
        assert "test command" in r.json()["detail"]


def test_add_repo_enabled_with_no_repo_test_command_falls_back_to_registry(tmp_path, client):
    # templates_dir binds `on.test.run` to a real subprocess command, so a
    # repo with neither `test_command` nor `test_scopes` can still enable —
    # `executor.dispatch` runs the registry's command for it.
    repo = make_repo(tmp_path)
    r = client.post("/api/repos", json={"path": str(repo), "enabled": True})
    assert r.status_code == 201, r.text


def test_add_repo_disabled_with_no_test_command_is_allowed(tmp_path, client):
    repo = make_repo(tmp_path)
    r = client.post("/api/repos", json={"path": str(repo), "enabled": False})
    assert r.status_code == 201, r.text


def test_patch_repo_cannot_enable_without_a_test_command(tmp_path, monkeypatch):
    no_fallback_templates_dir = fake_templates_dir(tmp_path, "claude", noop_verify=True)
    with _client(tmp_path, monkeypatch, no_fallback_templates_dir) as client:
        repo = make_repo(tmp_path)
        path = client.post("/api/repos", json={"path": str(repo), "enabled": False}).json()["path"]
        r = client.patch(f"/api/repos?path={path}", json={"enabled": True})
        assert r.status_code == 422, r.text


def test_patch_repo_can_enable_relying_on_the_registrys_test_command(tmp_path, client):
    repo = make_repo(tmp_path)
    path = client.post("/api/repos", json={"path": str(repo), "enabled": False}).json()["path"]
    r = client.patch(f"/api/repos?path={path}", json={"enabled": True})
    assert r.status_code == 200, r.text


def test_patch_repo_can_enable_alongside_a_test_command_in_the_same_request(
    tmp_path, client, templates_dir
):
    repo = make_repo(tmp_path)
    path = client.post("/api/repos", json={"path": str(repo), "enabled": False}).json()["path"]
    r = client.patch(f"/api/repos?path={path}", json={"enabled": True, "test_command": "pytest"})
    assert r.status_code == 200, r.text


def test_packaged_registry_wires_the_never_signal_steering_rule():
    """Kraft-f8u3: the rule the spec asks for actually ships wired to every
    repo, via the hook binding -- not just to the one repo happening to be
    listed in repos.yaml, which every real repo registered through POST
    /api/repos would never see."""
    templates_dir = Path(__file__).resolve().parents[1] / "templates"
    registry = templates.load_registry(
        templates_dir / "registry.yaml", steering_dir=templates_dir / "steering"
    )
    binding = registry.hooks["on.implementation.start"]
    assert "never-signal-processes-you-didnt-start" in binding.get("steering", [])


def test_startup_hardens_the_git_env_for_everything_the_server_spawns(tmp_path, monkeypatch):
    """Kraft-rki: the sandbox's guarantee is that a hook a worker plants in
    the gitdir it must be able to write cannot execute on the host. That holds
    only if the pin is on the server process itself -- every git Kraft runs,
    and every git those spawn, inherits it from here, so no call site has to
    remember."""
    templates_dir = fake_templates_dir(tmp_path, "claude")
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    import kraft.api as api

    with TestClient(api.app, client=("127.0.0.1", 54321)):
        count = int(os.environ["GIT_CONFIG_COUNT"])
        pinned = {
            os.environ[f"GIT_CONFIG_KEY_{i}"]: os.environ[f"GIT_CONFIG_VALUE_{i}"]
            for i in range(count)
        }
    assert pinned["core.hooksPath"] == os.devnull
