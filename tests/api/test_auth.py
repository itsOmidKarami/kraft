"""GET /health and the login wall on POST /work-items/{id}/triggers."""

from __future__ import annotations

import pytest

from kraft import auth as auth_mod
from kraft.api.routes import auth as routes_auth


def _broken_library(tdir):
    (tdir / "library.yaml").write_text("tasks: [unclosed\n")


def _invalid_policy(tdir):
    (tdir / "policy.yaml").write_text("default: { attempts: 0, wall_clock_s: 1 }\n")


@pytest.mark.parametrize(
    ("key", "names"),
    [
        pytest.param(
            "invalid_templates",
            "library.yaml",
            marks=pytest.mark.api_client(edit_templates=_broken_library),
            id="an-unreadable-library",
        ),
        pytest.param(
            "invalid_policy",
            None,
            marks=pytest.mark.api_client(edit_templates=_invalid_policy),
            id="an-invalid-policy",
        ),
    ],
)
def test_health_is_degraded_by(client, key, names):
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert body[key]
    if names:
        assert names in body[key]
    assert "reattach_summary" in body


def test_health_ok_with_valid_policy(client):
    h = client.get("/api/health").json()
    assert h["invalid_policy"] == []


def test_health_reports_port_and_version(client):
    h = client.get("/api/health").json()
    assert isinstance(h["port"], int)
    assert isinstance(h["version"], str) and h["version"]


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_login_rejects_with_the_new_copy(client, monkeypatch):
    st = client.app.state
    monkeypatch.setattr(
        st, "access", {**st.access, "password_hash": auth_mod.hash_password("hunter2")}
    )
    resp = client.post("/api/login", json={"password": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Wrong password."


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_stay_signed_in_false_sets_a_session_cookie(client, monkeypatch):
    st = client.app.state
    monkeypatch.setattr(
        st, "access", {**st.access, "password_hash": auth_mod.hash_password("hunter2")}
    )
    resp = client.post("/api/login", json={"password": "hunter2", "stay_signed_in": False})
    set_cookie = resp.headers["set-cookie"]
    assert "Max-Age" not in set_cookie


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_stay_signed_in_true_sets_a_persistent_cookie(client, monkeypatch):
    st = client.app.state
    monkeypatch.setattr(
        st, "access", {**st.access, "password_hash": auth_mod.hash_password("hunter2")}
    )
    resp = client.post("/api/login", json={"password": "hunter2", "stay_signed_in": True})
    assert "Max-Age" in resp.headers["set-cookie"]


def _set_password(client, monkeypatch):
    st = client.app.state
    monkeypatch.setattr(
        st, "access", {**st.access, "password_hash": auth_mod.hash_password("hunter2")}
    )


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_the_fifth_failed_login_locks_the_address_out(client, monkeypatch):
    _set_password(client, monkeypatch)
    for _ in range(routes_auth.LOGIN_MAX_FAILURES):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    # locked out: even the right password is refused until the window passes
    resp = client.post("/api/login", json={"password": "hunter2"})
    assert resp.status_code == 429
    assert 0 < int(resp.headers["retry-after"]) <= routes_auth.LOGIN_WINDOW_S


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_a_successful_login_clears_the_failure_count(client, monkeypatch):
    _set_password(client, monkeypatch)
    for _ in range(2):
        for _ in range(routes_auth.LOGIN_MAX_FAILURES - 1):
            assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200


@pytest.mark.parametrize(
    ("url", "headers", "secure"),
    [
        ("https://testserver/api/login", {}, True),
        ("http://testserver/api/login", {"x-forwarded-proto": "https"}, True),
        ("http://testserver/api/login", {}, False),
    ],
    ids=["https", "tunnel-forwarded-https", "plain-http"],
)
@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_the_session_cookie_is_secure_only_over_https(client, monkeypatch, url, headers, secure):
    _set_password(client, monkeypatch)
    resp = client.post(url, json={"password": "hunter2"}, headers=headers)
    assert resp.status_code == 200
    assert ("; secure" in resp.headers["set-cookie"].lower()) is secure


def test_health_carries_session_expiry_days(client):
    assert "session_expiry_days" in client.get("/api/health").json()


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_post_triggers_requires_auth(client, repo, monkeypatch):
    """The route takes the same `_authenticate` middleware as everything else:
    a loopback peer is unaffected (design 5e), so this needs a remote one to
    actually exercise the gate."""
    st = client.app.state
    monkeypatch.setattr(st, "access", {**st.access, "password_hash": "x"}, raising=False)
    r = client.post("/api/triggers", json={"repo": str(repo), "title": "t"})
    assert r.status_code == 401
