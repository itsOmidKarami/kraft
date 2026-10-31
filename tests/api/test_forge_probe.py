"""When the server probes API accounts: not on a poll of `/health`."""

from __future__ import annotations

import time

import pytest
from support import forge_probe as canned

from kraft.adapters.forge import probe


@pytest.fixture
def templates_dir(templates_dir):
    (templates_dir / "forges.yaml").write_text(
        "accounts:\n  corp:\n    forge: gitlab\n    client: api\n"
        "    web_url: https://code.corp.example/gitlab\n"
        "    token: {from: env, env: CORP_GL, kind: pat}\n"
    )
    return templates_dir


def until(n):
    for _ in range(100):
        if len(canned.CALLS) >= n:
            break
        time.sleep(0.05)
    return len(canned.CALLS)


def test_probes_run_at_start_reload_test_and_timer_never_per_health_poll(client, templates_dir):
    assert until(1) == 1
    for _ in range(3):
        client.get("/api/health")
    (templates_dir / "forges.yaml").write_text(
        (templates_dir / "forges.yaml").read_text().replace("pat}", "oauth}")
    )
    client.post("/api/apply/reload")
    assert until(2) == 2
    for _ in range(3):
        client.get("/api/health")
    time.sleep(0.2)
    assert len(canned.CALLS) == 2
    assert client.post("/api/forges/corp/test").json()["ok"] is True
    assert len(canned.CALLS) == 3
    assert client.app.state.forge_probes["corp"].state == "ok"


@pytest.fixture
def fast_timer(monkeypatch):
    monkeypatch.setattr(probe, "PROBE_INTERVAL_S", 0.05)


def test_the_timer_probes_again_with_nothing_asking(fast_timer, client):
    assert until(3) >= 3


def test_a_removed_account_leaves_health(client, templates_dir):
    assert until(1) == 1
    client.app.state.forge_probes["corp"] = probe.Probe("corp", "t", "refused", "HTTP 401")
    assert client.get("/api/health").json()["status"] == "degraded"
    (templates_dir / "forges.yaml").write_text("accounts: {}\n")
    client.post("/api/apply/reload")
    health = client.get("/api/health").json()
    assert health["forge_tokens"] == {} and health["status"] == "ok", health
