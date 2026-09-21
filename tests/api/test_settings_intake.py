"""The intake policy document: defaults, persistence, poller validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from support.harness import fake_templates_dir

from kraft import config

#: No default repo entry for an unconnected repo (`support.api._client`): these read real config.
pytestmark = pytest.mark.api_client(default_setup=False)


_FAKE_AGENT = Path(__file__).resolve().parents[1] / "support" / "fake_agent.py"


@pytest.fixture
def templates_dir(tmp_path):
    return fake_templates_dir(tmp_path, "claude")


def test_get_intake_returns_the_defaults_when_no_file_was_written(client):
    body = client.get("/api/intake").json()
    assert body["enabled"] is False
    assert body["interval_s"] == config.INTAKE_DEFAULT["interval_s"]


def test_put_intake_persists_and_applies_without_a_restart(client, templates_dir):
    """The poller task is replaced, not just the dict it reads: `interval_s` is
    read once at task start, so a live poller would keep the old interval."""
    app = client.app
    assert app.state.intake_task is None

    saved = client.put(
        "/api/intake",
        json={
            "enabled": True,
            "interval_s": 60,
            "max_concurrent": 2,
            "priority_ceiling": 3,
            "repos": ["/repo-a"],
        },
    )
    assert saved.status_code == 200
    assert app.state.intake["interval_s"] == 60
    assert app.state.intake_task is not None
    assert yaml.safe_load((templates_dir / "intake.yaml").read_text())["max_concurrent"] == 2
    assert client.get("/api/intake").json()["repos"] == ["/repo-a"]

    # and turning it back off stops the poller rather than leaving a live timer
    client.put(
        "/api/intake",
        json={
            "enabled": False,
            "interval_s": 60,
            "max_concurrent": 2,
            "priority_ceiling": 3,
            "repos": [],
        },
    )
    assert app.state.intake_task is None


@pytest.mark.parametrize(
    "over",
    [
        {"interval_s": 5},  # below the floor the poller would clamp to anyway
        {"max_concurrent": 0},
        {"priority_ceiling": 5},
        {"priority_ceiling": -1},
    ],
)
def test_put_intake_rejects_a_setting_the_poller_would_not_honour(client, over):
    body = {
        "enabled": True,
        "interval_s": 60,
        "max_concurrent": 1,
        "priority_ceiling": 2,
        "repos": [],
        **over,
    }
    assert client.put("/api/intake", json=body).status_code == 422


def test_put_intake_no_longer_requires_max_concurrent(client):
    body = client.put(
        "/api/intake",
        json={"enabled": False, "interval_s": 300, "priority_ceiling": 2, "repos": []},
    )
    assert body.status_code == 200, body.text


def test_get_intake_carries_repo_pickup_stats(client, templates_dir):
    body = client.get("/api/intake").json()
    assert "repo_pickups" in body
    assert "recent_pickups" in body
    assert body["recent_pickups"] == []
