"""Kraft-dwt: who may reach this API at all — peer address, Host, and Origin."""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from kraft import auth


def _set_password(client, monkeypatch):
    """Turn the auth gate on without binding a LAN-visible socket for the test run.

    Same trade as tests/test_ws.py:_require_auth: the hash is never verified here,
    only its presence is, so a placeholder is enough and scrypt is not paid for.
    """
    st = client.app.state
    monkeypatch.setattr(st, "access", {**st.access, "password_hash": "x"}, raising=False)


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """A built SPA (`index.html` and one asset), pinned as `KRAFT_FRONTEND_DIST`.
    List it before `client`: the app reads it at startup."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(dist))
    return dist


_LAN = pytest.mark.api_client(peer=("10.0.0.5", 54321))


def test_client_is_local_reads_the_peer_address():
    """Fails closed: anything that is not a parseable loopback IP is remote."""
    from kraft.api.perimeter import _client_is_local

    def peer(host):
        addr = None if host is None else type("Addr", (), {"host": host})()
        return type("Req", (), {"client": addr})

    assert _client_is_local(peer("127.0.0.1"))
    assert _client_is_local(peer("127.0.0.2"))  # all of 127/8 is loopback
    assert _client_is_local(peer("::1"))
    assert not _client_is_local(peer("10.0.0.5"))
    assert not _client_is_local(peer("testclient"))  # starlette's default peer
    assert not _client_is_local(peer(None))  # no peer at all


def test_a_loopback_client_needs_no_password(client):
    """The default instance is unchanged: the operator at the keyboard logs into nothing."""
    assert client.get("/api/work-items").status_code == 200
    assert client.get("/api/access").json()["auth_required"] is False


@pytest.mark.parametrize(
    "status",
    [200, pytest.param(401, marks=_LAN)],
    ids=["the-local-operator-is-unaffected", "a-lan-peer-must-log-in"],
)
def test_a_non_loopback_client_must_log_in_when_a_password_is_set(client, monkeypatch, status):
    """`bound_host` is a claim about the bind; the peer address is the fact.

    `uvicorn kraft.api:app --host 0.0.0.0` never updates access.yaml, so a server
    that still believes it is on loopback must not wave a remote peer through.
    """
    _set_password(client, monkeypatch)
    assert client.app.state.bound_host == "127.0.0.1"
    r = client.get("/api/work-items")
    assert r.status_code == status, r.text
    if status == 401:
        assert r.json()["detail"] == "authentication required"


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_a_non_loopback_client_is_refused_when_no_password_is_set(client):
    """The fail-closed half: access.yaml says 127.0.0.1 and a LAN peer arrived
    anyway, so the bind was widened by something that never told the app. With no
    password there is nothing to authenticate against — refuse rather than serve."""
    r = client.get("/api/work-items")
    assert r.status_code == 403, r.text
    assert "loopback bind" in r.json()["detail"]
    assert "10.0.0.5" in r.json()["detail"]
    # the public paths are not an exception: there is nothing here to log into
    assert client.post("/api/login", json={"password": "x"}).status_code == 403
    assert client.get("/api/health").status_code == 403


def test_a_cross_site_origin_cannot_pause_a_work_item(client, repo):
    """A bodiless POST needs no preflight, so any page the operator visits could
    reach the mutating routes that take no JSON body. On the default loopback
    instance auth is off, so there is no cookie to withhold either."""
    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "hold still", "autostart": False},
    ).json()["id"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "paused"

    r = client.post(f"/api/work-items/{wid}/pause", headers={"origin": "http://evil.com"})
    assert r.status_code == 403, r.text
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "paused"

    # and a GET is not a write: reads are not the thing this rule is about
    assert client.get("/api/work-items", headers={"origin": "http://evil.com"}).status_code == 200


def test_the_dev_server_origin_is_accepted(client):
    """`just dev`: vite rewrites Host to 127.0.0.1:8765 (changeOrigin) and passes
    the browser's own Origin through, so the pair never matches — the loopback
    hostname is what makes it legal."""
    r = client.post(
        "/api/index/rescan",
        headers={"origin": "http://localhost:5173", "host": "127.0.0.1:8765"},
    )
    assert r.status_code == 200, r.text


@pytest.mark.api_client(host="0.0.0.0")
def test_a_lan_instance_accepts_its_own_origin_and_host(client):
    """A board served on the LAN must not be locked out of itself. Neither the
    Origin nor the Host is loopback; they match each other, and the bind is not
    loopback, so the rebinding rule has nothing to say about it either."""
    saved = client.put(
        "/api/access",
        json={"bind": "0.0.0.0", "password": "hunter2", "allowed_hosts": ["192.168.1.5"]},
    )
    assert saved.status_code == 200, saved.text
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
    r = client.post(
        "/api/index/rescan",
        headers={
            "origin": "http://192.168.1.5:8765",
            "host": "192.168.1.5:8765",
            "sec-fetch-site": "same-origin",
        },
    )
    assert r.status_code == 200, r.text


@pytest.mark.api_client(host="0.0.0.0")
def test_a_non_loopback_bind_refuses_an_unlisted_host(client):
    """cdy: a `0.0.0.0` bind gets no rebinding protection today -- this is the
    missing half of test_a_rebound_host_is_refused_for_a_browser_request."""
    saved = client.put(
        "/api/access",
        json={"bind": "0.0.0.0", "password": "hunter2", "allowed_hosts": ["kraft.example.com"]},
    )
    assert saved.status_code == 200, saved.text
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
    browser = {"host": "evil.example.com:8765", "sec-fetch-site": "same-origin"}
    assert client.get("/api/work-items", headers=browser).status_code == 403


@pytest.mark.api_client(host="0.0.0.0")
def test_a_non_loopback_bind_accepts_a_listed_host(client):
    saved = client.put(
        "/api/access",
        json={"bind": "0.0.0.0", "password": "hunter2", "allowed_hosts": ["kraft.example.com"]},
    )
    assert saved.status_code == 200, saved.text
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
    browser = {"host": "kraft.example.com:8765", "sec-fetch-site": "same-origin"}
    assert client.get("/api/work-items", headers=browser).status_code == 200


@pytest.mark.api_client(host="0.0.0.0")
def test_a_non_loopback_bind_with_no_allowlist_refuses_every_browser_host(client):
    """Fails closed: no `allowed_hosts` configured is not an open gate."""
    saved = client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
    assert saved.status_code == 200, saved.text
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
    browser = {"host": "192.168.1.5:8765", "sec-fetch-site": "same-origin"}
    assert client.get("/api/work-items", headers=browser).status_code == 403


@pytest.mark.api_client(host="0.0.0.0")
def test_get_access_reports_allowed_hosts(client):
    client.put(
        "/api/access",
        json={"bind": "0.0.0.0", "password": "hunter2", "allowed_hosts": ["a.example.com"]},
    )
    client.post("/api/login", json={"password": "hunter2"})
    r = client.get("/api/access")
    assert r.status_code == 200, r.text
    assert r.json()["allowed_hosts"] == ["a.example.com"]


def test_a_rebound_host_is_refused_for_a_browser_request(client):
    """DNS rebinding: a page on evil.com whose name flips to 127.0.0.1 becomes
    same-origin with the local board and can read every response and drive every
    route. A server that bound loopback answers to loopback names only."""
    browser = {"host": "evil.com:8765", "sec-fetch-site": "same-origin"}
    assert client.get("/api/work-items", headers=browser).status_code == 403
    assert client.post("/api/index/rescan", headers=browser).status_code == 403

    # ...and a non-browser client keeps working. Only a browser can be
    # rebound, so curl, the CLI and MCP need no Host allowlist entry.
    assert client.get("/api/work-items", headers={"host": "kraft.internal"}).status_code == 200

    # this is also the ordering guarantee for /api/ itself: _perimeter has
    # to run before _authenticate/_spa_navigation even consider the
    # request, or a forged nav header on a rebound host could slip past it
    # the way test_the_perimeter_runs_before_the_spa_shell_middleware
    # checks for a client-side route.
    nav = {**browser, "sec-fetch-dest": "document"}
    assert client.get("/api/work-items", headers=nav).status_code == 403


def test_the_perimeter_runs_before_the_spa_shell_middleware(dist, client):
    """Starlette enters the last-added middleware first, so `_perimeter` has to be
    declared *below* `_authenticate` and `_spa_navigation`. Declared above, this
    rebound navigation gets the SPA shell instead of a 403.

    A client-side route, not an /api/ one: that prefix is excluded from the
    shell-diversion branches entirely now, so it would pass even with the
    middleware in the wrong order and prove nothing about ordering."""
    r = client.get(
        "/work-items",
        headers={
            "host": "evil.com:8765",
            "sec-fetch-site": "same-origin",
            "sec-fetch-dest": "document",
        },
    )
    assert r.status_code == 403, r.text
    assert not r.text.startswith("<!doctype html>")


@_LAN
def test_a_client_without_sec_fetch_dest_still_gets_the_spa_shell(dist, client, monkeypatch):
    """Kraft-qntj: some privacy browsers, in-app webviews and proxies omit or
    strip Sec-Fetch-Dest. That header was the only thing that let an
    unauthenticated GET / through to the shell instead of a raw 401 — so a
    client that never sends it could never reach the login page. Any non-/api
    GET is the same trust level as a static asset already; the bypass is
    keyed to method + path now, not to a header the client controls."""
    _set_password(client, monkeypatch)
    r = client.get("/")
    assert r.status_code == 200, r.text
    assert r.text.startswith("<!doctype html>")


@pytest.mark.parametrize(
    "headers",
    [{"origin": "http://evil.com"}, pytest.param({}, marks=_LAN)],
    ids=["a-cross-site-origin", "a-lan-peer-on-a-loopback-bind"],
)
def test_the_websocket_perimeter_stands_on_its_own(client, headers):
    """HTTP middleware does not run for websockets. `/ws/events` therefore keeps
    its own origin check, and needs the bind-mismatch rule restated — otherwise
    the live event stream is the one route the middleware did not close."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/ws/events", headers=headers):
            pass
    assert exc.value.code == 1008


@pytest.mark.api_client(host="0.0.0.0")
def test_the_spa_bundle_loads_before_a_session_exists(dist, client):
    """The login page cannot render if its own JS comes back 401."""
    client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
    client.cookies.clear()
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/work-items").status_code == 401
    # Kraft-qntj: any non-/api GET is the SPA shell now, same trust level as
    # a static asset — but traversal must still land on the shell, not on a
    # file outside dist. 200 here is the shell, not a leak.
    r = client.get("/../pyproject.toml")
    assert r.status_code == 200, r.text
    assert r.text == "<!doctype html>"


@pytest.mark.api_client(host="0.0.0.0")
def test_the_event_stream_needs_a_session_too(client, tmp_path):
    """HTTP middleware does not run for websockets; the check has to be in the
    endpoint or a LAN bind leaves the live stream wide open."""
    client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws/events") as ws:
            ws.receive_text()

    client.post("/api/login", json={"password": "hunter2"})
    # a fresh item's events arrive on the stream once the session is real
    with client.websocket_connect("/api/ws/events") as ws:
        client.post("/api/work-items", json={"title": "hello", "repo": str(tmp_path)})
        assert ws.receive_json()["type"] == "work_item_created"


@pytest.mark.api_client(host="0.0.0.0")
def test_a_forged_navigation_header_cannot_write(dist, client):
    """`sec-fetch-dest` is a request header any client can send. It must never be
    a way past the session check into a real handler — every route it could
    forge its way into lives under /api/, which the shell-diversion branches
    exclude outright, so a forged header there just hits the real 401."""
    client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
    client.cookies.clear()
    forged = {"sec-fetch-dest": "document"}

    # a POST is never a navigation
    assert client.post("/api/work-items/x/pause", json={}, headers=forged).status_code == 401
    assert client.put("/api/access", json={"bind": "0.0.0.0"}, headers=forged).status_code == 401
    assert client.delete("/api/sessions/abc", headers=forged).status_code == 401

    # a GET navigation gets the real 401, not a way in and not the shell —
    # /api/ is unambiguous JSON, forged header or not
    r = client.get("/api/work-items", headers=forged)
    assert r.status_code == 401
    assert not r.text.startswith("<!doctype html>")

    # the same header on a client-side route still gets the shell — that
    # part of the mechanism is unchanged, just no longer reachable under /api/
    shell = client.get("/work-items", headers=forged)
    assert shell.status_code == 200
    assert shell.text.startswith("<!doctype html>")


def test_a_bind_change_does_not_lock_out_a_server_still_on_loopback(client):
    """The bind takes effect on restart, so auth has to follow what the process
    actually bound — otherwise saving the setting logs the local operator out of
    a server that is still only listening on 127.0.0.1."""
    client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
    client.cookies.clear()
    assert client.get("/api/work-items").status_code == 200
    assert client.get("/api/access").json()["auth_required"] is False


@pytest.mark.api_client(host="0.0.0.0")
def test_a_failed_write_does_not_sign_everyone_out(client, monkeypatch):
    """Revoking first and then failing to persist the new hash would sign every
    session out while leaving the old password live."""
    client.put("/api/access", json={"bind": "0.0.0.0", "password": "first"})
    client.post("/api/login", json={"password": "first"})
    before = [s["id"] for s in client.get("/api/sessions").json()["sessions"]]
    assert len(before) == 1

    monkeypatch.setattr(
        "kraft.config.save_access",
        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")),
    )
    with pytest.raises(OSError):
        client.put("/api/access", json={"password": "second"})
    # still signed in, still on the old password (last_seen_at moves; the
    # session itself is what must survive)
    assert [s["id"] for s in client.get("/api/sessions").json()["sessions"]] == before
    assert client.get("/api/work-items").status_code == 200


@pytest.mark.api_client(host="0.0.0.0")
def test_a_bearer_token_authenticates_where_a_cookie_would(client, tmp_path):
    """`kraft mcp` has no cookie jar. The token file is its credential (design §5)."""
    assert (
        client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"}).status_code
        == 200
    )
    client.cookies.clear()
    assert client.get("/api/work-items").status_code == 401

    token = auth.read_mcp_token(tmp_path / "run")
    assert token, "serving should have created the token file"
    assert (
        client.get("/api/work-items", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )
    assert (
        client.get("/api/work-items", headers={"Authorization": "Bearer wrong"}).status_code == 401
    )
    # a bare token without the scheme is not a credential
    assert client.get("/api/work-items", headers={"Authorization": token}).status_code == 401


@pytest.mark.api_client(host="0.0.0.0")
def test_a_document_navigation_cannot_slip_past_the_bearer_check(client):
    """The SPA-shell branch runs first, so it must not become an auth bypass for
    JSON: with no dist configured there is no shell, and the request still 401s."""
    client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
    client.cookies.clear()
    r = client.get("/api/work-items", headers={"sec-fetch-dest": "document"})
    assert r.status_code == 401
