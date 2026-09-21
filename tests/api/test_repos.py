"""Repo CRUD (design 5a) over HTTP. The `config` helpers under it: tests/test_config_repos.py."""

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
from pydantic import ValidationError
from support.api import _client
from support.harness import (
    fake_templates_dir,
    isolated_bd,
    make_repo,
    make_repo_with_submodule,
    v1_chain,
    v1_item,
)

from kraft import db as kdb
from kraft import events, store, templates
from kraft.api.routes.repos import RepoBody, RepoPatch
from kraft.paths import RunDirs

#: No default repo entry for an unconnected repo (`support.api._client`): these read real config.
pytestmark = pytest.mark.api_client(default_setup=False)


_FAKE_AGENT = Path(__file__).resolve().parents[1] / "support" / "fake_agent.py"


def _gated_chain():
    """`review` (exec) -> `human_review_approval` (gate)."""
    return v1_chain(
        [
            {
                "id": "review",
                "kind": "exec",
                "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
            },
            {"id": "human_review_approval", "kind": "gate"},
        ],
        repo="/r",
    )


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


def _set_origin(repo, url):
    subprocess.run(["git", "remote", "add", "origin", url], cwd=repo, check=True)


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


def test_add_repo_round_trips_models_and_steering(tmp_path, client, templates_dir):
    (templates_dir / "steering").mkdir(exist_ok=True)
    (templates_dir / "steering" / "house-style.md").write_text("# House style\nBe direct.\n")
    repo = make_repo(tmp_path)
    created = client.post(
        "/api/repos",
        json={
            "path": str(repo),
            "enabled": False,
            "models": {"claude_review": "anything-at-all"},
            "deny_tools": ["WebFetch"],
            "steering": ["house-style"],
        },
    )
    assert created.status_code == 201, created.text

    on_disk = yaml.safe_load((templates_dir / "repos.yaml").read_text())
    assert on_disk["repos"][0]["models"] == {"claude_review": "anything-at-all"}
    assert "default_model" not in on_disk["repos"][0]
    assert on_disk["repos"][0]["deny_tools"] == ["WebFetch"]
    assert on_disk["repos"][0]["steering"] == ["house-style"]

    fetched = client.get("/api/repos").json()["repos"][0]
    assert fetched["models"] == {"claude_review": "anything-at-all"}
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


def _disabled(client, repo):
    """Connect `repo`, disabled (no test command needed); return its stored path."""
    r = client.post("/api/repos", json={"path": str(repo), "enabled": False})
    assert r.status_code == 201, r.text
    return r.json()["path"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("local_files", [".python-version"]), ("models", {"codex_default": "gpt-5"})],
    ids=["local-files", "models"],
)
def test_patch_repo_round_trips_a_field(client, repo, templates_dir, field, value):
    """The Settings UI's only write path for these (Kraft-gxcmy): PATCH goes
    through the same `entry.update`/`_validate_repos` machinery as every other
    repos.yaml field, so this pins the round trip end to end rather than
    re-testing `load_repos`' own validation (tests/test_config_repos.py)."""
    _disabled(client, repo)
    r = client.patch(f"/api/repos?path={repo}", json={field: value})
    assert r.status_code == 200, r.text
    on_disk = yaml.safe_load((templates_dir / "repos.yaml").read_text())
    assert on_disk["repos"][0][field] == value
    (entry,) = client.get("/api/repos").json()["repos"]
    assert entry[field] == value


@pytest.mark.parametrize(
    ("field", "value"),
    [("steering", ["does-not-exist"]), ("local_files", ["*.pyc"])],
    ids=["a-missing-steering-name", "a-glob-in-local-files"],
)
def test_a_refused_patch_writes_nothing(client, repo, templates_dir, field, value):
    """Write-side validation must reject exactly what the read side would
    later choke on, so a bad PATCH from the UI cannot brick every later
    GET /repos."""
    _disabled(client, repo)
    before = (templates_dir / "repos.yaml").read_text()
    r = client.patch(f"/api/repos?path={repo}", json={field: value})
    assert 400 <= r.status_code < 500, r.text
    assert (templates_dir / "repos.yaml").read_text() == before
    (entry,) = client.get("/api/repos").json()["repos"]
    assert entry[field] == []


@pytest.mark.parametrize("model, payload", [(RepoBody, {"path": "/r"}), (RepoPatch, {})])
def test_repo_request_models_reject_a_non_string_model(model, payload):
    """An API schema must reject a malformed model map before a route touches disk."""
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "models": {"claude_review": ["opus"]}})


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
        json={"forge": None, "project": None},
    )
    assert r.status_code == 200, r.text
    (entry,) = [
        x for x in client.get("/api/repos").json()["repos"] if x["path"] == str(repo.resolve())
    ]
    assert entry["forge"] is None
    assert entry["project"] is None


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
    forever -- the stale-override bug `config.TestScope`'s comment
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
            await v1_item(database, _gated_chain(), repo="/r", wid=wid, status="active")
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
    # A V1 gate is its own node, and the walk stands on it.
    _seed_active_work_item(
        run_dir, wid="w-gated", node_id="human_review_approval", gate="human_review_approval"
    )

    with _client(tmp_path, monkeypatch, templates_dir=templates_dir, default_setup=False) as client:
        r = client.post("/api/work-items/w-gated/gates/human_review_approval/approve")
        assert r.status_code == 200


def test_connected_repos_model_for_its_profile_reaches_the_agent_launch(tmp_path, monkeypatch):
    """Pins the `_connected` wiring the executor tests bypass by constructing
    `LaunchContext` by hand: connect a repo through the real API (its path
    round-trips through git's symlink-resolving `--show-toplevel`, the whole
    reason `_connected` exists over `r["path"] == repo`), then post a work item
    against the same, unresolved path and check `--model` reaches the agent."""
    templates_dir = fake_templates_dir(tmp_path, f"{sys.executable} {_FAKE_AGENT}")
    # An agent task that names no `model:` of its own: the shipped implementer
    # names one, and a task's own model rightly beats the repo's.
    (templates_dir / "chains" / "impl-only.yaml").write_text(
        "id: impl-only\n"
        "nodes:\n"
        "  - id: implementation\n"
        "    kind: exec\n"
        "    tasks:\n"
        "      - id: implement\n"
        "        kind: agent\n"
        "        harness: fake\n"
        "        prompt: Implement the work item.\n"
    )
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    repo = make_repo(tmp_path)

    with _client(tmp_path, monkeypatch, templates_dir=templates_dir, default_setup=False) as client:
        added = client.post(
            "/api/repos",
            json={
                "path": str(repo),
                "models": {"fake": "haiku"},
                "test_command": "pytest",
                "setup_command": "",
            },
        )
        assert added.status_code == 201

        wid = client.post(
            "/api/work-items",
            json={"title": "x", "repo": str(repo), "chain_template": "impl-only"},
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

    with _client(tmp_path, monkeypatch, templates_dir=templates_dir, default_setup=False) as client:
        added = client.post(
            "/api/repos",
            json={
                "path": str(repo),
                "steering": ["house"],
                "test_command": "pytest",
                "setup_command": "",
            },
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


@pytest.mark.api_client(edit_templates=_broken_repos_yaml)
def test_get_repos_with_a_deleted_steering_file_does_not_lock_out_the_screen(client):
    """A steering file deleted after the fact (an operator's `rm`, since there is
    no Settings screen for steering files) must not 422 the only screen that
    could fix it. `GET /repos` reads without steering validation and returns the
    entry as-is; `PATCH /repos` clearing the bad name must succeed too — that is
    how an operator actually recovers, short of hand-editing the YAML."""
    got = client.get("/api/repos")
    assert got.status_code == 200
    assert got.json()["repos"][0]["steering"] == ["deleted"]

    patched = client.patch("/api/repos?path=/r", json={"steering": []})
    assert patched.status_code == 200
    assert patched.json()["steering"] == []
    assert client.get("/api/repos").json()["repos"][0]["steering"] == []


def test_add_repo_writes_the_probed_setup_command(tmp_path, client):
    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    entry = client.post("/api/repos", json={"path": str(repo)}).json()
    assert entry["setup_command"] == "uv sync"


def test_add_repo_leaves_setup_command_undeclared_with_no_marker(tmp_path, client):
    repo = make_repo(tmp_path, name="plain")
    entry = client.post("/api/repos", json={"path": str(repo)}).json()
    assert entry["setup_command"] is None


def test_add_repo_enabled_with_no_test_command_is_refused(tmp_path, monkeypatch):
    # `noop_verify=True` leaves the registry's `on.test.run` without a
    # `command` too, so this actually exercises "nothing to run anywhere" —
    # the default `client` fixture's `on.test.run` is a real subprocess
    # command, which the repo is now allowed to fall back on.
    no_fallback_templates_dir = fake_templates_dir(tmp_path, "claude", noop_verify=True)
    with _client(
        tmp_path, monkeypatch, templates_dir=no_fallback_templates_dir, default_setup=False
    ) as client:
        repo = make_repo(tmp_path)  # sample_repo has no pyproject.toml/package.json marker
        r = client.post("/api/repos", json={"path": str(repo), "enabled": True})
        assert r.status_code == 422, r.text
        assert "test command" in r.json()["detail"]


def test_add_repo_enabled_with_no_repo_test_command_is_refused_despite_the_registry(
    tmp_path, client
):
    """Kraft-vd1ed: the default `client` registry binds `on.test.run` to a real
    command, and the guard used to accept that. V1 verification never reads
    the registry -- it stops every item on a repo that declares neither
    `test_scopes` nor `test_command` -- so enabling one is refused."""
    repo = make_repo(tmp_path)
    r = client.post("/api/repos", json={"path": str(repo), "enabled": True})
    assert r.status_code == 422, r.text
    assert "on.test.run" not in r.json()["detail"], "V1 does not read on.test.run"


def test_add_repo_disabled_with_no_test_command_is_allowed(tmp_path, client):
    repo = make_repo(tmp_path)
    r = client.post("/api/repos", json={"path": str(repo), "enabled": False})
    assert r.status_code == 201, r.text


def test_patch_repo_cannot_enable_without_a_test_command(tmp_path, monkeypatch):
    no_fallback_templates_dir = fake_templates_dir(tmp_path, "claude", noop_verify=True)
    with _client(
        tmp_path, monkeypatch, templates_dir=no_fallback_templates_dir, default_setup=False
    ) as client:
        repo = make_repo(tmp_path)
        path = client.post("/api/repos", json={"path": str(repo), "enabled": False}).json()["path"]
        r = client.patch(f"/api/repos?path={path}", json={"enabled": True})
        assert r.status_code == 422, r.text


def test_patch_repo_cannot_enable_relying_on_the_registrys_test_command(tmp_path, client):
    """Kraft-vd1ed: same as above, through PATCH."""
    repo = make_repo(tmp_path)
    path = client.post("/api/repos", json={"path": str(repo), "enabled": False}).json()["path"]
    r = client.patch(f"/api/repos?path={path}", json={"enabled": True})
    assert r.status_code == 422, r.text


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
    templates_dir = Path(__file__).resolve().parents[2] / "templates"
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
