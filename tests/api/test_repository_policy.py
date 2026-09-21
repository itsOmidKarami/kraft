"""The repository policy layer (Kraft-jzv1l, Ruling 152).

`repository-policy-cannot-relax-instance-safety`: a repository's own
`policy:` sits after the instance layer and before the work item's, may only
tighten safety, and is frozen into every item filed in that repository.
Ruling 105 puts the entry's `deny_tools` and `sandbox` in that layer too.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml
from support.harness import make_repo_with_submodule

from kraft import config
from kraft.api import deps
from kraft.policy import InstancePolicy, InstancePolicyInput, PolicyError
from kraft.templates.environment import WorkItemTarget

_SANDBOX = {"kind": "docker", "image": "kraft/worker:1"}


def _instance(**maxima) -> InstancePolicy:
    return InstancePolicy.from_input(InstancePolicyInput.model_validate({"maxima": maxima}))


def _connect(templates_dir, path, **entry) -> None:
    (templates_dir / "repos.yaml").write_text(
        yaml.safe_dump({"repos": [{"path": str(path), **entry}]})
    )


def test_the_repository_layer_folds_in_the_entrys_own_deny_tools_and_sandbox(tmp_path):
    """Ruling 105: the legacy top-level `deny_tools` and `sandbox` are the
    repository layer's, alongside whatever its `policy:` block sets."""
    _connect(
        tmp_path,
        "/r",
        deny_tools=["WebFetch"],
        sandbox=_SANDBOX,
        policy={"deny_tools": ["Bash"], "allowed_tools": ["Read", "Bash"]},
    )
    st = SimpleNamespace(templates_dir=tmp_path, instance_policy=_instance())

    layered = deps.item_policy(st, "/r")

    assert layered.allowed_tools == ("Read", "Bash")
    assert layered.deny_tools == ("Bash", "WebFetch")
    assert layered.sandbox is not None and layered.sandbox.image == "kraft/worker:1"
    unconnected = deps.item_policy(st, "/elsewhere")
    assert unconnected == st.instance_policy


def test_a_repository_policy_cannot_relax_the_instance(tmp_path):
    _connect(tmp_path, "/r", policy={"allowed_tools": ["Read", "Bash"]})
    st = SimpleNamespace(templates_dir=tmp_path, instance_policy=_instance(allowed_tools=["Read"]))
    with pytest.raises(PolicyError, match=r"repos\.yaml: /r: 'allowed_tools'") as refused:
        deps.item_policy(st, "/r")
    assert refused.value.field == "allowed_tools"


def test_an_unreadable_repos_yaml_refuses_rather_than_drops_the_layer(tmp_path):
    """Not knowing a repository's restrictions is not the absence of any."""
    (tmp_path / "repos.yaml").write_text("repos: [{path: /r, policy: {allowed_tools: git}}]\n")
    st = SimpleNamespace(templates_dir=tmp_path, instance_policy=_instance())
    with pytest.raises(PolicyError, match="repos.yaml"):
        deps.item_policy(st, "/r")


def test_an_entry_cannot_name_two_sandboxes(tmp_path):
    _connect(tmp_path, "/r", sandbox=_SANDBOX, policy={"sandbox": {**_SANDBOX, "image": "x"}})
    with pytest.raises(config.ConfigError, match="sandbox"):
        config.load_repos(tmp_path / "repos.yaml")


def test_an_entrys_policy_block_round_trips_without_nulls(tmp_path):
    """Settings re-saves what `load_repos` returns; an unset policy field must
    not come back as a `null` the operator never wrote."""
    _connect(tmp_path, "/r", policy={"allowed_tools": ["Read"]})
    (entry,) = config.load_repos(tmp_path / "repos.yaml")
    assert entry["policy"] == {"allowed_tools": ["Read"]}


def _snapshot(client, wid) -> dict:
    return json.loads(client.get(f"/api/work-items/{wid}").json()["materialized_chain"])


def _file(client, repo, **body):
    return client.post(
        "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False, **body}
    )


def _two_chains(templates_dir):
    task = "{id: run, kind: agent, harness: fake, prompt: go}"
    node = f"nodes: [{{id: work, kind: exec, tasks: [{task}]}}]\n"
    for id in ("one", "two"):
        (templates_dir / "chains" / f"{id}.yaml").write_text(f"id: {id}\n{node}")


@pytest.mark.api_client(edit_templates=_two_chains)
def test_every_item_filed_in_a_repository_is_bound_by_its_policy(client, repo):
    """The layer is frozen into the snapshot at intake, so every task of every
    chain the item runs resolves under it -- and it survives a template
    switch, which re-materializes from the same layered policy."""
    _connect(client.app.state.templates_dir, repo, deny_tools=["WebFetch"])

    wid = _file(client, repo, chain_template="one").json()["id"]
    assert _snapshot(client, wid)["policy"]["deny_tools"] == ["WebFetch"]

    switched = client.patch(f"/api/work-items/{wid}", json={"chain_template": "two"})
    assert switched.status_code == 200, switched.text
    assert _snapshot(client, wid)["chain"]["id"] == "two"
    assert _snapshot(client, wid)["policy"]["deny_tools"] == ["WebFetch"]


@pytest.mark.parametrize("door", ["/api/work-items", "/api/triggers"])
def test_a_repository_policy_the_instance_refuses_is_a_422_at_intake(client, repo, door):
    client.app.state.instance_policy = _instance(allowed_tools=["Read"])
    _connect(client.app.state.templates_dir, repo, policy={"allowed_tools": ["Read", "Bash"]})
    r = client.post(door, json={"title": "t", "repo": str(repo), "autostart": False})
    assert r.status_code == 422, r.text
    assert "allowed_tools" in r.json()["detail"]


# ── a workspace item: one layer per repository (Kraft-jc39p) ──


def _workspace_repos(templates_dir, *, root_policy, member_policy):
    (templates_dir / "repos.yaml").write_text(
        yaml.safe_dump(
            {
                "repos": [
                    {"path": "/ws", "id": "ws", "policy": root_policy},
                    {"path": "/ws/libs/a", "id": "a", "policy": member_policy},
                ],
                "workspaces": {
                    "ws": {"root": "ws", "members": {"a": {"repository": "a", "path": "libs/a"}}}
                },
            }
        )
    )
    return WorkItemTarget.from_selection(
        config.load_workspaces(templates_dir / "repos.yaml")["ws"], members=["a"]
    )


def test_a_workspace_item_binds_each_repository_by_its_own_layer_and_the_checkout_by_all(
    tmp_path,
):
    """A task fanned out to one repository runs under that repository's layer;
    a task in the assembled checkout, which holds every selected repository at
    once, runs under the tightest of all their layers
    (`repository-policy-cannot-relax-instance-safety`: every task that runs
    in a repository is bound by it)."""
    target = _workspace_repos(
        tmp_path,
        root_policy={"allowed_tools": ["Read", "Bash", "Edit"]},
        member_policy={"allowed_tools": ["Read", "Bash"], "deny_tools": ["WebFetch"]},
    )
    st = SimpleNamespace(templates_dir=tmp_path, instance_policy=_instance())

    assembled = deps.item_policy(st, "/ws", target)
    per_repository = deps.repository_policies(st, target)

    assert (assembled.allowed_tools, assembled.deny_tools) == (("Read", "Bash"), ("WebFetch",))
    assert per_repository["ws"].allowed_tools == ("Read", "Bash", "Edit")
    assert per_repository["ws"].deny_tools == ()
    assert per_repository["a"].deny_tools == ("WebFetch",)


def test_a_member_policy_the_instance_refuses_refuses_the_workspace_item(tmp_path):
    target = _workspace_repos(
        tmp_path, root_policy={}, member_policy={"allowed_tools": ["Read", "Bash"]}
    )
    st = SimpleNamespace(templates_dir=tmp_path, instance_policy=_instance(allowed_tools=["Read"]))
    for resolve in (deps.item_policy, lambda st, _repo, t: deps.repository_policies(st, t)):
        with pytest.raises(PolicyError, match=r"repos\.yaml: /ws/libs/a: 'allowed_tools'"):
            resolve(st, "/ws", target)


def test_a_filed_workspace_item_freezes_each_repositorys_policy(client, tmp_path):
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    client.post("/api/repos", json={"path": str(root), "enabled": False})
    client.patch(f"/api/repos?path={root / 'libs/a'}", json={"deny_tools": ["WebFetch"]})

    wid = _file(client, root, workspace="ws", members=["a"]).json()["id"]

    snapshot = _snapshot(client, wid)
    assert snapshot["repository_policies"]["a"]["deny_tools"] == ["WebFetch"]
    assert snapshot["repository_policies"]["ws"]["deny_tools"] == []
    assert snapshot["policy"]["deny_tools"] == ["WebFetch"]
