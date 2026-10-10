"""GET /health and the login wall on POST /work-items/{id}/triggers."""

from __future__ import annotations

import time

import pytest
from support.api import _hold_storage

from kraft import auth as auth_mod
from kraft.api.routes import auth as routes_auth


def _broken_library(tdir):
    (tdir / "library.yaml").write_text("tasks: [unclosed\n")


def _invalid_policy(tdir):
    (tdir / "policy.yaml").write_text("default: { attempts: 0, wall_clock_s: 1 }\n")


def _invalid_intake(tdir):
    (tdir / "intake.yaml").write_text("interval_s: abc\n")


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
        pytest.param(
            "invalid_intake",
            "intake.yaml",
            marks=pytest.mark.api_client(edit_templates=_invalid_intake),
            id="an-unreadable-intake-yaml",
        ),
    ],
)
def test_health_is_degraded_by(client, key, names):
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert body[key]
    assert body["intake_off"] is (key == "invalid_intake")  # started on it: off
    if names:
        assert names in body[key]
    assert "reattach_summary" in body


def _session(wid: str, sid: str | None) -> None:
    """A session `sid` of item `wid`; `s-ended` is one startup could not
    confirm, which stops the item. None completes the item instead."""
    import os
    import sqlite3
    from pathlib import Path

    from kraft import store

    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("SELECT 1 FROM work_items WHERE id = ?", (wid,)).fetchone() is None:
            store.create_work_item(
                conn,
                id=wid,
                bead_id=None,
                title="t",
                repo="/r",
                chain_template="t",
                chain_definition="{}",
                status="active",
            )
        if sid is None:
            conn.execute("UPDATE work_items SET status = 'completed' WHERE id = ?", (wid,))
        else:
            store.create_session(
                conn,
                id=sid,
                work_item_id=wid,
                node_id="implementation",
                hook_point="implementation.main.implement",
                log_path="/l",
                result_path="/r",
            )
        if sid == "s-ended":
            store.mark_needs_human(conn, wid, "implementation", "reattach", kind="infra")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("then", "orphaned"),
    [(None, ["s-ended"]), ("s-retried", []), ("completed", [])],
    ids=["still-stopped", "retried-since", "completed"],
)
def test_health_names_an_orphaned_session_only_while_its_item_stands_on_it(client, then, orphaned):
    """`kraft admin doctor` failed on "orphaned agent sessions" until the
    next restart, though the documented retry had run the item to the end:
    `/health` read the list startup made, never what became of it."""
    from kraft.worker.reattach import ReattachSummary

    _session("w-o", "s-ended")
    if then == "completed":
        _session("w-o", None)
    elif then:
        _session("w-o", then)
    client.app.state.reattach_summary = ReattachSummary(scanned=1, unknown=["s-ended"])

    assert client.get("/api/health").json()["reattach_summary"]["unknown"] == orphaned


def test_health_ok_with_valid_policy(client):
    h = client.get("/api/health").json()
    assert h["invalid_policy"] == []


@pytest.mark.parametrize(
    ("used", "state"),
    [(80, "ok"), (90, "over_quota"), (101, "held")],
    ids=["ok", "over-quota", "held"],
)
def test_health_reports_storage_and_degrades_only_when_held(client, used, state):
    _hold_storage(client, used=used, limit=100)

    health = client.get("/api/health").json()

    assert health["storage"]["state"] == state
    assert (health["storage"]["used_bytes"], health["storage"]["limit_bytes"]) == (used, 100)
    assert health["storage"]["quota_bytes"] == 80
    assert (health["status"] == "degraded") == (state == "held")


def test_health_storage_is_null_without_a_limit(client):
    assert client.get("/api/health").json()["storage"] is None


def test_health_reports_port_and_version(client):
    h = client.get("/api/health").json()
    assert isinstance(h["port"], int)
    assert isinstance(h["version"], str) and h["version"]


def test_health_reports_the_running_version_after_an_update_on_disk(client, monkeypatch):
    """`kraft admin update` without `--restart` replaces the package under a
    running server. `version` is what this process loaded, `installed` what a
    restart would run, and the two differ until that restart."""
    from kraft import update

    running = client.get("/api/health").json()["version"]
    monkeypatch.setattr(update, "installed", lambda: "99.0.0")

    h = client.get("/api/health").json()

    assert (h["version"], h["installed"]) == (running, "99.0.0")
    assert running != "99.0.0"


def test_health_reports_how_long_the_process_has_been_up(client):
    """`uptime_s` counts from the app's start, in whole seconds, so About can say "up 3d 4h"."""
    client.app.state.started_at = time.monotonic() - 3725
    assert 3725 <= client.get("/api/health").json()["uptime_s"] < 3735


def test_health_does_not_name_the_python(client):
    """/api/health needs no login: the interpreter is About's, read behind it from /api/update."""
    assert "python" not in client.get("/api/health").json()


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
    """The auth routes' `time` module with `monotonic` moved by hand; every
    other name (`time.time`, ...) is the real module's."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


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
