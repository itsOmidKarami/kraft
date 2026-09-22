"""Kraft-p8nem: a sandboxed item's project-controlled commands never run on
the host. Pinned at the launch: the argv and env each command is started
with, and whether it went through `sandbox.docker_argv` or a bare host spawn.
Every command here is benign (`true`, `touch`)."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess

import pytest
from support.harness import entry_of, fake_docker_bin, v1_chain, v1_walk

from kraft import builtins as kraft_builtins
from kraft import config as _config
from kraft import executor
from kraft.api import deps
from kraft.executor import dispatch
from kraft.policy import SandboxPolicy
from kraft.worker.env import worker_env

_SANDBOX = {"kind": "docker", "image": "kraft/setup:1"}
_SETUP = "touch prepared.txt"


def _record_run(monkeypatch) -> list[dict]:
    """Every `subprocess.run` `run_setup_command` makes, recorded, not run."""
    calls: list[dict] = []

    def run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(kraft_builtins.subprocess, "run", run)
    return calls


# -- the launch -----------------------------------------------------------------


async def test_a_sandboxed_setup_command_launches_through_docker(tmp_path, monkeypatch):
    entry = entry_of({"setup_command": _SETUP, "env": {"SETUP_FLAVOUR": "benign"}})
    calls = _record_run(monkeypatch)

    await kraft_builtins.run_setup_command(tmp_path, tmp_path, entry, sandbox=_SANDBOX)

    [call] = calls
    argv = call["args"]
    assert argv[:2] == ["docker", "run"]
    assert argv[-4:] == [_SANDBOX["image"], "sh", "-c", _SETUP]
    assert "-e" in argv and "SETUP_FLAVOUR=benign" in argv
    assert not call.get("shell"), "a sandboxed setup went to a host shell"
    assert call["cwd"] == tmp_path
    assert call["env"] == worker_env(entry)


async def test_an_unsandboxed_setup_command_still_runs_on_the_host(tmp_path, monkeypatch):
    entry = entry_of({"setup_command": _SETUP})
    calls = _record_run(monkeypatch)

    await kraft_builtins.run_setup_command(tmp_path, tmp_path, entry)

    assert calls == [
        {
            "args": _SETUP,
            "shell": True,
            "cwd": tmp_path,
            "env": worker_env(entry),
            "capture_output": True,
            "text": True,
        }
    ]


async def test_a_sandboxed_setup_command_really_runs_in_the_container(tmp_path, monkeypatch):
    """The fake `docker` unwraps back to the command, and touches a sentinel
    only it touches: the file alone would appear on a host run too."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}:{os.environ['PATH']}")
    called = tmp_path / "docker-was-called"
    monkeypatch.setenv("FAKE_DOCKER_CALLED", str(called))
    worktree = tmp_path / "wt"
    worktree.mkdir()

    await kraft_builtins.run_setup_command(
        worktree,
        tmp_path,
        entry_of({"setup_command": _SETUP, "env_passthrough": ["FAKE_DOCKER_CALLED"]}),
        sandbox=_SANDBOX,
    )

    assert called.exists()
    assert (worktree / "prepared.txt").exists()


def _without_docker(tmp_path, monkeypatch, *tools: str) -> list[dict]:
    """PATH holds `touch` -- so a host fallback of the setup *would* succeed
    and be seen -- plus `tools`, and no `docker`. Returns every
    `subprocess.run` made from here on, recorded and then really run
    (Kraft-g44n3: a PATH that broke the fallback too proved nothing)."""
    bin_dir = tmp_path / "no-docker-bin"
    bin_dir.mkdir()
    for tool in ("touch", *tools):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    monkeypatch.setenv("PATH", str(bin_dir))
    real = subprocess.run
    spawned: list[dict] = []

    def run(args, **kwargs):
        spawned.append({"args": args, "shell": kwargs.get("shell", False)})
        return real(args, **kwargs)

    monkeypatch.setattr(kraft_builtins.subprocess, "run", run)
    return spawned


def _host_setups(spawned: list[dict]) -> list[dict]:
    """The spawns that ran the setup on the host, not through docker."""
    return [s for s in spawned if s["shell"] or s["args"] == _SETUP]


async def test_no_docker_means_no_setup_never_a_host_fallback(tmp_path, monkeypatch):
    spawned = _without_docker(tmp_path, monkeypatch)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    with pytest.raises(RuntimeError, match="must run in its sandbox.*'docker'"):
        await kraft_builtins.run_setup_command(
            worktree, tmp_path, entry_of({"setup_command": _SETUP}), sandbox=_SANDBOX
        )

    assert [s["args"][0] for s in spawned] == ["docker"]
    assert _host_setups(spawned) == []
    assert not (worktree / "prepared.txt").exists()


# -- which items are sandboxed ------------------------------------------------------


def _agent(**fields):
    return {"id": "implement", "kind": "agent", "harness": "fake", "prompt": "Do it.", **fields}


def _item_policy_sandboxed(repo):
    snapshot = v1_chain([{"id": "work", "kind": "exec", "tasks": [_agent()]}], repo=repo)
    policy = dataclasses.replace(snapshot.policy, sandbox=SandboxPolicy(**_SANDBOX))
    return dataclasses.replace(snapshot, policy=policy)


@pytest.mark.parametrize(
    ("chain", "entry", "expected"),
    [
        ([{"id": "work", "kind": "exec", "tasks": [_agent()]}], {}, None),
        ([{"id": "work", "kind": "exec", "tasks": [_agent()]}], {"sandbox": _SANDBOX}, _SANDBOX),
        ([{"id": "work", "kind": "exec", "tasks": [_agent()]}], {"sandbox": False}, None),
        (
            [{"id": "work", "kind": "exec", "tasks": [_agent(policy={"sandbox": _SANDBOX})]}],
            {},
            _SANDBOX,
        ),
        (
            [{"id": "work", "kind": "exec", "tasks": [_agent(policy={"sandbox": _SANDBOX})]}],
            {"sandbox": False},
            _SANDBOX,
        ),
        ("item-policy", {"sandbox": {"kind": "docker", "image": "since/changed:2"}}, _SANDBOX),
        (
            [{"id": "work", "kind": "exec", "tasks": [_agent(policy={"sandbox": _SANDBOX})]}],
            {"sandbox": {"kind": "docker", "image": "since/changed:2"}},
            _SANDBOX,
        ),
    ],
    ids=[
        "nothing",
        "live-entry",
        "entry-says-off",
        "one-task-froze-one",
        "one-task-and-entry-says-off",
        "item-policy-beats-a-changed-entry",
        "a-tasks-beats-a-changed-entry",
    ],
)
async def test_the_item_sandbox_is_whatever_sandboxes_any_of_it(
    item_on, repo, chain, entry, expected
):
    it = await item_on(_item_policy_sandboxed(repo) if chain == "item-policy" else chain)
    launch = executor.LaunchContext(
        repo_entry=entry_of({"setup_command": "", **entry}), steering_dir=None
    )

    assert dispatch.item_sandbox(it.row(), launch) == expected


async def test_an_unreadable_repos_yaml_is_never_read_as_no_sandbox(item_on):
    it = await item_on([{"id": "work", "kind": "exec", "tasks": [_agent()]}])
    poisoned = deps._PoisonedRepoEntry(_config.ConfigError("repos.yaml: broken"))
    launch = executor.LaunchContext(repo_entry=poisoned, steering_dir=None)

    with pytest.raises(RuntimeError, match="cannot tell whether .* runs sandboxed"):
        dispatch.item_sandbox(it.row(), launch)


# -- the walk hands it to both setup runs -------------------------------------------


_SUBPROCESS = [
    {"id": "work", "kind": "exec", "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}]}
]


@pytest.mark.parametrize(("entry", "expected"), [({"sandbox": _SANDBOX}, _SANDBOX), ({}, None)])
async def test_the_walk_runs_both_setups_in_the_items_sandbox(
    tmp_path, repo, monkeypatch, entry, expected
):
    """`ensure_worktree` at creation and `prepare_runtime` at walk entry: each
    a `run_setup_command`, each handed the item's sandbox."""
    seen = []

    async def run_setup_command(worktree, repo, repo_entry, *, sandbox=None):
        seen.append(sandbox)
        return ""

    async def run_task(*_a, **_kw):
        return "done"

    monkeypatch.setattr(kraft_builtins, "run_setup_command", run_setup_command)
    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)

    status, *_ = await v1_walk(
        tmp_path,
        v1_chain(_SUBPROCESS, repo=repo),
        repo=repo,
        repo_entry=entry_of({"setup_command": _SETUP, **entry}),
    )

    assert status == "completed"
    assert seen == [expected, expected]


async def test_a_sandboxed_item_without_docker_stops_for_a_human(tmp_path, repo, monkeypatch):
    """`git` and `touch` on PATH, no `docker`: the walk's own git works, the
    setup's docker does not, the setup never reaches a host shell, and the
    item stops naming why."""
    spawned = _without_docker(tmp_path, monkeypatch, "git")

    status, evts, _sessions, _row = await v1_walk(
        tmp_path,
        v1_chain(_SUBPROCESS, repo=repo),
        repo=repo,
        repo_entry=entry_of({"setup_command": _SETUP, "sandbox": _SANDBOX}),
    )

    assert status == "needs_human"
    assert any("must run in its sandbox" in json.dumps(e["payload"]) for e in evts)
    assert any(s["args"][0] == "docker" for s in spawned)
    assert _host_setups(spawned) == []


# -- test scopes and area setups --------------------------------------------------


@pytest.mark.parametrize(("entry", "expected"), [({"sandbox": _SANDBOX}, _SANDBOX), ({}, None)])
async def test_test_scopes_and_their_area_setup_launch_in_the_items_sandbox(
    item_on, monkeypatch, entry, expected
):
    launched = []

    async def run_task(*_a, cmd, sandbox=None, **_kw):
        launched.append((cmd, sandbox))
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    builtin = {"id": "t", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"}
    it = await item_on([{"id": "verify", "kind": "exec", "tasks": [builtin]}])
    repo_entry = entry_of(
        {
            "setup_command": "",
            "test_scopes": [{"paths": ["**"], "command": "true root"}],
            "areas": {
                "ui": {
                    "paths": ["**"],
                    "setup": "true ui-setup",
                    "verification": {"test_scopes": [{"paths": ["**"], "command": "true ui"}]},
                }
            },
            **entry,
        }
    )
    node = it.chain.chain.nodes[0]

    status = await dispatch.dispatch_node(
        it.database,
        it.run_dirs,
        node.steps[0].tasks[0],
        node,
        it.row(),
        it.repo,
        launch=executor.LaunchContext(repo_entry=repo_entry, steering_dir=None),
    )

    assert status == "done"
    assert launched == [
        (["true", "root"], expected),
        (["true", "ui-setup"], expected),
        (["true", "ui"], expected),
    ]


async def test_repositories_with_different_live_sandboxes_stop_rather_than_pick_one(item_on):
    it = await item_on([{"id": "work", "kind": "exec", "tasks": [_agent()]}])
    launch = executor.LaunchContext(
        repo_entry=entry_of({"setup_command": "", "sandbox": _SANDBOX}),
        steering_dir=None,
        repositories={"pkg": entry_of({"sandbox": {"kind": "docker", "image": "member:2"}})},
    )

    with pytest.raises(RuntimeError, match="different sandboxes"):
        dispatch.item_sandbox(it.row(), launch)


async def test_a_members_live_sandbox_wraps_the_item(item_on):
    it = await item_on([{"id": "work", "kind": "exec", "tasks": [_agent()]}])
    launch = executor.LaunchContext(
        repo_entry=entry_of({"setup_command": ""}),
        steering_dir=None,
        repositories={"pkg": entry_of({"sandbox": _SANDBOX})},
    )

    assert dispatch.item_sandbox(it.row(), launch) == _SANDBOX
