"""Settings endpoints (design 5a–5e): repos, templates, registry, policy, access.

Every write lands in the same versioned YAML an operator edits by hand, so the
assertions check the file as well as the response.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

from kraft import auth, config, events, store
from kraft import db as kdb
from kraft.paths import RunDirs

_FAKE_AGENT = Path(__file__).resolve().parents[0] / "support" / "fake_agent.py"


def _client(tmp_path, monkeypatch, templates_dir, *, host: str | None = None):
    """`host` is what the process binds — auth follows that, not access.yaml, so a
    test about the locked-down posture has to set it before the app starts."""
    if host:
        monkeypatch.setenv("KRAFT_HOST", host)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    return TestClient(api.app)


@pytest.fixture
def templates_dir(tmp_path):
    return fake_templates_dir(tmp_path, "claude")


@pytest.fixture
def client(tmp_path, monkeypatch, templates_dir):
    with _client(tmp_path, monkeypatch, templates_dir) as c:
        yield c


# ── repos (5a) ───────────────────────────────────────────────────────────────


def test_probe_reads_the_repo_without_touching_it(tmp_path, client):
    repo = make_repo(tmp_path)
    before = sorted(p.name for p in repo.iterdir())

    body = client.post("/repos/probe", json={"path": str(repo)}).json()
    assert body["path"] == str(repo.resolve())
    assert body["branch"]
    assert body["submodules"] == []
    assert sorted(p.name for p in repo.iterdir()) == before

    assert client.post("/repos/probe", json={"path": str(tmp_path / "nope")}).status_code == 400
    plain = tmp_path / "plain"
    plain.mkdir()
    assert client.post("/repos/probe", json={"path": str(plain)}).status_code == 400


def test_probe_finds_submodules_and_a_test_command(tmp_path, client):
    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    (repo / ".gitmodules").write_text(
        '[submodule "libs/a"]\n\tpath = libs/a\n\turl = ../a.git\n'
        '[submodule "libs/b"]\n\tpath = libs/b\n\turl = ../b.git\n'
    )
    body = client.post("/repos/probe", json={"path": str(repo)}).json()
    assert body["submodules"] == ["libs/a", "libs/b"]
    assert body["test_command"] == "uv run pytest -q"


def test_repo_crud_round_trips_through_the_yaml(tmp_path, client, templates_dir):
    repo = make_repo(tmp_path)
    created = client.post("/repos", json={"path": str(repo), "default_chain_template": "default"})
    assert created.status_code == 201
    path = created.json()["path"]

    on_disk = yaml.safe_load((templates_dir / "repos.yaml").read_text())
    assert on_disk["repos"][0]["path"] == path
    assert client.get("/repos").json()["repos"][0]["default_chain_template"] == "default"

    # connecting the same repo twice is a conflict, not a duplicate row
    assert client.post("/repos", json={"path": str(repo)}).status_code == 409

    patched = client.patch(f"/repos?path={path}", json={"enabled": False, "name": "renamed"})
    assert patched.json()["enabled"] is False and patched.json()["name"] == "renamed"
    assert client.patch("/repos?path=/nope", json={"enabled": False}).status_code == 404

    # POST stores git's resolved toplevel, so the path a client connected with is
    # not always the path stored — patch and delete must still find it.
    assert client.patch(f"/repos?path={repo}", json={"enabled": True}).status_code == 200

    assert client.delete(f"/repos?path={path}").status_code == 204
    assert client.get("/repos").json()["repos"] == []
    assert client.delete(f"/repos?path={path}").status_code == 404


def test_add_repo_round_trips_default_model_and_steering(tmp_path, client, templates_dir):
    (templates_dir / "steering").mkdir()
    (templates_dir / "steering" / "house-style.md").write_text("# House style\nBe direct.\n")
    repo = make_repo(tmp_path)
    created = client.post(
        "/repos",
        json={
            "path": str(repo),
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

    fetched = client.get("/repos").json()["repos"][0]
    assert fetched["default_model"] == "anything-at-all"
    assert fetched["steering"] == ["house-style"]


def test_add_repo_with_a_missing_steering_name_is_refused(tmp_path, client, templates_dir):
    """Regression guard (task-3 fix round 1): the write side must validate
    before persisting, or a bad POST bricks every later GET /repos."""
    repos_yaml = templates_dir / "repos.yaml"
    assert not repos_yaml.exists()
    repo = make_repo(tmp_path)
    r = client.post("/repos", json={"path": str(repo), "steering": ["does-not-exist"]})
    assert 400 <= r.status_code < 500, r.text
    # a rejected write never got persisted
    assert not repos_yaml.exists()
    assert client.get("/repos").json()["repos"] == []


def test_patch_repo_with_a_missing_steering_name_is_refused(tmp_path, client, templates_dir):
    repo = make_repo(tmp_path)
    client.post("/repos", json={"path": str(repo)})
    before = (templates_dir / "repos.yaml").read_text()

    r = client.patch(f"/repos?path={repo}", json={"steering": ["does-not-exist"]})
    assert 400 <= r.status_code < 500, r.text
    assert (templates_dir / "repos.yaml").read_text() == before

    (entry,) = client.get("/repos").json()["repos"]
    assert entry["steering"] == []


def test_add_repo_stores_probed_forge(client, tmp_path):
    repo = make_repo(tmp_path, name="ghrepo")
    _set_origin(repo, "git@github.com:owner/repo.git")
    r = client.post("/repos", json={"path": str(repo)})
    assert r.status_code == 201
    assert r.json()["forge"] == "github"
    assert r.json()["project"] == "owner/repo"
    assert "gitlab_project" not in r.json()


def test_patch_repo_overrides_forge(client, tmp_path):
    repo = make_repo(tmp_path, name="patchrepo")
    client.post("/repos", json={"path": str(repo)})
    r = client.patch(f"/repos?path={repo}", json={"forge": "gitea", "project": "t/r"})
    assert r.status_code == 200
    (entry,) = [x for x in client.get("/repos").json()["repos"] if x["path"] == str(repo.resolve())]
    assert entry["forge"] == "gitea"
    assert entry["project"] == "t/r"


# ── templates (5b) ───────────────────────────────────────────────────────────


NODES = [
    {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
    {"id": "verify", "tasks": ["on.test.run"], "gate_after": None, "fix_loop": "verify_fix_loop"},
]


def test_template_put_validates_before_it_writes(client, templates_dir):
    bad = [{"id": "x", "tasks": ["on.does.not.exist"], "gate_after": None}]
    r = client.post("/templates/scratch/validate", json={"nodes": bad})
    assert r.json()["valid"] is False
    assert "not in the registry" in r.json()["error"]

    assert client.put("/templates/scratch", json={"nodes": bad}).status_code == 422
    assert not (templates_dir / "scratch.yaml").exists()

    assert client.put("/templates/scratch", json={"nodes": NODES}).status_code == 200
    assert yaml.safe_load((templates_dir / "scratch.yaml").read_text())["id"] == "scratch"
    # the new template is live without a restart
    assert any(t["id"] == "scratch" for t in client.get("/templates").json())
    assert client.get("/templates/scratch").json()["nodes"] == NODES
    assert client.get("/templates/nope").status_code == 404


def test_an_unknown_gate_is_refused(client):
    nodes = [{"id": "a", "tasks": ["on.test.run"], "gate_after": "made_up_gate"}]
    r = client.post("/templates/scratch/validate", json={"nodes": nodes})
    assert r.json()["valid"] is False and "gate_after" in r.json()["error"]


# ── registry (5c) ────────────────────────────────────────────────────────────


def test_registry_save_reruns_the_chain_validator(client, templates_dir):
    hooks = client.get("/registry").json()["hooks"]
    assert "on.test.run" in hooks

    broken = {k: v for k, v in hooks.items() if k != "on.test.run"}
    body = client.put("/registry", json={"hooks": broken})
    assert body.status_code == 200
    # quick-task measures with on.test.run, so dropping the binding breaks it
    assert "quick-task" in body.json()["invalid_templates"]
    assert client.get("/health").json()["status"] == "degraded"

    assert client.put("/registry", json={"hooks": {"on.x": {"kind": "nope"}}}).status_code == 422
    # the refused save left the file alone
    assert "on.x" not in yaml.safe_load((templates_dir / "registry.yaml").read_text())["hooks"]


def test_registry_carries_the_interactive_flag(client):
    hooks = client.get("/registry").json()["hooks"]
    hooks["on.implementation.start"]["interactive"] = True
    assert client.put("/registry", json={"hooks": hooks}).status_code == 200
    assert client.get("/registry").json()["hooks"]["on.implementation.start"]["interactive"] is True


def test_put_registry_validates_steering_against_the_real_templates_dir(client, templates_dir):
    """Regression guard: `put_registry` validates the candidate against
    `st.templates_dir / "steering"`, not the empty scratch dir it writes the
    candidate registry into — that would 422 every save naming a real file."""
    (templates_dir / "steering").mkdir()
    (templates_dir / "steering" / "house-style.md").write_text("# House style\nBe direct.\n")
    hooks = client.get("/registry").json()["hooks"]
    hooks["on.implementation.start"]["steering"] = ["house-style"]
    r = client.put("/registry", json={"hooks": hooks})
    assert r.status_code == 200, r.text
    assert client.get("/registry").json()["hooks"]["on.implementation.start"]["steering"] == [
        "house-style"
    ]


def test_get_put_registry_round_trip_is_byte_identical(client, templates_dir):
    before = (templates_dir / "registry.yaml").read_text()
    hooks = client.get("/registry").json()["hooks"]
    assert client.put("/registry", json={"hooks": hooks}).status_code == 200
    assert (templates_dir / "registry.yaml").read_text() == before


# ── policy (5d) ──────────────────────────────────────────────────────────────


def test_policy_put_rejects_a_cap_that_would_not_load(client, templates_dir):
    good = {
        "loops": {"verify_fix_loop": {"attempts": 5, "wall_clock_s": 60}},
        "default": {"attempts": 3, "wall_clock_s": 3600},
    }
    assert client.put("/policy", json=good).status_code == 200
    assert client.get("/policy").json()["loops"]["verify_fix_loop"]["attempts"] == 5
    assert yaml.safe_load((templates_dir / "policy.yaml").read_text()) == good

    bad = {"loops": {}, "default": {"attempts": 0, "wall_clock_s": 1}}
    assert client.put("/policy", json=bad).status_code == 422
    assert client.get("/policy").json()["default"]["attempts"] == 3


def test_saving_the_policy_preserves_the_findings_block(client, templates_dir):
    """A save from the policy screen must not silently erase a block it does not edit."""
    (templates_dir / "policy.yaml").write_text(
        "loops:\n  verify_fix_loop: { attempts: 3, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n"
        "findings:\n  loop_severities: [critical]\n"
    )
    body = client.get("/policy").json()
    assert body["findings"]["loop_severities"] == ["critical"]

    body["loops"]["verify_fix_loop"]["attempts"] = 5
    assert client.put("/policy", json=body).status_code == 200

    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert on_disk["findings"]["loop_severities"] == ["critical"]
    assert on_disk["loops"]["verify_fix_loop"]["attempts"] == 5


# ── access + auth (5e, 1m) ───────────────────────────────────────────────────


def test_localhost_needs_no_password_and_says_so(client):
    body = client.get("/access").json()
    assert body["bind"] == "127.0.0.1"
    assert body["auth_required"] is False and body["password_set"] is False
    assert client.get("/work-items").status_code == 200


def test_binding_off_localhost_without_a_password_is_refused(client):
    r = client.put("/access", json={"bind": "0.0.0.0"})
    assert r.status_code == 422
    assert "password" in r.json()["detail"]
    assert client.get("/access").json()["bind"] == "127.0.0.1"


def test_a_lan_bind_locks_the_api_until_a_login(tmp_path, monkeypatch, templates_dir):
    with _client(tmp_path, monkeypatch, templates_dir, host="0.0.0.0") as client:
        enabled = client.put("/access", json={"bind": "0.0.0.0", "password": "hunter2"})
        assert enabled.status_code == 200
        assert enabled.json()["auth_required"] is True

        client.cookies.clear()
        # /access is itself behind the wall it just raised
        assert client.get("/access").status_code == 401
        assert client.get("/work-items").status_code == 401
        # health stays reachable so a monitor does not need a session
        assert client.get("/health").status_code == 200

        assert client.post("/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/login", json={"password": "hunter2"}).status_code == 200
        assert client.get("/work-items").status_code == 200

        sessions = client.get("/sessions").json()["sessions"]
        assert len(sessions) == 1 and sessions[0]["current"] is True
        # the cookie's own value is never stored, only its hash
        cookie = client.cookies["kraft_session"]
        assert sessions[0]["id"] != cookie

        assert client.delete("/sessions/nope").status_code == 404
        # revoking your own session logs you straight back out
        assert client.delete(f"/sessions/{sessions[0]['id']}").status_code == 204
        assert client.get("/work-items").status_code == 401


def test_changing_the_password_revokes_every_session(tmp_path, monkeypatch, templates_dir):
    with _client(tmp_path, monkeypatch, templates_dir, host="0.0.0.0") as client:
        client.put("/access", json={"bind": "0.0.0.0", "password": "first"})
        client.post("/login", json={"password": "first"})
        assert client.get("/work-items").status_code == 200

        client.put("/access", json={"password": "second"})
        assert client.get("/work-items").status_code == 401
        assert client.post("/login", json={"password": "first"}).status_code == 401
        assert client.post("/login", json={"password": "second"}).status_code == 200


def test_the_password_is_stored_only_as_a_scrypt_hash(client, templates_dir):
    client.put("/access", json={"password": "hunter2"})
    raw = (templates_dir / "access.yaml").read_text()
    assert "hunter2" not in raw
    assert yaml.safe_load(raw)["password_hash"].startswith("scrypt$")


def test_the_configured_bind_is_what_the_server_starts_on(tmp_path, monkeypatch, templates_dir):
    from kraft.cli import _bind

    monkeypatch.delenv("KRAFT_PORT", raising=False)
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    config.save_access(
        templates_dir / "access.yaml",
        {"bind": "0.0.0.0", "port": 9100, "password_hash": "scrypt$aa$bb"},
    )
    assert _bind(templates_dir) == ("0.0.0.0", 9100)
    monkeypatch.setenv("KRAFT_PORT", "1234")
    assert _bind(templates_dir) == ("0.0.0.0", 1234)


def test_an_unprotected_lan_bind_refuses_to_start(tmp_path, monkeypatch, templates_dir):
    """The dangerous configuration is a non-loopback bind with no password. The
    API keeps working without one so the first password *can* be set; the process
    refuses to come up on the wire in that state."""
    from kraft.cli import _bind

    monkeypatch.delenv("KRAFT_PORT", raising=False)
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    config.save_access(templates_dir / "access.yaml", {"bind": "0.0.0.0", "port": 8765})
    with pytest.raises(SystemExit, match="no password is set"):
        _bind(templates_dir)


def test_an_atomic_write_leaves_no_half_file_behind(tmp_path):
    target = tmp_path / "policy.yaml"
    config.write_yaml(target, {"default": {"attempts": 3}})
    assert yaml.safe_load(target.read_text()) == {"default": {"attempts": 3}}
    assert [p.name for p in tmp_path.iterdir()] == ["policy.yaml"]


def test_a_broken_config_file_raises_rather_than_reading_as_empty(tmp_path):
    bad = tmp_path / "repos.yaml"
    bad.write_text("repos: [not-a-mapping]")
    with pytest.raises(config.ConfigError):
        config.load_repos(bad)
    bad.write_text("{{{")
    with pytest.raises(config.ConfigError):
        config.load_repos(bad)
    assert config.load_repos(tmp_path / "missing.yaml") == []


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


def test_the_spa_bundle_loads_before_a_session_exists(tmp_path, monkeypatch, templates_dir):
    """The login page cannot render if its own JS comes back 401."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("KRAFT_HOST", "0.0.0.0")
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(dist))
    import kraft.api as api

    with TestClient(api.app) as client:
        client.put("/access", json={"bind": "0.0.0.0", "password": "hunter2"})
        client.cookies.clear()
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/work-items").status_code == 401
        # ...but not anything outside the bundle
        assert client.get("/../pyproject.toml").status_code != 200


def test_the_event_stream_needs_a_session_too(tmp_path, monkeypatch, templates_dir):
    """HTTP middleware does not run for websockets; the check has to be in the
    endpoint or a LAN bind leaves the live stream wide open."""
    from starlette.websockets import WebSocketDisconnect

    with _client(tmp_path, monkeypatch, templates_dir, host="0.0.0.0") as client:
        client.put("/access", json={"bind": "0.0.0.0", "password": "hunter2"})
        client.cookies.clear()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/events") as ws:
                ws.receive_text()

        client.post("/login", json={"password": "hunter2"})
        # a fresh item's events arrive on the stream once the session is real
        with client.websocket_connect("/ws/events") as ws:
            client.post("/work-items", json={"title": "hello", "repo": str(tmp_path)})
            assert ws.receive_json()["type"] == "work_item_created"


def test_a_forged_navigation_header_cannot_write(tmp_path, monkeypatch, templates_dir):
    """`sec-fetch-dest` is a request header any client can send. A browser
    navigation must get the SPA shell; it must never be a way past the session
    check into a real handler."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(dist))
    monkeypatch.setenv("KRAFT_HOST", "0.0.0.0")
    import kraft.api as api

    with TestClient(api.app) as client:
        client.put("/access", json={"bind": "0.0.0.0", "password": "hunter2"})
        client.cookies.clear()
        forged = {"sec-fetch-dest": "document"}

        # a POST is never a navigation
        assert client.post("/work-items/x/pause", json={}, headers=forged).status_code == 401
        assert client.put("/access", json={"bind": "0.0.0.0"}, headers=forged).status_code == 401
        assert client.delete("/sessions/abc", headers=forged).status_code == 401

        # a GET navigation gets the shell, not JSON from a handler
        shell = client.get("/work-items", headers=forged)
        assert shell.status_code == 200
        assert shell.text.startswith("<!doctype html>")


def test_a_bind_change_does_not_lock_out_a_server_still_on_loopback(
    tmp_path, monkeypatch, templates_dir
):
    """The bind takes effect on restart, so auth has to follow what the process
    actually bound — otherwise saving the setting logs the local operator out of
    a server that is still only listening on 127.0.0.1."""
    with _client(tmp_path, monkeypatch, templates_dir) as client:
        client.put("/access", json={"bind": "0.0.0.0", "password": "hunter2"})
        client.cookies.clear()
        assert client.get("/work-items").status_code == 200
        assert client.get("/access").json()["auth_required"] is False


def test_a_failed_write_does_not_sign_everyone_out(tmp_path, monkeypatch, templates_dir):
    """Revoking first and then failing to persist the new hash would sign every
    session out while leaving the old password live."""
    with _client(tmp_path, monkeypatch, templates_dir, host="0.0.0.0") as client:
        client.put("/access", json={"bind": "0.0.0.0", "password": "first"})
        client.post("/login", json={"password": "first"})
        before = [s["id"] for s in client.get("/sessions").json()["sessions"]]
        assert len(before) == 1

        monkeypatch.setattr(
            "kraft.api.config_mod.save_access",
            lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")),
        )
        with pytest.raises(OSError):
            client.put("/access", json={"password": "second"})
        # still signed in, still on the old password (last_seen_at moves; the
        # session itself is what must survive)
        assert [s["id"] for s in client.get("/sessions").json()["sessions"]] == before
        assert client.get("/work-items").status_code == 200


def test_a_bearer_token_authenticates_where_a_cookie_would(tmp_path, monkeypatch, templates_dir):
    """`kraft mcp` has no cookie jar. The token file is its credential (design §5)."""
    with _client(tmp_path, monkeypatch, templates_dir, host="0.0.0.0") as client:
        assert (
            client.put("/access", json={"bind": "0.0.0.0", "password": "hunter2"}).status_code
            == 200
        )
        client.cookies.clear()
        assert client.get("/work-items").status_code == 401

        token = auth.read_mcp_token(tmp_path / "run")
        assert token, "serving should have created the token file"
        assert (
            client.get("/work-items", headers={"Authorization": f"Bearer {token}"}).status_code
            == 200
        )
        assert (
            client.get("/work-items", headers={"Authorization": "Bearer wrong"}).status_code == 401
        )
        # a bare token without the scheme is not a credential
        assert client.get("/work-items", headers={"Authorization": token}).status_code == 401


def test_a_document_navigation_cannot_slip_past_the_bearer_check(
    tmp_path, monkeypatch, templates_dir
):
    """The SPA-shell branch runs first, so it must not become an auth bypass for
    JSON: with no dist configured there is no shell, and the request still 401s."""
    with _client(tmp_path, monkeypatch, templates_dir, host="0.0.0.0") as client:
        client.put("/access", json={"bind": "0.0.0.0", "password": "hunter2"})
        client.cookies.clear()
        r = client.get("/work-items", headers={"sec-fetch-dest": "document"})
        assert r.status_code == 401


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


# ── `_launch` degrades on a broken repos.yaml instead of crashing (review r1) ──

_GATED_CHAIN = {
    "nodes": [
        {
            "id": "review",
            "tasks": ["on.human_review.requested"],
            "gate_after": "human_review_approval",
        },
    ]
}


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

    with TestClient(api.app) as client:  # must not raise
        assert client.get("/health").status_code == 200


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
        r = client.post("/work-items/w-gated/gates/human_review_approval/approve")
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
        added = client.post("/repos", json={"path": str(repo), "default_model": "haiku"})
        assert added.status_code == 201

        wid = client.post("/work-items", json={"title": "x", "repo": str(repo)}).json()["id"]

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not argv_log.exists():
            time.sleep(0.2)
        assert argv_log.exists(), f"agent never launched for {wid}"

    argv = json.loads(argv_log.read_text().splitlines()[0])
    assert argv[-2:] == ["--model", "haiku"]


def test_connected_repos_steering_reaches_the_agent_launch(tmp_path, monkeypatch):
    """The `_launch` -> `resolve_invocation` seam for steering, not just
    `--model`: connect a repo with `steering: ["house"]` through the real API,
    post a work item, and check the house body reaches the agent's
    `--append-system-prompt`. Both executor launch tests pass `steering_dir=None`
    and never exercise this join; this is the sibling that does."""
    templates_dir = fake_templates_dir(tmp_path, f"{sys.executable} {_FAKE_AGENT}")
    (templates_dir / "steering").mkdir()
    (templates_dir / "steering" / "house.md").write_text("Prefer tabs over spaces.")
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    repo = make_repo(tmp_path)

    with _client(tmp_path, monkeypatch, templates_dir) as client:
        added = client.post("/repos", json={"path": str(repo), "steering": ["house"]})
        assert added.status_code == 201, added.text

        wid = client.post("/work-items", json={"title": "x", "repo": str(repo)}).json()["id"]

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
        got = client.get("/repos")
        assert got.status_code == 200
        assert got.json()["repos"][0]["steering"] == ["deleted"]

        patched = client.patch("/repos?path=/r", json={"steering": []})
        assert patched.status_code == 200
        assert patched.json()["steering"] == []
        assert client.get("/repos").json()["repos"][0]["steering"] == []
