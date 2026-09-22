"""GET /health and the login wall on POST /work-items/{id}/triggers."""

from __future__ import annotations

import pytest

from kraft import auth as auth_mod


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
