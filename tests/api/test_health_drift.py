"""kraft-default drift on the server: a signature and count on public health,
the lines on an authenticated route."""

from __future__ import annotations

import json
import time

import httpx
import pytest
import yaml

from kraft import config, notify
from kraft.addons import builtin, drift


def _extend_both(templates_dir):
    """The fake home ships full copies of both built-in chains; thin ones are clean."""
    for chain in ("default", "quick-task"):
        body = {"id": chain, "extends": f"kraft-default:{chain}"}
        (templates_dir / "chains" / f"{chain}.yaml").write_text(yaml.safe_dump(body))


def _one_full_copy(templates_dir):
    _extend_both(templates_dir)
    (templates_dir / "chains" / "default.yaml").write_bytes(
        builtin.extracted().files["chains/default.yaml"][1]
    )


@pytest.mark.api_client(edit_templates=_one_full_copy)
def test_a_full_copy_shows_on_health_and_the_route(client):
    health = client.get("/api/health").json()
    assert health["kraft_default"]["count"] == 1
    assert health["kraft_default"]["signature"]
    assert "full copy" not in str(health)
    assert health["status"] == "ok"
    got = client.get("/api/kraft-default").json()
    assert "chains/default.yaml is a full copy" in got["lines"][0]
    assert got["signature"] == health["kraft_default"]["signature"]


@pytest.mark.api_client(edit_templates=_extend_both)
def test_a_clean_home_has_none(client):
    assert client.get("/api/health").json()["kraft_default"] == {"signature": None, "count": 0}
    assert client.get("/api/kraft-default").json() == {"lines": [], "signature": None}


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
def test_the_route_is_refused_without_a_session(client, monkeypatch):
    st = client.app.state
    monkeypatch.setattr(st, "access", {**st.access, "password_hash": "x"}, raising=False)
    assert client.get("/api/kraft-default").status_code == 401


def test_a_check_that_raises_is_one_line_and_startup_completes(request, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(drift, "lines", boom)
    from support.api import _client

    with _client(tmp_path, monkeypatch) as client:
        health = client.get("/api/health").json()
        assert health["kraft_default"]["count"] == 1
        assert (
            "cannot check kraft-default: nope"
            in client.get("/api/kraft-default").json()["lines"][0]
        )


@pytest.fixture
def webhook(monkeypatch):
    """Every Notifier the app builds posts to a recording transport. `kraft.api.app`
    is module-level, so the last test's notifier and sent mark are dropped first."""
    from kraft.api import app

    for name in ("notifier", "kraft_default_sent"):
        monkeypatch.setattr(app.state, name, None, raising=False)
    sends: list = []

    async def transport(request: httpx.Request) -> httpx.Response:
        sends.append(json.loads(request.content))
        return httpx.Response(200)

    init = notify.Notifier.__init__

    def init_mocked(self, *a, **k):
        init(self, *a, **k)
        self._transport = httpx.MockTransport(transport)

    monkeypatch.setattr(notify.Notifier, "__init__", init_mocked)
    return sends


def _drifted_with_webhook(templates_dir):
    _one_full_copy(templates_dir)
    config.Notify(enabled=True, url="https://hook.invalid/t0ken").save(
        templates_dir / "notify.yaml"
    )


@pytest.mark.api_client(edit_templates=_drifted_with_webhook)
def test_startup_sends_one_drift_notice(webhook, client):
    for _ in range(100):  # the send is a detached task
        if webhook:
            break
        time.sleep(0.02)
    assert [body["type"] for body in webhook] == ["kraft_default_drift"]


def test_startup_works_the_drift_out_once(tmp_path, monkeypatch):
    """The library loads before the notifier exists, so the lines are worked out then; the
    refresh after the notifier is built only sends, instead of scanning every add-on again."""
    calls = []
    real = drift.lines
    monkeypatch.setattr(drift, "lines", lambda *a: calls.append(a) or real(*a))
    from support.api import _client

    with _client(tmp_path, monkeypatch):
        pass

    assert len(calls) == 1
