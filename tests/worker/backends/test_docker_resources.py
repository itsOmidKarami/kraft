"""`resources` on the docker backend: the limit flags each runtime profile
gets, the refusal where it cannot enforce one, and the OOM kill read back off
the container (`sandbox-limits-never-silently-drop`)."""

import os

import pytest
from support.harness import fake_docker_bin

from kraft.worker.backends import docker

ALL = {"cpu": 2, "memory": "512m", "pids": 100}
IMAGE = {"kind": "docker", "image": "img"}


def _argv_on(monkeypatch, runtime, resources=None, **kw):
    monkeypatch.setattr(docker, "_RUNTIME", runtime)
    sandbox = {**IMAGE, **({"resources": resources} if resources is not None else {})}
    return docker.docker_argv(["true"], "/w", sandbox, None, **kw)


def _limits(argv):
    return [a for a in argv if a.startswith(("--cpus", "--memory", "--pids-limit"))]


@pytest.mark.parametrize(
    ("runtime", "resources", "flags"),
    [
        (
            docker.Runtime("docker"),
            ALL,
            ["--cpus=2", "--memory=512m", "--memory-swap=512m", "--pids-limit=100"],
        ),
        (
            docker.Runtime("docker", rootless=True, limits=frozenset({"cpu", "memory", "pids"})),
            {"cpu": 0.5, "memory": "1g"},
            ["--cpus=0.5", "--memory=1g", "--pids-limit=4096"],
        ),
        (
            docker.Runtime("podman"),
            {"memory": "64m"},
            ["--memory=64m", "--memory-swap=64m", "--pids-limit=4096"],
        ),
        (docker.Runtime("podman", rootless=True, limits=frozenset()), None, []),
        (docker.Runtime("podman", rootless=True, limits=frozenset()), {}, []),
    ],
    ids=["docker", "rootless-docker-no-swap", "podman", "rootless-podman-v1", "empty-resources"],
)
def test_each_runtime_gets_the_limits_it_can_enforce(monkeypatch, runtime, resources, flags):
    """Swap is limited to the memory limit wherever it can be: without it
    docker lets the container swap as much again, and it is not a limit."""
    assert _limits(_argv_on(monkeypatch, runtime, resources)) == flags


@pytest.mark.parametrize(
    ("limits", "flags"),
    [(docker.LIMITS, ["--pids-limit=4096"]), (docker.LIMITS - {"pids"}, [])],
    ids=["enforced", "unsupported-dropped"],
)
def test_the_default_pids_limit_applies_only_where_it_can(monkeypatch, limits, flags):
    """Nobody asked for the default, so a runtime that cannot limit pids runs
    without it rather than refusing every launch."""
    assert _limits(_argv_on(monkeypatch, docker.Runtime(limits=limits))) == flags


@pytest.mark.parametrize("name", ["cpu", "memory", "pids"])
def test_a_requested_limit_the_runtime_cannot_enforce_refuses_the_launch(monkeypatch, name):
    """docker drops a limit whose cgroup controller is missing with only a
    warning; Kraft stops instead, and says how to get the controller."""
    runtime = docker.Runtime(limits=docker.LIMITS - {name})
    with pytest.raises(docker.SandboxRefused, match=f"cannot enforce the sandbox's {name}") as no:
        _argv_on(monkeypatch, runtime, {name: ALL[name]})
    assert "cgroup v2" in str(no.value) and "Delegate=" in str(no.value)


async def test_the_refusal_stops_a_session_before_it_starts_and_fails_doctor(monkeypatch):
    monkeypatch.setattr(docker, "_RUNTIME", docker.Runtime(limits=frozenset()))
    monkeypatch.setattr(docker, "runtime", lambda refresh=False: docker._RUNTIME)
    sandbox = {**IMAGE, "resources": {"memory": "32m"}}
    reason = await docker.DockerBackend().probe(sandbox, "x", None)
    assert reason is not None and "memory limit (memory: 32m)" in reason
    ok, detail = await docker.DockerBackend().health(sandbox)
    assert not ok and detail == reason


@pytest.mark.parametrize(
    ("limits", "said"),
    [
        (docker.LIMITS, "limits cpu, memory, pids"),
        (frozenset({"cpu", "memory"}), "limits cpu, memory (swap unbounded)"),
        (frozenset(), "no resource limits (no cgroup controllers: needs cgroup v2"),
    ],
    ids=["all", "no-swap", "none"],
)
def test_doctor_says_which_limits_the_runtime_enforces(limits, said):
    assert docker.Runtime(limits=limits).describe_limits().startswith(said)


@pytest.mark.parametrize(
    ("cpu", "flag"),
    [
        (2.0, "--cpus=2"),
        (0.5, "--cpus=0.5"),
        (0.01, "--cpus=0.01"),
        (1.1234567, "--cpus=1.1234567"),
    ],
    ids=["whole", "half", "least", "many-digits"],
)
def test_cpus_are_written_in_fixed_point_as_given(monkeypatch, cpu, flag):
    """`:g` wrote 1e-05 and rounded to six digits; `--cpus` reads what it is given."""
    assert _limits(_argv_on(monkeypatch, docker.Runtime(), {"cpu": cpu}))[0] == flag


@pytest.mark.parametrize("asked_again_by", ["expiry", "doctor"])
def test_an_inconclusive_runtime_refuses_a_limit_and_is_asked_again(
    tmp_path, monkeypatch, asked_again_by
):
    """An `info` that did not answer (a daemon starting up, or hung for ~30s)
    is not "every limit works". It is kept briefly, so a hung daemon is not
    asked on every call (Kraft-zfu0a), then asked again; doctor asks at once."""
    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    monkeypatch.setattr(docker, "_ask", lambda cli: ("docker", False, False))
    answers = iter([None, docker.LIMITS])
    monkeypatch.setattr(docker, "_ask_limits", lambda cli, engine: next(answers))
    monkeypatch.setattr(docker, "_RUNTIME", None)

    unsure = docker.runtime()
    assert unsure.limit_args(None) == []  # the default pids limit is dropped
    with pytest.raises(docker.SandboxRefused, match="could not tell whether"):
        unsure.limit_args({"memory": "32m"})
    assert docker.runtime() is unsure
    if asked_again_by == "expiry":
        monkeypatch.setattr(docker, "_UNSURE_UNTIL", 0.0)
    assert docker.runtime(refresh=asked_again_by == "doctor").limits == docker.LIMITS


def _cli(tmp_path, monkeypatch, name, info):
    """A `name` on PATH answering every `info` with `info`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / name).write_text(f"#!/bin/sh\necho '{info}'\n")
    (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.parametrize(
    ("engine", "info", "limits"),
    [
        ("docker", "true true true true", docker.LIMITS),
        ("docker", "true true false false", {"cpu", "memory"}),
        ("docker", "false false false false", set()),
        ("docker", "", None),
        ("podman", '["cpuset","cpu","memory","pids"]', docker.LIMITS),
        ("podman", '["cpu"]', {"cpu"}),
        ("podman", "[]", set()),
        ("podman", "", None),
    ],
    ids=[
        "docker",
        "docker-no-swap-no-pids",
        "rootless-docker-v1",
        "docker-inconclusive",
        "podman",
        "podman-cpu-only",
        "rootless-podman-v1",
        "podman-inconclusive",
    ],
)
def test_the_runtime_says_what_it_can_enforce(tmp_path, monkeypatch, engine, info, limits):
    """Rootless podman on cgroup v1 lists no controllers at all (seen as user
    `kuser` on the dev container). An answer that does not parse is no
    answer, never every limit."""
    _cli(tmp_path, monkeypatch, "rt", info)
    assert docker._ask_limits("rt", engine) == limits


def test_a_named_container_outlives_its_exit_and_an_unnamed_one_does_not(monkeypatch):
    """Removed on exit, a session's container could not be asked whether its
    memory limit killed it; `teardown` removes it by name instead."""
    named = _argv_on(monkeypatch, docker.Runtime(), name="kraft-s1")
    assert "--rm" not in named
    # A container a failed teardown leaves until the next start keeps no log.
    assert "--log-driver=none" in named
    assert "--rm" in _argv_on(monkeypatch, docker.Runtime())


@pytest.fixture
def inspect(tmp_path, monkeypatch):
    """`inspect(answer)`: the fake docker's `inspect` prints `answer`."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}{os.pathsep}{os.environ['PATH']}")

    def go(answer):
        path = tmp_path / "inspect"
        path.write_text(answer)
        monkeypatch.setenv("FAKE_DOCKER_INSPECT", str(path))

    return go


@pytest.mark.parametrize(
    ("answer", "memory"),
    [
        ("true 33554432\n", "32m"),
        ("true 4294967296\n", "4g"),
        ("true 6292480\n", "6145k"),
        ("false 33554432\n", None),
        ("true 0\n", None),
        (None, None),
    ],
    ids=["killed-32m", "killed-4g", "killed-odd-size", "exit-137-alone", "host-oom", "gone"],
)
async def test_an_oom_kill_is_read_off_the_runtime_never_the_exit_code(inspect, answer, memory):
    """Exit 137 is also Kraft's own SIGKILL, so only `State.OOMKilled` says the
    limit did it; a container with no memory limit the host's OOM killer hit
    is the host running short, not a limit a retry would hit again."""
    if answer is not None:
        inspect(answer)
    assert await docker.DockerBackend().oom_killed("s1") == memory


@pytest.mark.parametrize("engine", ["podman", "docker"])
async def test_podmans_oom_file_is_its_word_on_cgroup_v1(inspect, monkeypatch, engine):
    """Podman on cgroup v1 never sets `State.OOMKilled`; its conmon writes an
    `oom` file where the client runs, which `client_cwd` made Kraft's own
    directory. Docker's word is its flag alone. `teardown` removes the file
    with the directory."""
    monkeypatch.setattr(docker, "_RUNTIME", docker.Runtime("docker", engine=engine))
    inspect("false 33554432\n")
    backend = docker.DockerBackend()
    (backend.client_cwd("s1") / "oom").touch()

    assert await backend.oom_killed("s1") == ("32m" if engine == "podman" else None)
    await backend.close("s1")
    assert not docker.client_dir("s1").exists()
