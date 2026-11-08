"""The remote client's pieces: URL keys, the session store, sign-in."""

from __future__ import annotations

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
