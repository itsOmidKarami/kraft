"""`kraft console --server URL`'s start-up: a stored session, or a password prompt
before the TUI starts. Prompts are injected; nothing reaches a network."""

from __future__ import annotations

import pytest

from kraft import client
from kraft.client import remote
from kraft.console import signin


class Prompts:
    def __init__(self, password="pw", go_on=True):
        self.asked, self.warned = [], []
        self._password, self._go_on = password, go_on

    def password(self, shown):
        self.asked.append(shown)
        return self._password

    def go_on(self, warning):
        self.warned.append(warning)
        return self._go_on


async def _ok(url, password, label, **kw):
    return remote.Target(remote.normalize_url(url), "fresh")


def _connect(tmp_path, url, prompts, sign_in=_ok):
    store = remote.SessionStore(tmp_path / "s.json")
    target = signin.connect(
        url,
        ask_password=prompts.password,
        ask_continue=prompts.go_on,
        sign_in=sign_in,
        store=store,
    )
    return target, store


def test_a_stored_session_starts_the_console_without_a_prompt(tmp_path):
    prompts = Prompts()
    remote.SessionStore(tmp_path / "s.json").put("https://box:443", "kept")
    target, _ = _connect(tmp_path, "https://box", prompts)
    assert target == remote.Target("https://box:443", "kept") and prompts.asked == []


def test_no_stored_session_asks_once_signs_in_and_stores_the_session(tmp_path):
    prompts = Prompts()
    target, store = _connect(tmp_path, "https://box", prompts)
    assert (target.session, prompts.asked, prompts.warned) == ("fresh", ["box:443"], [])
    assert store.get("https://box:443") == "fresh"
    # the next start finds the stored session and asks nothing
    again = Prompts()
    signin.connect(
        "https://box", ask_password=again.password, ask_continue=again.go_on, store=store
    )
    assert again.asked == []


def test_signing_in_sends_the_console_label(tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "console_label", lambda: "Kraft console on laptop")
    seen = []

    async def sign_in(url, password, label, **kw):
        seen.append((password, label))
        return remote.Target(remote.normalize_url(url), "t")

    _connect(tmp_path, "https://box", Prompts(password="hunter2"), sign_in)
    assert seen == [("hunter2", "Kraft console on laptop")]


@pytest.mark.parametrize(
    ("go_on", "stored"),
    [(True, "fresh"), (False, None)],
    ids=["go-on", "decline"],
)
def test_plain_http_to_another_host_warns_every_sign_in_and_asks_to_go_on(tmp_path, go_on, stored):
    prompts = Prompts(go_on=go_on)
    if go_on:
        _connect(tmp_path, "http://100.64.0.7:8765", prompts)
    else:
        with pytest.raises(ValueError, match="not signed in"):
            _connect(tmp_path, "http://100.64.0.7:8765", prompts)
    assert len(prompts.warned) == 1 and "unencrypted" in prompts.warned[0]
    assert remote.SessionStore(tmp_path / "s.json").get("http://100.64.0.7:8765") == stored
    assert (prompts.asked != []) is go_on  # declining never asks for the password


def test_plain_http_to_loopback_does_not_warn(tmp_path):
    prompts = Prompts()
    _connect(tmp_path, "http://127.0.0.1:9000", prompts)  # an ssh -L forward
    assert prompts.warned == []


def test_a_wrong_password_exits_with_the_servers_message_and_stores_nothing(tmp_path):
    async def refuse(url, password, label, **kw):
        raise client.status_error(401, "Wrong password.")

    with pytest.raises(client.ClientError, match="Wrong password"):
        _connect(tmp_path, "https://box", Prompts(), refuse)
    assert remote.SessionStore(tmp_path / "s.json").get("https://box:443") is None
