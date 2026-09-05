"""Settings endpoints (design 5a–5e): repos, templates, registry, policy, access.

Every write lands in the same versioned YAML an operator edits by hand, so the
assertions check the file as well as the response.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

from kraft import auth, config


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


def test_probe_survives_a_repo_with_no_git_remote(tmp_path):
    repo = make_repo(tmp_path)
    subprocess.run(["git", "remote", "remove", "origin"], cwd=repo, capture_output=True)
    probed = config.probe_repo(repo)
    assert probed["gitlab_project"] is None
    assert Path(probed["path"]) == repo.resolve()


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
