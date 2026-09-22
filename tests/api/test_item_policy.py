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
            {
                "maxima": {
                    "max_attempts": 5,
                    "timeout_minutes": 120,
                    "total_time_cap_minutes": 10080,
                }
            }
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
    override = {"max_attempts": 4, "paths": {_CI: {"total_time_cap_minutes": 60}}}
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
    # A wait's cap is its timeout (Ruling 196), and an item's only tightens it.
    (
        {"paths": {_CI: {"total_time_cap_minutes": 240}}},
        f"policy.paths.{_CI}.total_time_cap_minutes",
    ),
    ({"paths": {"verification": {"kind": "gate"}}}, "policy.paths.verification.kind"),
    ({"paths": {"nowhere": {"max_attempts": 2}}}, "policy.paths.nowhere"),
    (
        {"paths": {_CI: {"wait_timeout_minutes": 30}}},
        f"policy.paths.{_CI}.wait_timeout_minutes",
    ),
]
_REFUSAL_IDS = [
    "attempts-over-maximum",
    "wait-cap-raised",
    "structural",
    "unknown-path",
    "retired-wait-timeout",
]


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


@pytest.mark.parametrize(
    ("fields", "named"),
    [({"attempts": 6}, "max_attempts 5"), ({"wall_clock_s": 120 * 60 + 1}, "timeout_minutes 120")],
    ids=["attempts", "wall-clock"],
)
@pytest.mark.parametrize("door", ["intake", "patch"])
def test_the_older_node_override_door_is_held_to_the_same_maxima(
    bounded, repo, door, fields, named
):
    """Kraft-3br6j: `node_overrides`' `attempts`/`wall_clock_s` bound the
    same fix loop, so they answer to the same administrator maximum."""
    override = {"verification": fields}
    if door == "intake":
        refused = _file(bounded, repo, node_overrides=override)
    else:
        wid = _file(bounded, repo).json()["id"]
        refused = bounded.patch(f"/api/work-items/{wid}", json={"node_overrides": override})

    assert refused.status_code == 422, refused.text
    assert named in refused.json()["detail"]


def test_an_override_stored_before_tool_names_were_checked_still_reads(bounded, repo):
    """Kraft-9ct4q's rule for anything Kraft froze: an item whose override was
    stored before rule syntax was refused still loads, and the refusal
    happens at launch instead. Writing one now is refused."""
    wid = _file(bounded, repo).json()["id"]
    db = bounded.app.state.db
    stored = '{"deny_tools": ["Bash(git *)"]}'

    async def write():
        await db.write(
            lambda c: c.execute(
                "UPDATE work_items SET policy_override = ? WHERE id = ?", (stored, wid)
            )
        )

    bounded.portal.call(write)

    assert _stored(bounded, wid) == {"deny_tools": ["Bash(git *)"]}
    refused = bounded.patch(
        f"/api/work-items/{wid}", json={"policy": {"deny_tools": ["Bash(git *)"]}}
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"].startswith("policy.deny_tools: ")


def test_the_server_runs_the_time_cap_poller(client):
    """A parked item's total cap and a gate's own timeout run out while
    nothing of the item runs, so the lifespan starts `caps.poller` beside the
    wait scheduler."""
    assert client.app.state.caps_task is not None
    assert not client.app.state.caps_task.done()
