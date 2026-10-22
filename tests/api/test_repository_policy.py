"""The repository policy layer (Kraft-jzv1l, Ruling 152).

`repository-policy-cannot-relax-instance-safety`: a repository's own
`policy:` sits after the instance layer and before the work item's, may only
tighten safety, and is frozen into every item filed in that repository.
Ruling 105 puts the entry's `deny_tools` and `sandbox` in that layer too.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from support.api import _force_node
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
    assert entry.model_dump()["policy"] == {"allowed_tools": ["Read"]}


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


@pytest.mark.api_client(edit_templates=_two_chains)
def test_a_sandboxed_items_env_names_survive_a_repos_yaml_edit_and_a_switch(client, repo):
    """Kraft-8g7fp: the env names a sandboxed item passes in are its
    repository's at filing, names only, kept across a template switch; a
    name added to the entry later is not among them."""
    templates = client.app.state.templates_dir
    _connect(templates, repo, sandbox=_SANDBOX, env={"CI": "1"}, env_passthrough=["NPM_TOKEN"])
    wid = _file(client, repo, chain_template="one").json()["id"]
    _connect(
        templates, repo, sandbox=_SANDBOX, env={"CI": "1", "LATE": "x"}, env_passthrough=["AWS"]
    )

    switched = client.patch(f"/api/work-items/{wid}", json={"chain_template": "two"})

    assert switched.status_code == 200, switched.text
    snapshot = _snapshot(client, wid)
    assert snapshot["chain"]["id"] == "two"
    assert snapshot["repository_env_names"] == {str(repo): ["CI", "NPM_TOKEN"]}
    assert {"api.anthropic.com"} <= set(snapshot["harness_egress"]["claude"]["network_requires"])


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


# ── a person's retry re-reads the sandbox (Kraft-ajzx7) ──

_EDITED = {**_SANDBOX, "image": "kraft/worker:2"}


@pytest.fixture
def no_walk(monkeypatch):
    """A retry that walks nothing: the walk is not what these tests are about."""
    from kraft import executor

    async def walk(*_args, **_kwargs):
        return "completed"

    monkeypatch.setattr(executor, "retry", walk)


@pytest.fixture
def stopped(client, repo, no_walk):
    """An item filed sandboxed, stopped at `verification`."""
    _connect(client.app.state.templates_dir, repo, sandbox=_SANDBOX)
    wid = _file(client, repo, chain_template="default").json()["id"]
    _force_node(wid, "verification", "needs_human")
    return wid


def _rereads(client, wid) -> list[dict]:
    events = client.get(f"/api/work-items/{wid}/events").json()
    return [e["payload"] for e in events if e["type"] == "sandbox_reread"]


def _without_sandbox(snapshot: dict) -> dict:
    return {**snapshot, "policy": {**snapshot["policy"], "sandbox": None}}


def _row(wid, run_chain_from_intake=False) -> sqlite3.Row:
    with closing(sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")) as c, c:
        c.row_factory = sqlite3.Row
        if run_chain_from_intake:  # as an earlier retry's fork leaves it
            c.execute("UPDATE work_items SET run_chain = materialized_chain WHERE id = ?", (wid,))
        return c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()


_FLAG = {"sandbox_from_config": True}


@pytest.mark.parametrize(
    ("edit", "body", "forked", "runs_in", "recorded"),
    [
        (_EDITED, {}, False, _SANDBOX, []),
        (_EDITED, _FLAG, False, _EDITED, [{"old": _SANDBOX, "new": _EDITED}]),
        (_EDITED, _FLAG, True, _EDITED, [{"old": _SANDBOX, "new": _EDITED}]),
        (_SANDBOX, _FLAG, False, _SANDBOX, []),
    ],
    ids=[
        "plain-retry-keeps-the-frozen-one",
        "flag-takes-the-edit",
        "flag-rewrites-the-run-fork-too",
        "flag-with-no-change",
    ],
)
def test_a_persons_retry_can_take_the_sandbox_repos_yaml_sets_now(
    client, repo, stopped, edit, body, forked, runs_in, recorded
):
    """Ruling 105 holds for a plain retry; `sandbox_from_config` replaces
    only the frozen sandbox, in the intake snapshot and the run fork's
    chain, records both, and records nothing when the two are the same."""
    from kraft.executor import dispatch

    _row(stopped, run_chain_from_intake=forked)
    before = _snapshot(client, stopped)
    _connect(client.app.state.templates_dir, repo, sandbox=edit)
    shown = client.get(f"/api/work-items/{stopped}/sandbox-from-config").json()
    assert (shown["old"], shown["new"]) == (_SANDBOX, edit)
    assert _snapshot(client, stopped) == before  # the preview `--yes` gates changes nothing

    r = client.post(f"/api/work-items/{stopped}/retry", json=body)

    assert r.status_code == 200, r.text
    row = _row(stopped)
    assert (row["run_chain"] is not None) is forked
    assert dispatch._item_policy(row, None).model_dump() == runs_in
    assert _without_sandbox(_snapshot(client, stopped)) == _without_sandbox(before)
    assert _rereads(client, stopped) == recorded
    if body:
        assert r.json()["sandbox"]["changed"] is bool(recorded)


@pytest.mark.parametrize(
    ("headers", "instance", "status", "detail"),
    [
        ({"X-Kraft-Session-Id": "w-other/abc123"}, None, 403, "not an agent's"),
        ({"X-Kraft-Client": "mcp"}, None, 403, "not an assistant's"),
        ({}, {"allowed_tools": ["Read"]}, 422, "allowed_tools"),
    ],
    ids=["worker", "assistant", "instance-refuses"],
)
def test_a_refused_sandbox_reread_changes_nothing(
    client, repo, stopped, headers, instance, status, detail
):
    before = _snapshot(client, stopped)
    if instance is not None:
        client.app.state.instance_policy = _instance(**instance)
    _connect(
        client.app.state.templates_dir,
        repo,
        sandbox=_EDITED,
        policy={"allowed_tools": ["Read", "Bash"]},
    )

    r = client.post(
        f"/api/work-items/{stopped}/retry", json={"sandbox_from_config": True}, headers=headers
    )

    assert r.status_code == status, r.text
    assert detail in r.json()["detail"]
    assert _snapshot(client, stopped) == before
    assert _rereads(client, stopped) == []
    assert client.get(f"/api/work-items/{stopped}").json()["status"] == "needs_human"


def test_a_workspace_retry_rereads_each_members_sandbox(client, tmp_path, no_walk):
    """A workspace item's members are re-read too, each into its own frozen
    policy, and the assembled checkout's with them."""
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    client.post("/api/repos", json={"path": str(root), "enabled": False})
    repos_yaml = client.app.state.templates_dir / "repos.yaml"

    def member_sandbox(sandbox):
        doc = yaml.safe_load(repos_yaml.read_text())
        next(e for e in doc["repos"] if e.get("id") == "a")["sandbox"] = sandbox
        repos_yaml.write_text(yaml.safe_dump(doc))

    member_sandbox(_SANDBOX)
    wid = _file(client, root, workspace="ws", members=["a"]).json()["id"]
    _force_node(wid, "verification", "needs_human")
    member_sandbox(_EDITED)

    r = client.post(f"/api/work-items/{wid}/retry", json=_FLAG)

    assert r.status_code == 200, r.text
    snapshot = _snapshot(client, wid)
    assert snapshot["repository_policies"]["a"]["sandbox"] == _EDITED
    assert snapshot["policy"]["sandbox"] == _EDITED
    assert _rereads(client, wid) == [{"old": _SANDBOX, "new": _EDITED}]
