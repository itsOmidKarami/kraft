"""A work item's own policy override at the doors that set it (Kraft-ab1bh):
`POST /work-items` (behind `kraft item create --policy` and the MCP
`create_work_item`) and `PATCH /work-items/{id}` (behind `kraft item
set-policy` and the MCP `set_work_item_policy`). What the layer does once set
is tests/templates/test_item_policy.py, tests/test_waits.py and
tests/executor/test_policy_enforcement.py."""

from __future__ import annotations

import pytest

from kraft.policy import InstancePolicy, InstancePolicyInput

_CI = "merge_request_feedback.ci.await_ci"


@pytest.fixture
def bounded(client):
    """The client, on an instance whose `maxima:` bound the operational fields
    (the wait maximum a week, as the seeded approval wait needs)."""
    client.app.state.instance_policy = InstancePolicy.from_input(
        InstancePolicyInput.model_validate(
            {"maxima": {"max_attempts": 5, "timeout_minutes": 120, "wait_timeout_minutes": 10080}}
        )
    )
    return client


def _file(client, repo, **body):
    return client.post(
        "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False, **body}
    )


def _stored(client, wid):
    return client.get(f"/api/work-items/{wid}").json()["policy_override"]


def _set_status(client, wid, status):
    db = client.app.state.db

    async def write():
        await db.write(
            lambda c: c.execute("UPDATE work_items SET status = ? WHERE id = ?", (status, wid))
        )

    client.portal.call(write)


def test_intake_and_a_patch_set_the_items_own_override(bounded, repo):
    """Filed with one, replaced by a PATCH while the item waits, cleared by
    `{}`. Each change is on the item's timeline."""
    override = {"max_attempts": 4, "paths": {_CI: {"wait_timeout_minutes": 240}}}
    filed = _file(bounded, repo, policy=override)
    assert filed.status_code == 201, filed.text
    wid = filed.json()["id"]
    assert _stored(bounded, wid) == override

    _set_status(bounded, wid, "waiting")
    replaced = bounded.patch(
        f"/api/work-items/{wid}", json={"policy": {"paths": {"verification": {"max_attempts": 5}}}}
    )
    assert replaced.status_code == 200, replaced.text
    assert _stored(bounded, wid) == {"paths": {"verification": {"max_attempts": 5}}}

    assert bounded.patch(f"/api/work-items/{wid}", json={"policy": {}}).status_code == 200
    assert _stored(bounded, wid) is None
    changes = [
        e["payload"]["policy"]
        for e in bounded.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == "policy_override_changed"
    ]
    assert changes == [{"paths": {"verification": {"max_attempts": 5}}}, {}]


_REFUSALS = [
    ({"max_attempts": 6}, "policy.max_attempts"),
    ({"paths": {_CI: {"wait_timeout_minutes": 10081}}}, f"policy.paths.{_CI}.wait_timeout_minutes"),
    ({"paths": {"verification": {"kind": "gate"}}}, "policy.paths.verification.kind"),
    ({"paths": {"nowhere": {"max_attempts": 2}}}, "policy.paths.nowhere"),
]
_REFUSAL_IDS = ["attempts-over-maximum", "wait-over-maximum", "structural", "unknown-path"]


@pytest.mark.parametrize(("override", "field"), _REFUSALS, ids=_REFUSAL_IDS)
def test_intake_refuses_an_override_naming_the_field(bounded, repo, override, field):
    refused = _file(bounded, repo, policy=override)

    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"].startswith(f"{field}: ")
    assert bounded.get("/api/work-items").json()["items"] == []


@pytest.mark.parametrize(("override", "field"), _REFUSALS, ids=_REFUSAL_IDS)
def test_a_patch_refuses_an_override_naming_the_field_and_keeps_the_old_one(
    bounded, repo, override, field
):
    wid = _file(bounded, repo, policy={"max_attempts": 2}).json()["id"]

    refused = bounded.patch(f"/api/work-items/{wid}", json={"policy": override})

    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"].startswith(f"{field}: ")
    assert _stored(bounded, wid) == {"max_attempts": 2}


def test_an_ended_item_takes_no_override(bounded, repo):
    wid = _file(bounded, repo).json()["id"]
    _set_status(bounded, wid, "completed")

    refused = bounded.patch(f"/api/work-items/{wid}", json={"policy": {"max_attempts": 2}})

    assert refused.status_code == 409, refused.text
    assert _stored(bounded, wid) is None


def _two_chains(templates_dir):
    task = "{id: run, kind: agent, harness: fake, prompt: go}"
    for id, node in (("one", "work"), ("two", "other")):
        (templates_dir / "chains" / f"{id}.yaml").write_text(
            f"id: {id}\nnodes: [{{id: {node}, kind: exec, tasks: [{task}]}}]\n"
        )


@pytest.mark.api_client(edit_templates=_two_chains)
def test_a_chain_switch_rechecks_the_stored_override(client, repo):
    """The override is checked against the chain the item will run: a switch
    onto a chain that has no node its override names is refused, not left
    binding nothing."""
    wid = _file(
        client, repo, chain_template="one", policy={"paths": {"work": {"max_attempts": 2}}}
    ).json()["id"]

    refused = client.patch(f"/api/work-items/{wid}", json={"chain_template": "two"})

    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"].startswith("policy.paths.work: ")
    assert client.get(f"/api/work-items/{wid}").json()["chain_template"] == "one"
