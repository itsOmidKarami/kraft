"""The policy document (design 5d): findings caps, budget."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from support.api_settings import _client
from support.harness import fake_templates_dir

_FAKE_AGENT = Path(__file__).resolve().parents[0] / "support" / "fake_agent.py"


@pytest.fixture
def templates_dir(tmp_path):
    return fake_templates_dir(tmp_path, "claude")


@pytest.fixture
def client(tmp_path, monkeypatch, templates_dir):
    with _client(tmp_path, monkeypatch, templates_dir) as c:
        yield c


def test_policy_put_rejects_a_cap_that_would_not_load(client, templates_dir):
    good = {
        "loops": {"verify_fix_loop": {"attempts": 5, "wall_clock_s": 60}},
        "default": {"attempts": 3, "wall_clock_s": 3600},
    }
    assert client.put("/api/policy", json=good).status_code == 200
    assert client.get("/api/policy").json()["loops"]["verify_fix_loop"]["attempts"] == 5
    assert yaml.safe_load((templates_dir / "policy.yaml").read_text()) == {
        **good,
        "max_concurrent": 3,
    }

    bad = {"loops": {}, "default": {"attempts": 0, "wall_clock_s": 1}}
    assert client.put("/api/policy", json=bad).status_code == 422
    assert client.get("/api/policy").json()["default"]["attempts"] == 3


def test_saving_the_policy_preserves_the_findings_block(client, templates_dir):
    """A save from the policy screen must not silently erase a block it does not edit."""
    (templates_dir / "policy.yaml").write_text(
        "loops:\n  verify_fix_loop: { attempts: 3, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n"
        "findings:\n  loop_severities: [critical]\n"
    )
    body = client.get("/api/policy").json()
    assert body["findings"]["loop_severities"] == ["critical"]

    body["loops"]["verify_fix_loop"]["attempts"] = 5
    assert client.put("/api/policy", json=body).status_code == 200

    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert on_disk["findings"]["loop_severities"] == ["critical"]
    assert on_disk["loops"]["verify_fix_loop"]["attempts"] == 5


def test_put_policy_persists_the_budget_block(client):
    body = {
        "loops": {},
        "default": {"attempts": 3, "wall_clock_s": 3600},
        "budget": {"work_item_usd": 20.0, "daily_usd": None},
    }
    assert client.put("/api/policy", json=body).status_code == 200
    assert client.get("/api/policy").json()["budget"] == {"work_item_usd": 20.0, "daily_usd": None}


def test_put_policy_persists_rate_limit_retries(client):
    body = {
        "loops": {},
        "default": {"attempts": 3, "wall_clock_s": 3600},
        "rate_limit_retries": 8,
    }
    assert client.put("/api/policy", json=body).status_code == 200
    assert client.get("/api/policy").json()["rate_limit_retries"] == 8


def test_saving_the_policy_preserves_rate_limit_retries_and_triggers(client, templates_dir):
    """A save from the policy screen must not silently erase what it does not edit."""
    (templates_dir / "policy.yaml").write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
        "rate_limit_retries: 8\n"
        "triggers:\n"
        "  - cron: '0 9 * * 1'\n"
        "    repo: /r\n"
        "    chain: default\n"
        "    title: weekly sweep\n"
    )
    body = client.get("/api/policy").json()
    assert body["rate_limit_retries"] == 8
    body["default"]["attempts"] = 5
    assert client.put("/api/policy", json=body).status_code == 200
    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert on_disk["rate_limit_retries"] == 8
    assert on_disk["triggers"][0]["title"] == "weekly sweep"


def test_put_policy_persists_the_archive_block(client):
    body = {
        "loops": {},
        "default": {"attempts": 3, "wall_clock_s": 600},
        "archive": {"after_days": 14},
    }
    assert client.put("/api/policy", json=body).status_code == 200
    assert client.get("/api/policy").json()["archive"] == {"after_days": 14}


def test_put_policy_rejects_a_negative_budget(client):
    body = {
        "loops": {},
        "default": {"attempts": 3, "wall_clock_s": 3600},
        "budget": {"work_item_usd": -5},
    }
    assert client.put("/api/policy", json=body).status_code == 422


def test_put_policy_without_a_budget_key_still_works(client):
    """Backward compatibility: an older UI build PUTs no budget."""
    body = {"loops": {}, "default": {"attempts": 3, "wall_clock_s": 3600}}
    assert client.put("/api/policy", json=body).status_code == 200


def test_saving_the_policy_preserves_the_budget_block(client, templates_dir):
    """A save from the policy screen must not silently erase a block it does not edit."""
    (templates_dir / "policy.yaml").write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
        "budget:\n  work_item_usd: 20.0\n  daily_usd: null\n"
    )
    body = client.get("/api/policy").json()
    assert body["budget"]["work_item_usd"] == 20.0
    body["default"]["attempts"] = 5
    assert client.put("/api/policy", json=body).status_code == 200
    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert on_disk["budget"]["work_item_usd"] == 20.0


def test_get_policy_reports_max_concurrent_default(client):
    assert client.get("/api/policy").json()["max_concurrent"] == 3


def test_put_policy_persists_max_concurrent(client, templates_dir):
    body = client.get("/api/policy").json()
    body["max_concurrent"] = 5
    saved = client.put("/api/policy", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["max_concurrent"] == 5
    assert yaml.safe_load((templates_dir / "policy.yaml").read_text())["max_concurrent"] == 5
    assert client.get("/api/policy").json()["max_concurrent"] == 5


def test_put_policy_rejects_bad_max_concurrent(client):
    body = client.get("/api/policy").json()
    body["max_concurrent"] = 0
    assert client.put("/api/policy", json=body).status_code == 422
