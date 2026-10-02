"""GET /health and the login wall on POST /work-items/{id}/triggers."""

from __future__ import annotations

import time

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


def test_health_reports_how_long_the_process_has_been_up(client):
    """`uptime_s` counts from the app's start, in whole seconds, so About can say "up 3d 4h"."""
    client.app.state.started_at = time.monotonic() - 3725
    assert 3725 <= client.get("/api/health").json()["uptime_s"] < 3735


@pytest.mark.api_client(host="localhost", env={"KRAFT_PORT": "18772"})
def test_health_reports_the_bound_address_not_access_yaml(client):
    """`admin start --port`/`--host` reach the server as KRAFT_PORT/KRAFT_HOST;
    access.yaml still says 127.0.0.1:8765, and health must not."""
    h = client.get("/api/health").json()
    assert (h["bind"], h["port"]) == ("localhost", 18772)


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
        ("https://127.0.0.1/api/login", {}, True),
        ("http://127.0.0.1/api/login", {"x-forwarded-proto": "https"}, True),
        ("http://127.0.0.1/api/login", {}, False),
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


class _Clock:
    """`time.monotonic` for the login route, moved by hand."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture
def login_clock(client, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(routes_auth, "time", clock)
    return clock


def _fail_logins(client, n=routes_auth.LOGIN_MAX_FAILURES):
    for _ in range(n):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_a_lockout_ends_once_the_window_has_passed(client, monkeypatch, login_clock):
    _set_password(client, monkeypatch)
    _fail_logins(client)
    login_clock.now += routes_auth.LOGIN_WINDOW_S - 1
    resp = client.post("/api/login", json={"password": "hunter2"})
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "1"
    login_clock.now += 1
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_failures_older_than_the_window_do_not_count_toward_a_lockout(
    client, monkeypatch, login_clock
):
    _set_password(client, monkeypatch)
    _fail_logins(client, routes_auth.LOGIN_MAX_FAILURES - 1)
    login_clock.now += routes_auth.LOGIN_WINDOW_S
    _fail_logins(client, 1)
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_one_addresss_failures_do_not_lock_out_another(client, monkeypatch):
    """Keyed per peer address, so a stranger guessing at the password cannot
    lock the owner out from their own machine."""
    _set_password(client, monkeypatch)
    _fail_logins(client)
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 429
    # Starlette's test transport holds the peer address the app sees.
    monkeypatch.setattr(client._transport, "client", ("10.0.0.6", 54321))
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_logout_revokes_the_session_and_clears_its_cookie(client, monkeypatch):
    _set_password(client, monkeypatch)
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
    token = client.cookies[auth_mod.COOKIE]
    assert client.get("/api/work-items").status_code == 200

    resp = client.post("/api/logout")

    assert resp.status_code == 204
    cleared = resp.headers["set-cookie"]
    assert cleared.startswith(f'{auth_mod.COOKIE}=""') and "Max-Age=0" in cleared
    assert auth_mod.COOKIE not in client.cookies
    # The token is dead server-side, not only forgotten by this browser.
    client.cookies.set(auth_mod.COOKIE, token)
    assert client.get("/api/work-items").status_code == 401


def test_logout_without_a_session_cookie_revokes_nothing(client):
    """A loopback caller needs no session, so it can reach logout holding
    none; nothing is revoked and other devices stay signed in."""

    def phone(c):
        auth_mod.create_session(c, "a-phone", label="phone", ip="10.0.0.9", expiry_days=1)

    client.portal.call(client.app.state.db.write, phone)

    assert client.post("/api/logout").status_code == 204

    sessions = client.get("/api/sessions").json()["sessions"]
    assert [s["label"] for s in sessions] == ["phone"]
