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
    """Fails closed: no `allowed_hosts` configured is not an open gate. Only
    this machine's own loopback names get through, as on any LAN bind."""
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


@pytest.mark.api_client(host="0.0.0.0")
def test_an_allowed_host_typed_with_a_port_case_or_scheme_still_matches(client):
    """A Host header carries the port, so `kraft.test:8765` is a natural thing
    to type into the list. Saved as typed, it never matched the lowercased,
    portless name the perimeter compares, and the browser got a 403 on its own
    board. Saved normalized, the list reads back the way it is compared."""
    typed = ["Kraft.Test:8765", "http://phone.test/", "[FD00::5]:8765", "tab.test.", "kraft.test"]
    saved = client.put(
        "/api/access", json={"bind": "0.0.0.0", "password": "hunter2", "allowed_hosts": typed}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["allowed_hosts"] == ["kraft.test", "phone.test", "[fd00::5]", "tab.test"]
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
    for host in ("kraft.test:8765", "Phone.Test:8765", "[fd00::5]:8765", "tab.test.:8765"):
        browser = {"host": host, "sec-fetch-site": "same-origin"}
        assert client.get("/api/work-items", headers=browser).status_code == 200, host


@pytest.mark.api_client(host="0.0.0.0")
def test_an_allowed_host_that_is_not_one_name_is_refused_by_name(client):
    """Wildcards are not supported. Saved, `*.ts.net` would be a name no
    browser sends; refused, the 422 says which entry and what form works."""
    r = client.put(
        "/api/access",
        json={"bind": "0.0.0.0", "password": "hunter2", "allowed_hosts": ["ok.test", "*.ts.net"]},
    )
    assert r.status_code == 422, r.text
    assert "'*.ts.net' is not a host name or IP address" in r.json()["detail"]
    assert client.get("/api/access").json()["allowed_hosts"] == []


def _a_hand_edited_wildcard(templates_dir):
    (templates_dir / "access.yaml").write_text("allowed_hosts: ['*.ts.net', kraft.local]\n")


@pytest.mark.api_client(edit_templates=_a_hand_edited_wildcard)
def test_a_bad_entry_already_stored_does_not_block_the_next_save(client):
    """The Access screens send the whole list back on every add and remove.
    `access.yaml` loads a hand-edited `*.ts.net` as written, so refusing
    every entry again would make each later save of that list a 422. Only a
    new entry is checked; the stored one stays for `config_check` to name."""
    assert client.get("/api/access").json()["allowed_hosts"] == ["*.ts.net", "kraft.local"]
    added = client.put(
        "/api/access", json={"allowed_hosts": ["*.ts.net", "kraft.local", "Phone.Local:8765"]}
    )
    assert added.status_code == 200, added.text
    assert added.json()["allowed_hosts"] == ["*.ts.net", "kraft.local", "phone.local"]
    another = client.put("/api/access", json={"allowed_hosts": ["*.ts.net", "*.lan"]})
    assert another.status_code == 422, another.text
    assert "'*.lan' is not a host name" in another.json()["detail"]


def test_a_rebound_host_is_refused_for_a_browser_request(client):
    """DNS rebinding: a page on evil.com whose name flips to 127.0.0.1 becomes
    same-origin with the local board and can read every response and drive every
    route. A server that bound loopback answers to loopback names only."""
    browser = {"host": "evil.com:8765", "sec-fetch-site": "same-origin"}
    assert client.get("/api/work-items", headers=browser).status_code == 403
    assert client.post("/api/index/rescan", headers=browser).status_code == 403

    # this is also the ordering guarantee for /api/ itself: _perimeter has
    # to run before _authenticate/_spa_navigation even consider the
    # request, or a forged nav header on a rebound host could slip past it
    # the way test_the_perimeter_runs_before_the_spa_shell_middleware
    # checks for a client-side route.
    nav = {**browser, "sec-fetch-dest": "document"}
    assert client.get("/api/work-items", headers=nav).status_code == 403


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    "headers",
    [
        {"host": "evil.example:8765"},
        {"host": "evil.example:8765", "origin": "http://evil.example:8765"},
        {"host": "[::1"},
        {"host": "evil@127.0.0.1:8765"},
    ],
    ids=["no-browser-headers", "origin-matches-host", "unparseable-host", "userinfo-host"],
)
def test_a_loopback_bind_refuses_a_foreign_host_with_no_fetch_metadata(client, method, headers):
    """Chromium sends no Sec-Fetch-* headers to a plain-http origin on any name
    but localhost, so a rebound page's GET carries a Host and nothing else, and
    its POST an Origin that matches that Host. A loopback bind answers to
    loopback names only, whatever else the request carries."""
    path = "/api/work-items" if method == "GET" else "/api/index/rescan"
    r = client.request(method, path, headers=headers)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "unexpected Host for a server bound to 127.0.0.1"


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:8765",
        "localhost:8765",
        "[::1]:8765",
        "LOCALHOST",
        "127.0.0.1",
        "localhost.:8765",
        "127.0.0.1.:8765",
        "[0:0:0:0:0:0:0:1]:8765",
    ],
    ids=[
        "v4-port",
        "localhost-port",
        "v6-port",
        "case",
        "no-port",
        "localhost-trailing-dot",
        "v4-trailing-dot",
        "v6-uncompressed",
    ],
)
def test_a_loopback_bind_answers_to_every_loopback_name(client, host):
    """The CLI, MCP, the VS Code extension and the dev proxy all dial one of
    these, browser or not. A trailing dot or an uncompressed ::1 is the same
    name, and no one else can own it."""
    for headers in ({"host": host}, {"host": host, "sec-fetch-site": "same-origin"}):
        assert client.get("/api/work-items", headers=headers).status_code == 200


_BROWSER_SHAPED = [
    {"sec-fetch-site": "same-origin"},
    {"origin": "http://{host}"},
    {"referer": "http://{host}/work-items"},
    {"accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
]
_BROWSER_IDS = ["fetch-metadata", "origin", "referer", "page-navigation"]


def _browser(host, shape):
    return {"host": host, **{k: v.format(host=host) for k, v in shape.items()}}


@pytest.fixture
def lan_bind(client):
    """A `0.0.0.0` bind listing kraft.example.com, signed in."""
    saved = client.put(
        "/api/access",
        json={"bind": "0.0.0.0", "password": "hunter2", "allowed_hosts": ["kraft.example.com"]},
    )
    assert saved.status_code == 200, saved.text
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
    return client


@pytest.mark.api_client(host="0.0.0.0")
@pytest.mark.parametrize("shape", _BROWSER_SHAPED, ids=_BROWSER_IDS)
def test_a_non_loopback_bind_checks_every_browser_shaped_request(lan_bind, shape):
    """Off loopback the allowlist applies to anything a browser adds on its own,
    not to Sec-Fetch-Site alone: a browser on plain http sends none of that."""
    listed = _browser("kraft.example.com:8765", shape)
    assert lan_bind.get("/api/work-items", headers=listed).status_code == 200
    unlisted = _browser("evil.example:8765", shape)
    r = lan_bind.get("/api/work-items", headers=unlisted)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "unexpected Host for a server bound to 0.0.0.0"


@pytest.mark.api_client(host="0.0.0.0")
@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765"])
def test_a_non_loopback_bind_always_answers_a_browser_on_this_machine(lan_bind, host):
    """The board still opens at http://127.0.0.1:8765 on the machine itself
    after a switch to a LAN bind, without listing the loopback names: a
    browser only sends one when it really is on this machine."""
    for shape in _BROWSER_SHAPED:
        r = lan_bind.get("/api/work-items", headers=_browser(host, shape))
        assert r.status_code == 200, r.text


_NAVIGATION = {"accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


@pytest.mark.api_client(host="0.0.0.0")
def test_a_browser_opening_an_unlisted_name_gets_a_page_saying_how_to_allow_it(lan_bind):
    """1.4 never checked a plain-http browser's Host, so an upgraded LAN user's
    first sight of `allowed_hosts` is this refusal: a page naming the Host it
    got and where to add it, not a bare JSON line. The Host is the
    requester's text, so it is escaped. A script, and the board's own API
    calls, still get the JSON refusal."""
    page = lan_bind.get("/", headers={"host": "MyBox.lan:8765", **_NAVIGATION})
    assert page.status_code == 403
    assert page.headers["content-type"].startswith("text/html")
    assert "<code>mybox.lan</code>" in page.text
    assert "Settings &gt; Access" in page.text and "allowed_hosts" in page.text
    assert page.headers["x-frame-options"] == "SAMEORIGIN"

    hostile = lan_bind.get("/", headers={"host": "<b>x</b>", **_NAVIGATION})
    assert hostile.status_code == 403 and "<b>" not in hostile.text

    api = lan_bind.get("/api/work-items", headers={"host": "mybox.lan:8765", **_NAVIGATION})
    assert api.json()["detail"] == "unexpected Host for a server bound to 0.0.0.0"


def test_a_loopback_bind_tells_a_browser_where_it_does_answer(client):
    page = client.get("/", headers={"host": "mybox.lan:8765", **_NAVIGATION})
    assert page.status_code == 403
    assert "<code>mybox.lan</code>" in page.text
    assert "only at <code>localhost</code> or <code>127.0.0.1</code>" in page.text
    assert 'href="http://127.0.0.1:' in page.text


@pytest.mark.api_client(host="::1")
def test_the_page_links_back_to_the_address_the_server_is_bound_to(client):
    """127.0.0.1 is only right for a wildcard bind, which listens there too: a
    server bound to ::1 does not answer at 127.0.0.1."""
    page = client.get("/", headers={"host": "mybox.lan:8765", **_NAVIGATION})
    assert page.status_code == 403
    assert 'href="http://[::1]:' in page.text
    assert "127.0.0.1:" not in page.text


def test_the_page_shows_a_non_ascii_host_as_the_browser_sent_it(client):
    """Starlette decodes headers as latin-1, which turns UTF-8 into mojibake."""
    page = client.get("/", headers={"host": "bücher.lan:8765".encode(), **_NAVIGATION})
    assert page.status_code == 403
    assert "<code>bücher.lan</code>" in page.text


@pytest.mark.api_client(host="0.0.0.0")
def test_a_non_loopback_bind_needs_no_allowlist_entry_for_a_non_browser_client(lan_bind, tmp_path):
    """The CLI, MCP and curl carry none of a browser's headers, so they reach a
    LAN bind by whatever address they dialled, and the auth gate decides."""
    bare = {"host": "100.101.102.103:8765"}
    assert lan_bind.get("/api/work-items", headers=bare).status_code == 200
    lan_bind.cookies.clear()
    assert lan_bind.get("/api/work-items", headers=bare).status_code == 401
    token = auth.read_mcp_token(tmp_path / "run")
    bearer = {**bare, "authorization": f"Bearer {token}"}
    assert lan_bind.get("/api/work-items", headers=bearer).status_code == 200


def _session(client):
    """The signed-in cookie, by hand: the jar holds it for 127.0.0.1, where
    `lan_bind` logged in, and a browser on the LAN logged in at its own name."""
    return {"cookie": f"{auth.COOKIE}={client.cookies[auth.COOKIE]}"}


@pytest.mark.api_client(host="0.0.0.0")
def test_a_non_loopback_bind_streams_events_to_a_board_on_an_allowed_host(lan_bind, tmp_path):
    """A phone or another computer opens the board at a listed name and gets
    live updates. Its websocket carries an Origin that is neither loopback
    nor different from the Host: the board's own page, which the HTTP routes
    already accept by rule 3's Origin-equals-Host clause."""
    board = {"origin": "http://kraft.example.com:8765", **_session(lan_bind)}
    with lan_bind.websocket_connect(
        "ws://kraft.example.com:8765/api/ws/events", headers=board
    ) as ws:
        lan_bind.post(
            "/api/work-items", json={"autostart": False, "title": "hi", "repo": str(tmp_path)}
        )
        assert ws.receive_json()["type"] == "work_item_created"


@pytest.mark.api_client(host="0.0.0.0")
def test_a_non_loopback_bind_streams_events_to_a_non_browser_client_on_any_host(lan_bind, tmp_path):
    """`kraft view watch` against a LAN server sends no Origin, so it needs no
    allowlist entry for the name it dialled, as on the HTTP routes."""
    token = auth.read_mcp_token(tmp_path / "run")
    with lan_bind.websocket_connect(
        "ws://100.101.102.103:8765/api/ws/events",
        headers={"authorization": f"Bearer {token}"},
    ) as ws:
        assert ws is not None


@pytest.mark.api_client(host="0.0.0.0")
@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("ws://kraft.example.com:8765/api/ws/events", "http://evil.example"),
        ("ws://evil.example:8765/api/ws/events", "http://evil.example:8765"),
        ("ws://kraft.example.com:8765/api/ws/events", "http://kraft.example.com:9999"),
    ],
    ids=["cross-site-origin", "unlisted-host", "listed-name-other-port"],
)
def test_a_non_loopback_bind_refuses_the_event_stream_to_any_other_page(lan_bind, url, origin):
    """The session cookie is valid in every case, so the perimeter is what
    refuses: it wants a listed Host and the board's own Origin."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with lan_bind.websocket_connect(url, headers={"origin": origin, **_session(lan_bind)}):
            pass
    assert exc.value.code == 1008


@pytest.mark.parametrize(
    "headers",
    [{}, {"origin": "http://evil.example:8765"}],
    ids=["no-origin", "origin-matches-host"],
)
def test_a_loopback_bind_refuses_the_event_stream_to_a_rebound_name(client, headers):
    """A rebound page's Origin matches its own Host, which is what lets a LAN
    board in. On a loopback bind the Host rule refuses it first."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("ws://evil.example:8765/api/ws/events", headers=headers):
            pass
    assert exc.value.code == 1008


def test_the_perimeter_runs_before_the_spa_shell_middleware(client, dist):
    """Starlette enters the last-added middleware first, so `_perimeter` has to be
    declared *below* `_authenticate` and `_spa_navigation`. Declared above, this
    rebound navigation gets the SPA shell instead of a 403.

    A client-side route, not an /api/ one: that prefix is excluded from the
    shell-diversion branches entirely now, so it would pass even with the
    middleware in the wrong order and prove nothing about ordering.

    `client` is listed before `dist` deliberately, the reverse of every other
    test here: `client` now pulls `dist` itself before its lifespan starts
    (tests/conftest.py), so declaration order no longer decides whether this
    test exercises anything. Do not use this as a template — list `dist`
    before `client` as usual; this ordering only proves the fixture no longer
    depends on it."""
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
    assert r.text.startswith("<!doctype html>")


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
        client.post(
            "/api/work-items", json={"autostart": True, "title": "hello", "repo": str(tmp_path)}
        )
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
        "kraft.config.Access.save",
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


@_LAN
def test_the_trigger_token_files_work_and_does_nothing_else(client, monkeypatch, repo, tmp_path):
    """CI and webhooks get a credential that cannot approve or reconfigure; the
    admin token keeps working on both."""
    _set_password(client, monkeypatch)
    run = tmp_path / "run"
    assert (run / auth.TRIGGER_TOKEN_FILE).stat().st_mode & 0o777 == 0o600
    trigger = auth.read_mcp_token(run, auth.TRIGGER_TOKEN_FILE)
    admin = auth.read_mcp_token(run)
    assert trigger and trigger != admin
    approve = "/api/work-items/nope/gates/spec/approve"
    for token in (trigger, admin):
        r = client.post(
            "/api/triggers",
            json={"repo": str(repo), "title": "t"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
    r = client.post(approve, headers={"Authorization": f"Bearer {trigger}"})
    assert r.status_code == 401
    assert client.post(approve, headers={"Authorization": f"Bearer {admin}"}).status_code != 401


@pytest.mark.api_client(host="0.0.0.0")
def test_a_document_navigation_cannot_slip_past_the_bearer_check(client):
    """The SPA-shell branch runs first, so it must not become an auth bypass for
    JSON: with no dist configured there is no shell, and the request still 401s."""
    client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
    client.cookies.clear()
    r = client.get("/api/work-items", headers={"sec-fetch-dest": "document"})
    assert r.status_code == 401


def test_a_document_navigation_under_ng_gets_the_spa_shell(dist, client):
    """The new UI was served under /ng until the cutover, and the shell
    redirects an /ng/... bookmark to the same page without the prefix
    (frontend/src/main.tsx). It only can if the server answers /ng/... with
    the shell, uncached, like any client-side route."""
    r = client.get("/ng/work-items/abc", headers={"sec-fetch-dest": "document"})
    assert r.status_code == 200, r.text
    assert r.text == (dist / "index.html").read_text()
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_a_browser_navigation_to_fastapis_docs_gets_them_not_the_board(dist, client, path):
    """Kraft-9efnk.24: the SPA-shell fast path answered every non-/api
    navigation, so a browser opening /docs or /redoc got the board."""
    r = client.get(path, headers={"sec-fetch-dest": "document"})
    assert r.status_code == 200, r.text
    assert "/openapi.json" in r.text


@pytest.mark.parametrize(
    ("method", "path", "headers", "status"),
    [
        ("GET", "/work-items/abc", {"sec-fetch-dest": "document"}, 200),
        ("GET", "/", {}, 200),
        ("GET", "/assets/app.js", {}, 200),
        ("GET", "/api/health", {}, 200),
        ("GET", "/docs", {}, 200),
        ("GET", "/api/nothing-here", {}, 404),
        ("GET", "/work-items/abc", {"host": "evil.com:8765", "sec-fetch-dest": "document"}, 403),
    ],
    ids=["spa-navigation", "spa-root", "static-asset", "api", "swagger", "api-404", "refused"],
)
def test_no_other_site_may_frame_any_response(dist, client, method, path, headers, status):
    """A framed loopback board is live with no login, so a page that framed it
    under its own content could turn a click there into Resume or Approve.
    Every response forbids it, a refusal included: the SPA shell is what a
    frame would load, but a shell served by one route and not another is a
    hole the next route opens."""
    r = client.request(method, path, headers=headers)
    assert r.status_code == status, r.text
    # One directive of the policy: a page may carry others of its own (/docs).
    directives = [d.strip() for d in r.headers["content-security-policy"].split(";")]
    assert "frame-ancestors 'self'" in directives
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
