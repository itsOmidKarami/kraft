"""Kraft-dshto (Ruling 180): a sandbox and submodule members are refused
together, at every door.

A sandboxed worker can write its worktree's gitdir, and a submodule's gitdir
lives inside it, so a hook, `core.sshCommand` or filter driver it plants there
runs as the operator the next time host git works in that submodule -- a push
above all. So the pairing is refused at load, at connect, at intake (and on a
retry that would add a sandbox), fails its repository's doctor row, and stops
an item already in flight for a human before any host git touches its
worktree."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
import yaml
from support.harness import entry_of, make_repo_with_submodule, v1_chain, v1_walk
from support.workspace import workspace_target

from kraft import client, config, doctor, events, executor, store
from kraft.api import deps
from kraft.executor import gates, stops
from kraft.executor.context import LaunchContext
from kraft.policy import (
    InstancePolicy,
    InstancePolicyInput,
    PolicyError,
    SandboxPolicy,
    TemplatePolicyOverride,
)
from kraft.templates.models import ResolvedChain
from kraft.templates.retry import RetryOverrideError, validate_retry_override

SANDBOX = {"kind": "docker", "image": "img"}
WHY = "host git runs as you when Kraft pushes the submodule (Kraft-dshto)"
_NODES = [
    {
        "id": "implementation",
        "kind": "exec",
        "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}],
    }
]


def _sandboxed(materialized):
    """`materialized` as an item filed before Ruling 180 froze it: a sandbox on
    the item's policy, which `materialize` would now refuse to build."""
    layer = TemplatePolicyOverride(sandbox=SandboxPolicy(**SANDBOX))
    policy = materialized.policy.apply_template_override(layer)
    return dataclasses.replace(materialized, policy=policy)


def _write(tmp_path, repos, workspaces=None):
    path = tmp_path / "repos.yaml"
    path.write_text(yaml.safe_dump({"repos": repos, "workspaces": workspaces or {}}))
    return path


_WS = {"ws": {"root": "ws", "members": {"a": {"repository": "a", "path": "libs/a"}}}}


@pytest.mark.parametrize(
    ("root", "member", "named"),
    [
        ({"sandbox": SANDBOX}, {}, "'ws'"),
        ({}, {"sandbox": SANDBOX}, "'a'"),
        ({}, {"policy": {"sandbox": SANDBOX}}, "'a'"),
    ],
    ids=["on-the-root", "on-a-member", "in-a-members-policy-block"],
)
def test_a_sandboxed_workspace_with_members_is_refused_at_load(tmp_path, root, member, named):
    """It names the repository that set the sandbox and says why, and it
    raises a `ConfigError` -- the legible 422 every reader already maps it
    to -- rather than loading the workspace with the sandbox dropped."""
    path = _write(
        tmp_path, [{"path": "/ws", "id": "ws", **root}, {"path": "/a", "id": "a", **member}], _WS
    )
    with pytest.raises(config.ConfigError) as exc:
        config.load_workspaces(path)
    assert f"repos.yaml: workspaces.ws: repository {named} sets a sandbox" in str(exc.value)
    assert WHY in str(exc.value)
    # `GET /repos` alone reads it unrefused, so doctor can name the row.
    assert set(config.load_workspaces(path, refuse_sandboxed=False)) == {"ws"}


def test_a_sandbox_without_submodule_members_still_loads(tmp_path):
    """Only the pairing is refused: a sandboxed repository in no workspace, or
    rooting one that mounts nothing, keeps its sandbox."""
    path = _write(
        tmp_path,
        [{"path": "/ws", "id": "ws", "sandbox": SANDBOX}, {"path": "/a", "id": "a"}],
        {"ws": {"root": "ws"}},
    )
    assert set(config.load_workspaces(path)) == {"ws"}
    assert config.load_repos(path)[0].sandbox == SANDBOX


def _repos_yaml(tmp_path):
    return tmp_path / "templates" / "repos.yaml"


def _set_sandbox(tmp_path, path):
    """Hand-edit `sandbox:` onto the entry at `path`: no route writes one."""
    data = yaml.safe_load(_repos_yaml(tmp_path).read_text())
    next(r for r in data["repos"] if r["path"] == str(path))["sandbox"] = SANDBOX
    _repos_yaml(tmp_path).write_text(yaml.safe_dump(data))


def test_connecting_a_root_whose_submodule_is_sandboxed_is_refused(client, tmp_path):
    """Connect declares a workspace mounting each `.gitmodules` child; one
    already connected with a sandbox makes that workspace refusable, so the
    connect is a 422 and nothing is saved."""
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    child = root / "libs" / "a"
    assert client.post("/api/repos", json={"path": str(child)}).status_code == 201
    _set_sandbox(tmp_path, child.resolve())
    before = _repos_yaml(tmp_path).read_text()

    r = client.post("/api/repos", json={"path": str(root), "enabled": False})

    assert r.status_code == 422, r.text
    assert "sets a sandbox" in r.json()["detail"] and WHY in r.json()["detail"]
    assert _repos_yaml(tmp_path).read_text() == before


def _connect_workspace(client, tmp_path):
    """A root with one submodule connected: workspace `ws`, member `a`."""
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    assert client.post("/api/repos", json={"path": str(root), "enabled": False}).status_code == 201
    return root.resolve()


def test_filing_a_workspace_item_once_a_member_is_sandboxed_is_refused(client, tmp_path):
    root = _connect_workspace(client, tmp_path)
    _set_sandbox(tmp_path, root / "libs" / "a")

    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(root), "workspace": "ws", "members": ["a"]},
    )

    assert r.status_code == 422, r.text
    assert "repository 'a' sets a sandbox" in r.json()["detail"]
    assert client.get("/api/work-items").json()["items"] == []


def _base_policy() -> InstancePolicy:
    return InstancePolicy.from_input(InstancePolicyInput.model_validate({}))


def test_materializing_a_sandboxed_chain_onto_submodule_mounts_is_refused():
    """Every snapshot is built by `materialize` -- intake by any route, a
    trigger, a chain template switch -- so a sandbox from any layer, here the
    chain's own policy, is refused there. The same chain on one repository
    materializes."""
    chain = ResolvedChain.from_chain(
        v1_chain(_NODES, repo="/r").chain.chain.model_copy(
            update={"policy": TemplatePolicyOverride(sandbox=SandboxPolicy(**SANDBOX))}
        )
    )
    with pytest.raises(PolicyError) as exc:
        chain.materialize(target=workspace_target({"a": "libs/a"}), effective_policy=_base_policy())
    assert exc.value.field == "sandbox"
    assert "workspace 'ws': the chain sets a sandbox" in str(exc.value)
    assert WHY in str(exc.value)
    assert chain.materialize(
        target=v1_chain(_NODES, repo="/r").target, effective_policy=_base_policy()
    ).policy.sandbox == SandboxPolicy(**SANDBOX)


async def test_intake_refuses_a_task_sandbox_over_submodule_mounts(database, run_dirs):
    """`executor.intake` is every filing door's (HTTP, MCP, a trigger); a
    sandbox a task sets is refused before anything is written."""
    task = {**_NODES[0]["tasks"][0], "policy": {"sandbox": SANDBOX}}
    chain = v1_chain([{**_NODES[0], "tasks": [task]}], repo="/r").chain
    with pytest.raises(PolicyError, match="workspace 'ws': the chain sets a sandbox"):
        await executor.intake(
            database,
            run_dirs,
            title="t",
            repo="/r",
            chain=chain,
            target=workspace_target({"a": "libs/a"}),
        )
    assert database.read(lambda c: c.execute("SELECT id FROM work_items").fetchall()) == []


def test_a_retry_that_adds_a_sandbox_to_a_workspace_task_is_refused():
    materialized = v1_chain(_NODES, repo="/r", target=workspace_target({"a": "libs/a"}))
    with pytest.raises(RetryOverrideError) as exc:
        validate_retry_override(materialized, "implementation.main.t", policy={"sandbox": SANDBOX})
    assert exc.value.field == "policy.sandbox"
    assert "workspace 'ws': the chain sets a sandbox" in str(exc.value)


def test_doctor_fails_the_row_of_a_repository_sandboxing_a_workspace(app, tmp_path):
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    asyncio.run(client.ensure_repo(str(root)))
    _set_sandbox(tmp_path, root.resolve() / "libs" / "a")

    rows = [r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("sandbox ")]

    assert [(r["name"], r["ok"]) for r in rows] == [("sandbox a", False)]
    assert "workspaces.ws: repository 'a' sets a sandbox" in rows[0]["detail"]


def _reason(evts) -> str:
    return next(e for e in reversed(evts) if e["type"] == "work_item_needs_human")["payload"][
        "reason"
    ]


@pytest.mark.parametrize(
    ("chain", "item", "entry"),
    [
        pytest.param(
            _sandboxed(v1_chain(_NODES, repo="/r", target=workspace_target({"a": "libs/a"}))),
            {},
            {},
            id="a-workspace-snapshot-frozen-sandboxed",
        ),
        pytest.param(
            v1_chain(_NODES, repo="/r"),
            {"submodules": ["repos/pkg"], "root_merge_policy": "bump"},
            {"sandbox": SANDBOX},
            id="a-legacy-submodules-column-under-a-live-sandbox",
        ),
    ],
)
async def test_an_in_flight_item_stops_for_a_human_before_its_worktree_is_touched(
    tmp_path, monkeypatch, chain, item, entry
):
    """Kraft-zvqwl's legacy column counts as submodule members too. The stop
    comes before `ensure_worktree`, so no worktree exists and no host git ran
    in one -- the push last of all."""
    pushed = []
    monkeypatch.setattr("kraft.adapters.forge.git.push", lambda *a: pushed.append(a))
    root, _sub = make_repo_with_submodule(tmp_path)

    status, evts, sessions, _row = await v1_walk(
        tmp_path,
        chain,
        repo=root,
        repo_entry=entry_of({"path": str(root), "setup_command": "", **entry}),
        **item,
    )

    assert status == "needs_human"
    assert WHY in _reason(evts)
    assert not (tmp_path / "run" / "worktrees" / "w1").exists()
    assert sessions == [] and pushed == []


async def test_an_unsandboxed_item_with_submodules_is_not_stopped(tmp_path):
    root, _sub = make_repo_with_submodule(tmp_path)
    status, _evts, _sessions, _row = await v1_walk(
        tmp_path,
        v1_chain(_NODES, repo="/r"),
        repo=root,
        repo_entry=entry_of({"path": str(root), "setup_command": ""}),
        submodules=["repos/pkg"],
        root_merge_policy="bump",
    )
    assert status == "completed"


@pytest.fixture
def refreshed(monkeypatch):
    """Every `refresh_worktree_base` a door makes, recorded, never run."""
    calls = []

    async def fake(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr("kraft.builtins.refresh_worktree_base", fake)
    return calls


@pytest.mark.parametrize(
    ("door", "stopped"), [("retry", "needs_human"), ("resume", "paused")], ids=["retry", "resume"]
)
def test_a_door_stops_the_item_before_it_refreshes_the_worktree(
    client, tmp_path, refreshed, door, stopped
):
    """`/retry` and `/resume` rebase the worktree before the walk, which is
    host git in it. A sandbox added to a member after filing binds the item
    live (`dispatch._task_sandbox`), so the door stops it instead."""
    root = _connect_workspace(client, tmp_path)
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(root),
            "workspace": "ws",
            "members": ["a"],
            "autostart": False,
        },
    )
    wid = r.json()["id"]
    from support.api import _force_node

    _force_node(wid, "spec", stopped)
    _set_sandbox(tmp_path, root / "libs" / "a")

    r = client.post(f"/api/work-items/{wid}/{door}", json={})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "needs_human"
    evts = client.get(f"/api/work-items/{wid}/events").json()
    assert WHY in _reason(evts)
    assert refreshed == []


async def test_an_escalations_self_retry_stops_before_it_refreshes_the_worktree(
    item_on, run_dirs, refreshed
):
    it = await item_on(_NODES, "implementation", target=workspace_target({"a": "libs/a"}))
    await it.database.write(
        lambda c: store.mark_needs_human(c, it.id, "implementation", "stuck", stuck=True)
    )
    cursor = it.events()[-1]["seq"]
    request = {"node_id": "implementation", "key": None, "gate_key": None, "steer": None}
    await it.database.write(
        lambda c: events.append(c, it.id, "work_item_self_retry_requested", request)
    )
    launch = LaunchContext(
        repo_entry=entry_of({"path": "/r", "sandbox": SANDBOX}), steering_dir=None
    )

    status = await gates.resume_after_escalation(
        it.database, run_dirs, work_item_id=it.id, cursor=cursor, launch=launch
    )

    assert status == "needs_human"
    assert WHY in _reason(it.events())
    assert refreshed == [] and not it.events("work_item_retried")


def test_the_guard_lets_a_sandboxed_item_without_submodules_through():
    """Only the pairing stops an item: a sandbox on one repository is the
    ordinary sandboxed run."""
    row = {"id": "w1", "materialized_chain": v1_chain(_NODES, repo="/r").to_json()}
    row["submodules"] = None
    launch = LaunchContext(
        repo_entry=entry_of({"path": "/r", "sandbox": SANDBOX}), steering_dir=None
    )
    assert stops.refuse_sandboxed_submodules(row, launch) is None


def test_the_guard_reads_an_unreadable_repos_yaml_as_a_stop_not_as_no_sandbox():
    """A poisoned entry (`deps._PoisonedRepoEntry`) must not read as "no
    sandbox" and wave the item through."""

    row = {"id": "w1", "materialized_chain": v1_chain(_NODES, repo="/r").to_json()}
    row["submodules"] = '["repos/pkg"]'
    poisoned = deps._PoisonedRepoEntry(config.ConfigError("repos.yaml: broken"))
    launch = LaunchContext(repo_entry=poisoned, steering_dir=None)
    with pytest.raises(RuntimeError, match="cannot tell whether w1 runs sandboxed"):
        stops.refuse_sandboxed_submodules(row, launch)
