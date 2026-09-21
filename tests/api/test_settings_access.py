"""Theme, and access control (design 5e): bind, password, the bind used at startup."""

from __future__ import annotations

import pytest
import yaml

from kraft import config

#: No default repo entry for an unconnected repo (`support.api._client`): these read real config.
pytestmark = pytest.mark.api_client(default_setup=False)


def test_get_theme_defaults_to_nocturne_dark(client):
    body = client.get("/api/theme").json()
    assert body["palette"] == "nocturne"
    assert body["mode"] == "dark"
    assert body["density"] == "compact"
    assert body["board"] == {"group_by": "status", "show_done": 5, "open_in": "peek"}


def test_put_theme_round_trips_through_the_yaml(client, templates_dir):
    body = {
        "palette": "forest",
        "mode": "light",
        "density": "comfortable",
        "board": {"group_by": "repo", "show_done": 10, "open_in": "full"},
    }
    assert client.put("/api/theme", json=body).status_code == 200
    assert client.get("/api/theme").json() == body
    assert yaml.safe_load((templates_dir / "theme.yaml").read_text()) == body


def test_put_theme_rejects_an_unknown_palette(client):
    resp = client.put("/api/theme", json={"palette": "cerulean", "mode": "dark"})
    assert resp.status_code == 422
    assert client.get("/api/theme").json()["palette"] == "nocturne"


def test_put_theme_rejects_an_unknown_mode(client):
    resp = client.put("/api/theme", json={"palette": "nocturne", "mode": "twilight"})
    assert resp.status_code == 422


def test_put_theme_defaults_density_and_board_when_omitted(client):
    resp = client.put("/api/theme", json={"palette": "nocturne", "mode": "dark"})
    assert resp.json()["density"] == "compact"
    assert resp.json()["board"] == {"group_by": "status", "show_done": 5, "open_in": "peek"}


def test_get_theme_fills_defaults_for_a_pre_existing_file(client, templates_dir):
    # An operator's theme.yaml from before this change — no density/board keys.
    (templates_dir / "theme.yaml").write_text("palette: rose\nmode: light\n")
    body = client.get("/api/theme").json()
    assert body == {
        "palette": "rose",
        "mode": "light",
        "density": "compact",
        "board": {"group_by": "status", "show_done": 5, "open_in": "peek"},
    }


def test_put_theme_rejects_an_unknown_group_by(client):
    resp = client.put(
        "/api/theme",
        json={
            "palette": "nocturne",
            "mode": "dark",
            "board": {"group_by": "priority", "show_done": 5, "open_in": "peek"},
        },
    )
    assert resp.status_code == 422


def test_changing_theme_does_not_report_health_as_degraded(client):
    # theme.yaml has no 'id' key -- load_templates would read it as a broken
    # chain template unless it's in CONFIG_FILES (Kraft-w1ps).
    client.put("/api/theme", json={"palette": "forest", "mode": "light"})
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["invalid_templates"] == {}


def test_localhost_needs_no_password_and_says_so(client):
    body = client.get("/api/access").json()
    assert body["bind"] == "127.0.0.1"
    assert body["auth_required"] is False and body["password_set"] is False
    assert client.get("/api/work-items").status_code == 200


def test_binding_off_localhost_without_a_password_is_refused(client):
    r = client.put("/api/access", json={"bind": "0.0.0.0"})
    assert r.status_code == 422
    assert "password" in r.json()["detail"]
    assert client.get("/api/access").json()["bind"] == "127.0.0.1"


@pytest.mark.api_client(host="0.0.0.0")
def test_a_lan_bind_locks_the_api_until_a_login(client):
    enabled = client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
    assert enabled.status_code == 200
    assert enabled.json()["auth_required"] is True

    client.cookies.clear()
    # /access is itself behind the wall it just raised
    assert client.get("/api/access").status_code == 401
    assert client.get("/api/work-items").status_code == 401
    # health stays reachable so a monitor does not need a session
    assert client.get("/api/health").status_code == 200

    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
    assert client.get("/api/work-items").status_code == 200

    sessions = client.get("/api/sessions").json()["sessions"]
    assert len(sessions) == 1 and sessions[0]["current"] is True
    # the cookie's own value is never stored, only its hash
    cookie = client.cookies["kraft_session"]
    assert sessions[0]["id"] != cookie

    assert client.delete("/api/sessions/nope").status_code == 404
    # revoking your own session logs you straight back out
    assert client.delete(f"/api/sessions/{sessions[0]['id']}").status_code == 204
    assert client.get("/api/work-items").status_code == 401


@pytest.mark.api_client(host="0.0.0.0")
def test_changing_the_password_revokes_every_session(client):
    client.put("/api/access", json={"bind": "0.0.0.0", "password": "first"})
    client.post("/api/login", json={"password": "first"})
    assert client.get("/api/work-items").status_code == 200

    client.put("/api/access", json={"password": "second"})
    assert client.get("/api/work-items").status_code == 401
    assert client.post("/api/login", json={"password": "first"}).status_code == 401
    assert client.post("/api/login", json={"password": "second"}).status_code == 200


def test_the_password_is_stored_only_as_a_scrypt_hash(client, templates_dir):
    client.put("/api/access", json={"password": "hunter2"})
    raw = (templates_dir / "access.yaml").read_text()
    assert "hunter2" not in raw
    assert yaml.safe_load(raw)["password_hash"].startswith("scrypt$")


def test_the_configured_bind_is_what_the_server_starts_on(tmp_path, monkeypatch, templates_dir):
    from kraft.cli.admin import _bind

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
    from kraft.cli.admin import _bind

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
