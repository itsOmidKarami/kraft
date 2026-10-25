"""`runner` as an item setting: where it is accepted, and what it refuses."""

import pytest
import yaml
from support.api import _poll_node_started, _post_default
from support.harness import connect_repo

from kraft.remote import registry


def _machines(client, **machines):
    client.app.state.workers_hub.apply(
        registry.WorkersFile.model_validate(
            {
                "workers": {
                    n: {"token_sha256": registry.hash_token(f"kwt_{n}"), **kw}
                    for n, kw in machines.items()
                }
            }
        )
    )


def _file(client, repo, **body):
    return client.post("/api/work-items", json={"title": "t", "repo": str(repo), **body})


def _first_node(client) -> str:
    return client.app.state.library.resolve_chain("default").nodes[0].id


def _overrides(client, wid):
    return client.get(f"/api/work-items/{wid}").json()["agent_overrides"]


def test_a_known_machine_is_accepted_at_filing_and_kept_as_the_items_override(client, repo):
    _machines(client, gpu={})
    reply = _file(client, repo, runner="gpu")
    assert reply.status_code == 201, reply.text
    assert _overrides(client, reply.json()["id"]) == {"runner": "gpu"}


def test_a_group_is_accepted_wherever_a_machine_is_and_kept_as_given(client, repo):
    _machines(client, gpu={"groups": ["farm"]})
    reply = _file(client, repo, runner="farm")
    assert reply.status_code == 201, reply.text
    wid = reply.json()["id"]
    assert _overrides(client, wid) == {"runner": "farm"}
    node = _first_node(client)
    by_node = client.patch(
        f"/api/work-items/{wid}", json={"node_overrides": {node: {"runner": "farm"}}}
    )
    assert by_node.status_code == 200, by_node.text
    whole = client.patch(f"/api/work-items/{wid}", json={"agent_overrides": {"runner": "farm"}})
    assert whole.status_code == 200, whole.text


@pytest.mark.parametrize(
    "sandbox",
    [
        {"kind": "docker", "image": "img"},
        {"kind": "os", "network": {"runtime": {"allow": ["api.example.com"]}}},
    ],
    ids=["docker", "os"],
)
def test_a_sandboxed_item_may_be_placed_on_a_machine_which_builds_its_sandbox(
    client, repo, sandbox
):
    _machines(client, gpu={})
    connect_repo(repo, sandbox=sandbox)
    reply = _file(client, repo, runner="gpu", autostart=False)
    assert reply.status_code == 201, reply.text
    assert _overrides(client, reply.json()["id"]) == {"runner": "gpu"}


@pytest.mark.parametrize(
    ("body", "says"),
    [
        ({"runner": "nope"}, "'nope' is not a machine or a group in workers.yaml"),
        (
            {"runner": "Bad Name"},
            "'runner' must be local, any, a machine or a group from workers.yaml",
        ),
        ({"node_overrides": "FIRST"}, "'nope' is not a machine or a group in workers.yaml"),
    ],
    ids=["unknown-machine", "not-a-name", "unknown-machine-in-a-node-override"],
)
def test_a_placement_that_cannot_work_is_a_422_that_says_why(client, repo, body, says):
    _machines(client, gpu={})
    if body.get("node_overrides") == "FIRST":
        body = {"node_overrides": {_first_node(client): {"runner": "nope"}}}
    reply = _file(client, repo, **body)
    assert reply.status_code == 422 and says in reply.json()["detail"]


def test_a_machine_that_excludes_the_repo_is_a_422(client, repo):
    _machines(client, gpu={"repos": ["somewhere-else"]})
    reply = _file(client, repo, runner="gpu")
    assert reply.status_code == 422 and "not allowed for repo" in reply.json()["detail"]


def test_a_runner_change_is_allowed_on_a_started_node_and_other_changes_are_not(client, repo):
    _machines(client, gpu={})
    wid = _post_default(client, repo)
    node = _first_node(client)
    _poll_node_started(client, wid, node)  # the first node has started
    ok = client.patch(f"/api/work-items/{wid}", json={"node_overrides": {node: {"runner": "gpu"}}})
    assert ok.status_code == 200, ok.text
    locked = client.patch(
        f"/api/work-items/{wid}", json={"node_overrides": {node: {"effort": "low"}}}
    )
    assert locked.status_code == 409


def test_patching_the_whole_item_to_an_unknown_machine_is_a_422(client, repo):
    wid = _file(client, repo).json()["id"]
    reply = client.patch(f"/api/work-items/{wid}", json={"agent_overrides": {"runner": "nope"}})
    assert reply.status_code == 422 and "'nope' is not a machine" in reply.json()["detail"]


def test_lint_names_a_chain_whose_runner_is_not_a_machine(client, templates_dir):
    (templates_dir / "chains" / "placed.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "placed",
                "runner": "ghost",
                "nodes": [
                    {
                        "id": "run",
                        "kind": "exec",
                        "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}],
                    }
                ],
            }
        )
    )
    issues = client.get("/api/templates/lint").json()["issues"]
    (issue,) = [i for i in issues if i["chain"] == "placed"]
    assert "'ghost' is not a machine or a group in workers.yaml" in issue["message"]


def test_lint_accepts_a_chain_placed_on_a_group(client, templates_dir):
    (templates_dir / "workers.yaml").write_text(
        f"workers:\n  gpu:\n    token_sha256: {registry.hash_token('kwt_x')}\n    groups: [farm]\n"
    )
    (templates_dir / "chains" / "placed.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "placed",
                "runner": "farm",
                "nodes": [
                    {
                        "id": "run",
                        "kind": "exec",
                        "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}],
                    }
                ],
            }
        )
    )
    issues = client.get("/api/templates/lint").json()["issues"]
    assert [i for i in issues if i["chain"] == "placed"] == []


@pytest.mark.parametrize(
    ("sent", "stored"),
    [({"effort": "low"}, {"effort": "low", "runner": "gpu"}), ({}, {})],
    ids=["a-replacement-without-runner-keeps-it", "the-clear-clears-it"],
)
def test_a_patch_that_does_not_name_the_runner_keeps_the_stored_one(client, repo, sent, stored):
    _machines(client, gpu={})
    wid = _file(client, repo, runner="gpu").json()["id"]
    reply = client.patch(f"/api/work-items/{wid}", json={"agent_overrides": sent})
    assert reply.status_code == 200, reply.text
    assert (_overrides(client, wid) or {}) == stored
