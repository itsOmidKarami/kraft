"""The remote client's pieces: URL keys, the session store, sign-in."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys

import httpx
import pytest

from kraft import client
from kraft.client import remote


@pytest.mark.parametrize(
    ("given", "key"),
    [
        ("http://Kraft.Example", "http://kraft.example:80"),
        ("https://kraft.example/", "https://kraft.example:443"),
        ("https://kraft.example:8443", "https://kraft.example:8443"),
        ("HTTP://127.0.0.1:8765", "http://127.0.0.1:8765"),
        ("http://[::1]:9000", "http://[::1]:9000"),
        ("https://[2001:DB8::1]", "https://[2001:db8::1]:443"),
        ("  http://box:1  ", "http://box:1"),
    ],
    ids=[
        "lowercase+port",
        "slash+scheme-port",
        "explicit-port",
        "scheme-case",
        "ipv6-loopback",
        "ipv6-lowercased",
        "stripped",
    ],
)
def test_a_server_url_is_keyed_by_scheme_host_and_port(given, key):
    assert remote.normalize_url(given) == key


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ("kraft.example", "http:// or https://"),
        ("ftp://kraft.example", "http:// or https://"),
        ("https://kraft.example/kraft/", "path"),
        ("https://kraft.example/?x=1", "path"),
        ("https://kraft.example#top", "path"),
        ("https://user:pw@kraft.example", "user name"),
        ("http://[fe80::1%25eth0]:8765", "zone"),
        ("http://", "host"),
        ("http://box:notaport", "port"),
    ],
    ids=[
        "no-scheme",
        "ftp",
        "path-prefix",
        "query",
        "fragment",
        "userinfo",
        "zone-id",
        "no-host",
        "bad-port",
    ],
)
def test_a_server_url_that_cannot_be_a_key_is_refused_in_one_sentence(given, said):
    with pytest.raises(client.ClientError) as caught:
        remote.normalize_url(given)
    assert caught.value.kind == client.Failure.REFUSED
    assert said in str(caught.value)


@pytest.mark.parametrize(
    ("url", "warns"),
    [
        ("http://kraft.example:80", True),
        ("http://100.64.0.7:8765", True),
        ("http://127.0.0.1:8765", False),
        ("http://[::1]:8765", False),
        ("http://localhost:8765", False),
        ("https://kraft.example:443", False),
    ],
    ids=[
        "http-named-host",
        "http-tailscale-ip",
        "http-loopback",
        "http-ipv6-loopback",
        "http-localhost",
        "https",
    ],
)
def test_only_plain_http_to_a_non_loopback_host_warns(url, warns):
    assert remote.is_plain_http_remote(url) is warns


def test_shown_drops_the_scheme_only():
    assert remote.shown("https://kraft.example:443") == "kraft.example:443"


def test_the_store_lives_in_the_run_dir_and_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path))
    assert remote.sessions_path() == tmp_path / "console-sessions.json"
    store = remote.SessionStore(remote.sessions_path())
    store.put("https://a:443", "tok-a")
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert json.loads(store.path.read_text()) == {"https://a:443": "tok-a"}


def test_the_store_keeps_each_server_apart_and_survives_a_second_writer(tmp_path):
    first = remote.SessionStore(tmp_path / "s.json")
    second = remote.SessionStore(tmp_path / "s.json")  # another console
    first.put("https://a:443", "A")
    second.put("https://b:443", "B")
    assert (first.get("https://a:443"), first.get("https://b:443")) == ("A", "B")
    # no temp file is left behind by the atomic write
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]


@pytest.mark.parametrize(
    ("stored", "failed", "left"),
    [("old", "old", None), ("new", "old", "new")],
    ids=["same-token-deleted", "re-signed-in-meanwhile-kept"],
)
def test_an_entry_is_forgotten_only_if_it_still_holds_the_token_that_failed(
    tmp_path, stored, failed, left
):
    store = remote.SessionStore(tmp_path / "s.json")
    store.put("https://a:443", stored)
    store.forget("https://a:443", failed)
    assert store.get("https://a:443") == left


@pytest.mark.parametrize(
    "body",
    ["", "not json", "[1]", '{"https://a:443": 7}'],
    ids=["empty", "garbage", "not-a-map", "token-not-a-string"],
)
def test_a_store_that_does_not_say_truthfully_reads_as_no_session(tmp_path, body):
    path = tmp_path / "s.json"
    path.write_text(body)
    assert remote.SessionStore(path).get("https://a:443") is None


def _server(handler):
    return httpx.MockTransport(handler)


def test_sign_in_posts_the_password_with_a_label_and_reads_the_cookie_by_hand():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(
            url=str(request.url),
            ua=request.headers["user-agent"],
            body=json.loads(request.content),
            cookie=request.headers.get("cookie"),
        )
        # Secure and HttpOnly on a plain-http server: a cookie jar would drop it.
        return httpx.Response(
            200,
            json={"ok": True},
            headers={
                "set-cookie": "kraft_session=abc123; HttpOnly; Max-Age=86400; "
                "Path=/; SameSite=lax; Secure"
            },
        )

    target = asyncio.run(
        remote.sign_in(
            "http://Box:8765/", "pw", "Kraft console on laptop", transport=_server(handler)
        )
    )
    assert target == remote.Target("http://box:8765", "abc123")
    assert seen == {
        "url": "http://box:8765/api/login",
        "ua": "Kraft console on laptop",
        "body": {"password": "pw", "stay_signed_in": True},
        "cookie": None,
    }


@pytest.mark.parametrize(
    ("response", "kind", "said"),
    [
        (
            httpx.Response(401, json={"detail": "Wrong password."}),
            client.Failure.UNAUTHORIZED,
            "Wrong password.",
        ),
        (
            httpx.Response(
                429,
                json={"detail": "Too many failed logins. Try again later."},
                headers={"Retry-After": "540"},
            ),
            client.Failure.REFUSED,
            "9 minutes",
        ),
        (
            httpx.Response(302, headers={"location": "https://other.example/api/login"}),
            client.Failure.REFUSED,
            "redirect",
        ),
        (httpx.Response(200, json={"ok": True}), client.Failure.BAD_RESPONSE, "session cookie"),
        (httpx.Response(502, text="bad gateway"), client.Failure.SERVER_ERROR, "502"),
    ],
    ids=["wrong-password", "rate-limited", "redirected", "no-cookie", "proxy-error"],
)
def test_sign_in_says_why_it_did_not_get_a_session(response, kind, said):
    with pytest.raises(client.ClientError) as caught:
        asyncio.run(
            remote.sign_in(
                "https://box",
                "pw",
                "Kraft console on x",
                transport=_server(lambda request: response),
            )
        )
    assert (caught.value.kind, said in str(caught.value)) == (kind, True)


def test_sign_in_to_nothing_says_there_is_no_server_there():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(client.ClientError) as caught:
        asyncio.run(remote.sign_in("https://box", "pw", "l", transport=_server(handler)))
    assert caught.value.kind == client.Failure.UNREACHABLE and "box:443" in str(caught.value)


def test_the_label_names_the_client_and_the_machine(monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "work-laptop")
    assert remote.console_label() == "Kraft console on work-laptop"


def test_kraft_client_remote_is_the_submodule_not_a_copied_global():
    """`from kraft.client import remote` after the package's star-imports: a public
    `transport.remote` global would shadow the submodule with its None."""
    code = 'from kraft.client import remote; assert type(remote).__name__ == "module"'
    ran = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
