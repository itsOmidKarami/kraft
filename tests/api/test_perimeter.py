"""Kraft-dwt: who may reach this API at all — peer address, Host, and Origin."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from support.harness import fake_templates_dir, isolated_bd, make_repo

from kraft import auth

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


#: Some tests open a second client against the same `tmp_path` (one loopback, one
#: LAN) to compare their behaviour, and `isolated_bd` copies into a fixed
#: subdirectory name — so each call here needs its own name to avoid colliding
#: with the first client's checkout.
_bd_names = (f"tracker-{n}" for n in itertools.count())


def _client(tmp_path, monkeypatch, *, peer=("127.0.0.1", 54321), host="127.0.0.1", dist=None):
    """A TestClient whose peer address the perimeter can actually read.

    Starlette's default peer is ("testclient", 50000), which is not an IP and is
    refused by a check that fails closed — so every test states its peer, and a
    test that wants to be a LAN client says so by passing one.
    """
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path, name=next(_bd_names))))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(dist or tmp_path / "no-dist"))
    monkeypatch.setenv("KRAFT_HOST", host)
    import kraft.api as api

    return TestClient(api.app, client=peer)


def _set_password(client, monkeypatch):
    """Turn the auth gate on without binding a LAN-visible socket for the test run.

    Same trade as tests/test_ws.py:_require_auth: the hash is never verified here,
    only its presence is, so a placeholder is enough and scrypt is not paid for.
    """
    st = client.app.state
    monkeypatch.setattr(st, "access", {**st.access, "password_hash": "x"}, raising=False)


@pytest.fixture
def templates_dir(tmp_path):
    return fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))


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


def test_a_loopback_client_needs_no_password(tmp_path, monkeypatch):
    """The default instance is unchanged: the operator at the keyboard logs into nothing."""
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/work-items").status_code == 200
        assert client.get("/api/access").json()["auth_required"] is False


def test_a_non_loopback_client_must_log_in_when_a_password_is_set(tmp_path, monkeypatch):
    """`bound_host` is a claim about the bind; the peer address is the fact.

    `uvicorn kraft.api:app --host 0.0.0.0` never updates access.yaml, so a server
    that still believes it is on loopback must not wave a remote peer through.
    """
    with _client(tmp_path, monkeypatch) as client:
        _set_password(client, monkeypatch)
        assert client.app.state.bound_host == "127.0.0.1"
        assert client.get("/api/work-items").status_code == 200  # the local operator is unaffected

    with _client(tmp_path, monkeypatch, peer=("10.0.0.5", 54321)) as lan:
        _set_password(lan, monkeypatch)
        r = lan.get("/api/work-items")
        assert r.status_code == 401, r.text
        assert r.json()["detail"] == "authentication required"


def test_a_non_loopback_client_is_refused_when_no_password_is_set(tmp_path, monkeypatch):
    """The fail-closed half: access.yaml says 127.0.0.1 and a LAN peer arrived
    anyway, so the bind was widened by something that never told the app. With no
    password there is nothing to authenticate against — refuse rather than serve."""
    with _client(tmp_path, monkeypatch, peer=("10.0.0.5", 54321)) as client:
        r = client.get("/api/work-items")
        assert r.status_code == 403, r.text
        assert "loopback bind" in r.json()["detail"]
        assert "10.0.0.5" in r.json()["detail"]
        # the public paths are not an exception: there is nothing here to log into
        assert client.post("/api/login", json={"password": "x"}).status_code == 403
        assert client.get("/api/health").status_code == 403


def test_a_cross_site_origin_cannot_pause_a_work_item(tmp_path, monkeypatch):
    """A bodiless POST needs no preflight, so any page the operator visits could
    reach the mutating routes that take no JSON body. On the default loopback
    instance auth is off, so there is no cookie to withhold either."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "hold still", "autostart": False},
        ).json()["id"]
        assert client.get(f"/api/work-items/{wid}").json()["status"] == "paused"

        r = client.post(f"/api/work-items/{wid}/pause", headers={"origin": "http://evil.com"})
        assert r.status_code == 403, r.text
        assert client.get(f"/api/work-items/{wid}").json()["status"] == "paused"

        # and a GET is not a write: reads are not the thing this rule is about
        assert (
            client.get("/api/work-items", headers={"origin": "http://evil.com"}).status_code == 200
        )


def test_the_dev_server_origin_is_accepted(tmp_path, monkeypatch):
    """`just dev`: vite rewrites Host to 127.0.0.1:8765 (changeOrigin) and passes
    the browser's own Origin through, so the pair never matches — the loopback
    hostname is what makes it legal."""
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/api/index/rescan",
            headers={"origin": "http://localhost:5173", "host": "127.0.0.1:8765"},
        )
        assert r.status_code == 200, r.text


def test_a_lan_instance_accepts_its_own_origin_and_host(tmp_path, monkeypatch):
    """A board served on the LAN must not be locked out of itself. Neither the
    Origin nor the Host is loopback; they match each other, and the bind is not
    loopback, so the rebinding rule has nothing to say about it either."""
    with _client(tmp_path, monkeypatch, host="0.0.0.0") as client:
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


def test_a_non_loopback_bind_refuses_an_unlisted_host(tmp_path, monkeypatch):
    """cdy: a `0.0.0.0` bind gets no rebinding protection today -- this is the
    missing half of test_a_rebound_host_is_refused_for_a_browser_request."""
    with _client(tmp_path, monkeypatch, host="0.0.0.0") as client:
        saved = client.put(
            "/api/access",
            json={"bind": "0.0.0.0", "password": "hunter2", "allowed_hosts": ["kraft.example.com"]},
        )
        assert saved.status_code == 200, saved.text
        assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
        browser = {"host": "evil.example.com:8765", "sec-fetch-site": "same-origin"}
        assert client.get("/api/work-items", headers=browser).status_code == 403


def test_a_non_loopback_bind_accepts_a_listed_host(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, host="0.0.0.0") as client:
        saved = client.put(
            "/api/access",
            json={"bind": "0.0.0.0", "password": "hunter2", "allowed_hosts": ["kraft.example.com"]},
        )
        assert saved.status_code == 200, saved.text
        assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
        browser = {"host": "kraft.example.com:8765", "sec-fetch-site": "same-origin"}
        assert client.get("/api/work-items", headers=browser).status_code == 200


def test_a_non_loopback_bind_with_no_allowlist_refuses_every_browser_host(tmp_path, monkeypatch):
    """Fails closed: no `allowed_hosts` configured is not an open gate."""
    with _client(tmp_path, monkeypatch, host="0.0.0.0") as client:
        saved = client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
        assert saved.status_code == 200, saved.text
        assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
        browser = {"host": "192.168.1.5:8765", "sec-fetch-site": "same-origin"}
        assert client.get("/api/work-items", headers=browser).status_code == 403


def test_get_access_reports_allowed_hosts(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, host="0.0.0.0") as client:
        client.put(
            "/api/access",
            json={"bind": "0.0.0.0", "password": "hunter2", "allowed_hosts": ["a.example.com"]},
        )
        client.post("/api/login", json={"password": "hunter2"})
        r = client.get("/api/access")
        assert r.status_code == 200, r.text
        assert r.json()["allowed_hosts"] == ["a.example.com"]


def test_a_rebound_host_is_refused_for_a_browser_request(tmp_path, monkeypatch):
    """DNS rebinding: a page on evil.com whose name flips to 127.0.0.1 becomes
    same-origin with the local board and can read every response and drive every
    route. A server that bound loopback answers to loopback names only."""
    with _client(tmp_path, monkeypatch) as client:
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


def test_the_perimeter_runs_before_the_spa_shell_middleware(tmp_path, monkeypatch):
    """Starlette enters the last-added middleware first, so `_perimeter` has to be
    declared *below* `_authenticate` and `_spa_navigation`. Declared above, this
    rebound navigation gets the SPA shell instead of a 403.

    A client-side route, not an /api/ one: that prefix is excluded from the
    shell-diversion branches entirely now, so it would pass even with the
    middleware in the wrong order and prove nothing about ordering."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    with _client(tmp_path, monkeypatch, dist=dist) as client:
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


def test_a_client_without_sec_fetch_dest_still_gets_the_spa_shell(tmp_path, monkeypatch):
    """Kraft-qntj: some privacy browsers, in-app webviews and proxies omit or
    strip Sec-Fetch-Dest. That header was the only thing that let an
    unauthenticated GET / through to the shell instead of a raw 401 — so a
    client that never sends it could never reach the login page. Any non-/api
    GET is the same trust level as a static asset already; the bypass is
    keyed to method + path now, not to a header the client controls."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    with _client(tmp_path, monkeypatch, peer=("10.0.0.5", 54321), dist=dist) as client:
        _set_password(client, monkeypatch)
        r = client.get("/")
        assert r.status_code == 200, r.text
        assert r.text.startswith("<!doctype html>")


def test_the_websocket_perimeter_stands_on_its_own(tmp_path, monkeypatch):
    """HTTP middleware does not run for websockets. `/ws/events` therefore keeps
    its own origin check, and needs the bind-mismatch rule restated — otherwise
    the live event stream is the one route the middleware did not close."""
    with _client(tmp_path, monkeypatch) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/ws/events", headers={"origin": "http://evil.com"}):
                pass
        assert exc.value.code == 1008

    with _client(tmp_path, monkeypatch, peer=("10.0.0.5", 54321)) as lan:
        with pytest.raises(WebSocketDisconnect) as exc:
            with lan.websocket_connect("/api/ws/events"):
                pass
        assert exc.value.code == 1008


def test_the_spa_bundle_loads_before_a_session_exists(tmp_path, monkeypatch, templates_dir):
    """The login page cannot render if its own JS comes back 401."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("KRAFT_HOST", "0.0.0.0")
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path, name=next(_bd_names))))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(dist))
    import kraft.api as api

    with TestClient(api.app, client=("127.0.0.1", 54321)) as client:
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


def test_the_event_stream_needs_a_session_too(tmp_path, monkeypatch):
    """HTTP middleware does not run for websockets; the check has to be in the
    endpoint or a LAN bind leaves the live stream wide open."""
    with _client(tmp_path, monkeypatch, host="0.0.0.0") as client:
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


def test_a_forged_navigation_header_cannot_write(tmp_path, monkeypatch, templates_dir):
    """`sec-fetch-dest` is a request header any client can send. It must never be
    a way past the session check into a real handler — every route it could
    forge its way into lives under /api/, which the shell-diversion branches
    exclude outright, so a forged header there just hits the real 401."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path, name=next(_bd_names))))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(dist))
    monkeypatch.setenv("KRAFT_HOST", "0.0.0.0")
    import kraft.api as api

    with TestClient(api.app, client=("127.0.0.1", 54321)) as client:
        client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
        client.cookies.clear()
        forged = {"sec-fetch-dest": "document"}

        # a POST is never a navigation
        assert client.post("/api/work-items/x/pause", json={}, headers=forged).status_code == 401
        assert (
            client.put("/api/access", json={"bind": "0.0.0.0"}, headers=forged).status_code == 401
        )
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


def test_a_bind_change_does_not_lock_out_a_server_still_on_loopback(tmp_path, monkeypatch):
    """The bind takes effect on restart, so auth has to follow what the process
    actually bound — otherwise saving the setting logs the local operator out of
    a server that is still only listening on 127.0.0.1."""
    with _client(tmp_path, monkeypatch) as client:
        client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
        client.cookies.clear()
        assert client.get("/api/work-items").status_code == 200
        assert client.get("/api/access").json()["auth_required"] is False


def test_a_failed_write_does_not_sign_everyone_out(tmp_path, monkeypatch):
    """Revoking first and then failing to persist the new hash would sign every
    session out while leaving the old password live."""
    with _client(tmp_path, monkeypatch, host="0.0.0.0") as client:
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


def test_a_bearer_token_authenticates_where_a_cookie_would(tmp_path, monkeypatch):
    """`kraft mcp` has no cookie jar. The token file is its credential (design §5)."""
    with _client(tmp_path, monkeypatch, host="0.0.0.0") as client:
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
            client.get("/api/work-items", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        # a bare token without the scheme is not a credential
        assert client.get("/api/work-items", headers={"Authorization": token}).status_code == 401


def test_a_document_navigation_cannot_slip_past_the_bearer_check(tmp_path, monkeypatch):
    """The SPA-shell branch runs first, so it must not become an auth bypass for
    JSON: with no dist configured there is no shell, and the request still 401s."""
    with _client(tmp_path, monkeypatch, host="0.0.0.0") as client:
        client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
        client.cookies.clear()
        r = client.get("/api/work-items", headers={"sec-fetch-dest": "document"})
        assert r.status_code == 401
