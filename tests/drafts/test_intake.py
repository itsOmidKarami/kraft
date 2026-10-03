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


def test_set_intake_writes_intake_yaml_whole(client):
    body = resolved(
        client,
        {"op": "set_intake", "patch": {"enabled": True, "interval_s": 90}},
    )
    assert written(client, "intake.yaml") == {
        "enabled": True,
        "interval_s": 90,
        "repos": [],
        "priority_ceiling": 2,
    }
    r = body["resolved"]
    assert (r["enabled"], r["interval_s"], r["priority_ceiling"]) == (True, 90, 2)


@pytest.mark.parametrize(
    "patch",
    [{"cron": "x"}, {"max_concurrent": 4}],
    ids=["a-schedule-field", "policy-yamls-max-concurrent"],
)
def test_set_intake_refuses_a_field_that_is_not_an_intake_setting(client, patch):
    """`max_concurrent` is `policy.yaml`'s, edited on Settings › Policy: this
    area writes one file."""
    assert ops(client, {"op": "set_intake", "patch": patch}).status_code == 422


def test_a_bound_outside_the_range_is_a_problem_not_a_refused_op(client):
    body = resolved(client, {"op": "set_intake", "patch": {"interval_s": 5}})
    assert any("interval_s" in p["message"] for p in body["problems"])
    assert client.post(f"{URL}/publish").status_code == 422


def test_schedule_ops_rewrite_schedules_in_place(client, connected):
    resolved(client, schedule(connected), schedule(connected, title="Second"))
    resolved(client, {"op": "set_schedule", "index": 0, "patch": {"cron": "0 8 * * 1"}})
    body = resolved(client, {"op": "remove_schedule", "index": 1})
    schedules = written(client, "intake.yaml")["schedules"]
    assert schedules == [
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


@pytest.mark.parametrize(
    ("over", "fields"),
    [
        pytest.param(
            {"repo": "/not/connected", "chain": "no-such-chain"},
            {"repo", "chain"},
            id="repo-and-chain",
        ),
        pytest.param({"cron": "61 9 * * 1-5"}, {"cron"}, id="a-cron-the-scheduler-cannot-run"),
    ],
)
def test_a_schedule_naming_no_connected_repo_or_no_chain_is_a_problem(
    client, connected, over, fields
):
    """Each is named once, on its schedule, and the screen still renders the
    file. A cron is checked as the scheduler runs it (R12a-01): `61` was
    published, then never fired."""
    on = {"op": "set_intake", "patch": {"enabled": True}}
    body = resolved(client, on, schedule(**{"repo": connected, **over}))
    by_field = {p["field"]: p for p in body["problems"] if "schedule" in p}
    assert set(by_field) == fields
    assert {p["path"] for p in by_field.values()} == {f"schedules[0].{f}" for f in fields}
    assert len(body["problems"]) == len(fields)
    assert body["resolved"]["schedules"][0]["title"] == "Weekly sweep"
    assert client.post(f"{URL}/publish").status_code == 422

    fixed = {"repo": connected, "chain": "default", "cron": "0 9 * * 1-5"}
    body = resolved(client, {"op": "set_schedule", "index": 0, "patch": fixed})
    assert body["problems"] == []


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
    assert [s["title"] for s in client.app.state.intake["schedules"]] == ["Weekly sweep"]


# ── changes at key level (W15 A.2) ──


def test_an_intake_draft_lists_each_changed_key_and_a_schedule_by_its_index(client, connected):
    body = resolved(
        client,
        {"op": "set_intake", "patch": {"interval_s": 90}},
        schedule(connected),
    )
    by_path = {(c["file"], c["path"]): c for c in body["changes"]}
    assert ("intake.yaml", "interval_s") in by_path
    assert by_path[("intake.yaml", "interval_s")]["summary"].endswith("→ 90")
    assert by_path[("intake.yaml", "schedules.0.cron")]["kind"] == "add"
    assert by_path[("intake.yaml", "schedules.0.title")]["summary"] == "not set → Weekly sweep"
    # No file-level row alongside.
    assert {c["path"] for c in body["changes"]}.isdisjoint({"intake.yaml"})


@pytest.mark.parametrize("reads_back", [True, False], ids=["kept-readable", "refused"])
def test_removing_the_first_commented_schedule_drafts_a_readable_file(
    client, connected, templates_dir, monkeypatch, reads_back
):
    """The comment above the first schedule left its indent before the next
    `- `, and the draft held YAML its user never wrote and Publish refused
    (R13d-01). A change whose rewrite would not read back is refused, naming
    the file, rather than drafted."""
    entry = "  - cron: '0 9 * * 1'\n    repo: %s\n    chain: default\n    title: %s\n"
    (templates_dir / "intake.yaml").write_text(
        "enabled: false\nschedules:\n  # first one\n"
        + entry % (connected, "one")
        + entry % (connected, "two")
    )
    if not reads_back:
        from kraft.drafts import preserve

        def refuse(*_a, **_k):
            raise preserve.RewriteError("it would not parse at line 3")

        monkeypatch.setattr(preserve, "rewrite", refuse)
    r = ops(client, {"op": "remove_schedule", "index": 0})
    if reads_back:
        assert r.status_code == 200, r.text
        assert [s["title"] for s in written(client, "intake.yaml")["schedules"]] == ["two"]
    else:
        assert r.status_code == 422
        assert r.json()["detail"].startswith("intake.yaml: this change can't be written")
