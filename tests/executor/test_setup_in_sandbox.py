"""Kraft-p8nem: a sandboxed item's project-controlled commands never run on
the host. Pinned at the launch: the argv and env each command is started
with, and whether it went through `docker_argv` or a bare host spawn.
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
from kraft.paths import RunDirs
from kraft.policy import SandboxPolicy
from kraft.worker import ca, refstore
from kraft.worker.backends import docker as docker_backend
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
    # By name: the value is in the client's env (below), never on argv.
    assert argv[argv.index("SETUP_FLAVOUR") - 1] == "-e"
    assert not [a for a in argv if "benign" in a]
    assert not call.get("shell"), "a sandboxed setup went to a host shell"
    # The client runs in a directory of Kraft's, never the worktree: podman
    # leaves an `oom` file where its client runs.
    name = argv[argv.index("--name") + 1]
    assert call["cwd"] == docker_backend.client_dir(name.removeprefix("kraft-"))
    assert call["env"] == worker_env(entry)


async def test_a_setup_commands_managed_credentials_cross_as_sentinels(tmp_path, monkeypatch):
    """Spec §6 in the install phase: each listed variable holds a sentinel
    (no harness says more), the docker client holds no value, the bundle
    trusts the Kraft CA, and a repository's own credential is injected
    where `install` names its host."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real-VALUE")
    monkeypatch.setenv("REG_TOKEN", "reg-real-VALUE")
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    opened, bundled = [], []

    async def open_egress(db, backend, sid, wid, sandbox, lists, credentials=()):
        opened.append(credentials)
        return {"HTTPS_PROXY": "http://127.0.0.1:3128"}

    async def close_egress(backend, sid):
        pass

    async def prepare(image, *, kraft_ca=None):
        bundled.append(kraft_ca)

    monkeypatch.setattr(kraft_builtins._subprocess, "open_egress", open_egress)
    monkeypatch.setattr(kraft_builtins._subprocess, "close_egress", close_egress)
    monkeypatch.setattr(docker_backend._forward, "prepare", prepare)
    calls = _record_run(monkeypatch)
    registry = {"domain": "registry.corp", "header": "authorization", "format": "Bearer %s"}
    sandbox = {
        **_SANDBOX,
        "network": {"install": {"allow": ["registry.corp"]}},
        "credentials": [{"env": "ANTHROPIC_API_KEY"}, {"env": "REG_TOKEN", "inject": [registry]}],
    }
    entry = entry_of({"setup_command": _SETUP, "env_passthrough": ["REG_TOKEN"]})

    await kraft_builtins.run_setup_command(tmp_path, tmp_path, entry, sandbox=sandbox)

    [call] = calls
    assert [a for a in call["args"] if a.startswith(("ANTHROPIC_API_KEY", "REG_TOKEN"))] == [
        "ANTHROPIC_API_KEY=kraft-proxy-managed",
        "REG_TOKEN=kraft-proxy-managed",
    ]
    assert not {"ANTHROPIC_API_KEY", "REG_TOKEN"} & set(call["env"])
    [(rule,)] = opened
    assert (rule.domain, rule.carrying(rule.value)) == ("registry.corp", "Bearer reg-real-VALUE")
    assert bundled == [ca.ensure_ca(RunDirs(tmp_path / "run"))[0]]


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


@pytest.mark.parametrize("killed", [True, False], ids=["oom-killed", "exit-137-alone"])
async def test_a_setup_command_runs_under_the_limits_and_names_the_one_that_killed_it(
    tmp_path, monkeypatch, killed
):
    """The sandbox's limits bind the setup command too, and its container
    outlives the command long enough to be asked whether its memory limit
    killed it; then it is removed by name."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}:{os.environ['PATH']}")
    removed = tmp_path / "docker-rm-log"
    monkeypatch.setenv("FAKE_DOCKER_RM_LOG", str(removed))
    answer = tmp_path / "inspect"
    answer.write_text(f"{str(killed).lower()} 33554432\n")
    monkeypatch.setenv("FAKE_DOCKER_INSPECT", str(answer))
    real = subprocess.run
    argvs = []

    def run(args, **kwargs):
        argvs.append(args)
        return real(args, **kwargs)

    monkeypatch.setattr(kraft_builtins.subprocess, "run", run)
    sandbox = {**_SANDBOX, "resources": {"memory": "32m"}}

    with pytest.raises(RuntimeError) as failed:
        await kraft_builtins.run_setup_command(
            tmp_path, tmp_path, entry_of({"setup_command": "exit 137"}), sandbox=sandbox
        )

    [argv] = argvs
    assert "--memory=32m" in argv and "--rm" not in argv
    assert removed.read_text().split() == [argv[argv.index("--name") + 1]]
    named = "a process in the sandbox was killed by its memory limit (32m)" in str(failed.value)
    assert named is killed


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
            [{"id": "work", "kind": "exec", "tasks": [_agent()]}],
            {"policy": {"sandbox": _SANDBOX}},
            _SANDBOX,
        ),
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
        "live-entry-policy-block",
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
        repo_entry=entry_of({"setup_command": "", **entry}),
    )

    assert dispatch.item_sandbox(it.row(), launch) == expected


async def test_an_unreadable_repos_yaml_is_never_read_as_no_sandbox(item_on):
    it = await item_on([{"id": "work", "kind": "exec", "tasks": [_agent()]}])
    poisoned = deps._PoisonedRepoEntry(_config.ConfigError("repos.yaml: broken"))
    launch = executor.LaunchContext(repo_entry=poisoned)

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
        launch=executor.LaunchContext(repo_entry=repo_entry),
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
        repositories={"pkg": entry_of({"sandbox": {"kind": "docker", "image": "member:2"}})},
    )

    with pytest.raises(RuntimeError, match="different sandboxes"):
        dispatch.item_sandbox(it.row(), launch)


async def test_a_members_live_sandbox_wraps_the_item(item_on):
    it = await item_on([{"id": "work", "kind": "exec", "tasks": [_agent()]}])
    launch = executor.LaunchContext(
        repo_entry=entry_of({"setup_command": ""}),
        repositories={"pkg": entry_of({"sandbox": _SANDBOX})},
    )

    assert dispatch.item_sandbox(it.row(), launch) == _SANDBOX


async def test_a_sandboxed_setup_command_mounts_the_ref_store_and_publishes_nothing(
    tmp_path, repo, monkeypatch
):
    """The worktree's HEAD is the worker's to write, so a setup command's
    store names no branch, and nothing it leaves there is ever published."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}:{os.environ['PATH']}")
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "kraft/x", str(wt)], cwd=repo, check=True)
    seen = {}
    real_docker_argv = docker_backend.docker_argv

    def spy(cmd, cwd, sandbox, results_dir, **kw):
        seen.update(kw)
        return real_docker_argv(cmd, cwd, sandbox, results_dir, **kw)

    monkeypatch.setattr(docker_backend, "docker_argv", spy)
    published = []
    monkeypatch.setattr(refstore, "sync", lambda *a, **kw: published.append(a))

    await kraft_builtins.run_setup_command(
        wt,
        repo,
        entry_of({"setup_command": _SETUP, "env_passthrough": ["X_KEY"]}),
        sandbox=_SANDBOX,
    )

    assert seen["refstore"] is not None and seen["refstore"].branch is None
    assert list(seen["passthrough"]) == ["X_KEY"]
    assert published == []
