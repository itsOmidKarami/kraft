"""The `intake` draft area's ops and its resolve (W13 G): the written YAML, the
schedule problems, and that a publish applies without a restart."""

from __future__ import annotations

import pytest
import yaml
from support.harness import connect_repo

URL = "/api/drafts/intake/intake"

pytestmark = pytest.mark.api_client(default_setup=False)


def ops(client, *batch):
    return client.post(f"{URL}/ops", json={"ops": list(batch)})


def resolved(client, *batch):
    r = ops(client, *batch)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def written(client, name):
    return yaml.safe_load(client.get(URL).json()["files"][name])


@pytest.fixture
def connected(client, repo, templates_dir):
    connect_repo(repo, templates_dir, name="r", enabled=False, managed=True)
    return str(repo.resolve())


def schedule(repo, **over):
    return {
        "op": "add_schedule",
        "cron": "0 9 * * 1",
        "repo": repo,
        "chain": "default",
        "title": "Weekly sweep",
        **over,
    }


def test_set_intake_writes_intake_yaml_whole_and_max_concurrent_to_policy(client):
    body = resolved(
        client,
        {"op": "set_intake", "patch": {"enabled": True, "interval_s": 90, "max_concurrent": 2}},
    )
    assert written(client, "intake.yaml") == {
        "enabled": True,
        "interval_s": 90,
        "repos": [],
        "priority_ceiling": 2,
    }
    assert written(client, "policy.yaml")["max_concurrent"] == 2
    r = body["resolved"]
    assert (r["enabled"], r["interval_s"], r["max_concurrent"], r["priority_ceiling"]) == (
        True,
        90,
        2,
        2,
    )


def test_set_intake_refuses_a_field_that_is_not_an_intake_setting(client):
    assert ops(client, {"op": "set_intake", "patch": {"cron": "x"}}).status_code == 422


def test_a_bound_outside_the_range_is_a_problem_not_a_refused_op(client):
    body = resolved(client, {"op": "set_intake", "patch": {"interval_s": 5}})
    assert any("interval_s" in p["message"] for p in body["problems"])
    assert client.post(f"{URL}/publish").status_code == 422


def test_schedule_ops_rewrite_triggers_in_place(client, connected):
    resolved(client, schedule(connected), schedule(connected, title="Second"))
    resolved(client, {"op": "set_schedule", "index": 0, "patch": {"cron": "0 8 * * 1"}})
    body = resolved(client, {"op": "remove_schedule", "index": 1})
    triggers = written(client, "policy.yaml")["triggers"]
    assert triggers == [
        {
            "cron": "0 8 * * 1",
            "repo": connected,
            "chain": "default",
            "title": "Weekly sweep",
            "description": "",
        }
    ]
    assert [s["index"] for s in body["resolved"]["schedules"]] == [0]
    assert ops(client, {"op": "remove_schedule", "index": 4}).status_code == 422
    assert ops(client, {"op": "set_schedule", "index": 0, "patch": {"x": 1}}).status_code == 422


def test_a_schedule_naming_no_connected_repo_or_no_chain_is_a_problem(client, connected):
    body = resolved(client, schedule("/not/connected", chain="no-such-chain"))
    by_field = {p["field"]: p for p in body["problems"] if "schedule" in p}
    assert set(by_field) == {"repo", "chain"}
    assert by_field["chain"]["path"] == "triggers[0].chain"
    assert client.post(f"{URL}/publish").status_code == 422

    body = resolved(client, {"op": "set_schedule", "index": 0, "patch": {"repo": connected}})
    assert {p["field"] for p in body["problems"] if "schedule" in p} == {"chain"}


def test_publish_applies_the_interval_and_schedules_with_one_poller_restart(
    client, connected, templates_dir, monkeypatch
):
    from kraft import intake as intake_mod

    restarts = []

    async def restart(app):
        restarts.append(app.state.intake["interval_s"])

    monkeypatch.setattr(intake_mod, "restart", restart)
    resolved(
        client,
        {"op": "set_intake", "patch": {"enabled": True, "interval_s": 90}},
        schedule(connected),
    )
    assert client.post(f"{URL}/publish").status_code == 200
    assert restarts == [90]
    assert yaml.safe_load((templates_dir / "intake.yaml").read_text())["interval_s"] == 90
    assert [t.title for t in client.app.state.policy.triggers] == ["Weekly sweep"]


# ── changes at key level (W15 A.2) ──


def test_an_intake_draft_lists_each_changed_key_and_a_schedule_by_its_index(client, connected):
    body = resolved(
        client,
        {"op": "set_intake", "patch": {"interval_s": 90, "max_concurrent": 2}},
        schedule(connected),
    )
    by_path = {(c["file"], c["path"]): c for c in body["changes"]}
    assert ("intake.yaml", "interval_s") in by_path
    assert by_path[("intake.yaml", "interval_s")]["summary"].endswith("→ 90")
    assert by_path[("policy.yaml", "max_concurrent")]["summary"].endswith("→ 2")
    assert by_path[("policy.yaml", "triggers.0.cron")]["kind"] == "add"
    assert by_path[("policy.yaml", "triggers.0.title")]["summary"] == "not set → Weekly sweep"
    # No file-level row alongside.
    assert {c["path"] for c in body["changes"]}.isdisjoint({"intake.yaml", "policy.yaml"})


def test_max_concurrent_is_one_key_whichever_draft_writes_it(client):
    resolved(client, {"op": "set_intake", "patch": {"max_concurrent": 4}})
    assert written(client, "policy.yaml")["max_concurrent"] == 4
