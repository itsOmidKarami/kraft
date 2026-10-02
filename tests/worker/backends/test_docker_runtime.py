"""Whether the runtime is rootless, asked of it: what a launch does when it
does not answer in time."""

import os
import time

import pytest

from kraft.worker.backends import docker

_SANDBOX = {"kind": "docker", "image": "img"}


def _cli(tmp_path, monkeypatch, name, version, info):
    """A `name` on PATH answering `--version` as given, and `info` with
    `info`, or failing it for None."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    answer = "exit 1" if info is None else f"echo '{info}'"
    (bin_dir / name).write_text(
        f'#!/bin/sh\n[ "$1" = --version ] && {{ echo "{version}"; exit 0; }}\n{answer}\n'
    )
    (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.parametrize(
    ("name", "version", "info"),
    [
        ("docker", "Docker version 29.3.1", None),
        ("podman", "podman version 4.9.3", None),
        ("podman", "podman version 4.9.3", ""),
        ("podman", "podman version 4.9.3", "Error: database is locked"),
    ],
    ids=["docker-no-answer", "podman-no-answer", "podman-empty", "podman-garbled"],
)
def test_a_runtime_that_does_not_answer_is_not_read_as_rootful(
    tmp_path, monkeypatch, name, version, info
):
    """A rootless podman's first `info` on a busy runner took over `_run`'s
    ten seconds, and the guess "rootful" ran its container without
    `keep-id`, as a user who could not write the worktree. No answer is
    no answer."""
    _cli(tmp_path, monkeypatch, name, version, info)
    assert docker._ask(name)[1:] == (None, None)


@pytest.fixture
def slow_podman(tmp_path, monkeypatch):
    """`slow_podman(*answers)`: a podman whose `info` answers each detection
    in turn, None for one that timed out; detected once now, with
    `UNSURE_TTL` far from over."""
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "sandbox.yaml").write_text("cli: podman\n")
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    monkeypatch.setattr(docker, "_ask_limits", lambda cli, engine: docker.LIMITS)
    monkeypatch.setattr(docker, "missing_executable", lambda *a: _false())
    monkeypatch.setattr(docker, "_RUNTIME", None)
    # Recorded first, so the value `runtime()` writes is undone too.
    monkeypatch.setattr(docker, "_UNSURE_UNTIL", docker._UNSURE_UNTIL)

    def go(*answers):
        asked = iter(answers)
        monkeypatch.setattr(docker, "_ask", lambda cli: ("podman", next(asked), False))
        unsure = docker.runtime()
        monkeypatch.setattr(docker, "_UNSURE_UNTIL", time.monotonic() + 3600)
        return unsure

    return go


async def _false():
    return False


async def test_a_launch_asks_a_runtime_that_did_not_answer_again_at_once(slow_podman):
    """The e2e walk's flake: the first `info` timed out and the cached guess
    stopped the task. Now that guess refuses every launch, and the launch
    itself asks again rather than waiting out `UNSURE_TTL`."""
    unsure = slow_podman(None, True)
    assert unsure.refusal() is not None
    with pytest.raises(docker.SandboxRefused, match="could not tell whether podman runs rootless"):
        docker.docker_argv(["true"], "/work", _SANDBOX, None)
    assert docker.runtime() is unsure  # a best-effort call does not ask again

    assert await docker.DockerBackend().probe(_SANDBOX, "sh", None) is None
    argv = docker.docker_argv(["true"], "/work", _SANDBOX, None)
    assert "--userns=keep-id" in argv


def test_a_runtime_that_did_not_answer_is_asked_again_after_a_while(slow_podman, monkeypatch):
    slow_podman(None, True)
    monkeypatch.setattr(docker, "_UNSURE_UNTIL", 0.0)
    assert docker.runtime().rootless is True


async def test_a_runtime_that_still_does_not_answer_stops_the_launch(slow_podman):
    slow_podman(None, None)
    reason = await docker.DockerBackend().probe(_SANDBOX, "sh", None)
    assert reason is not None and "could not tell whether podman runs rootless" in reason


@pytest.mark.parametrize(
    ("version", "said"),
    [(None, "not installed or its daemon is not reachable"), ((0, "4.9.3\n"), "runs rootless")],
    ids=["daemon-down", "daemon-up"],
)
async def test_doctor_names_a_daemon_that_is_down_before_a_runtime_that_did_not_answer(
    slow_podman, monkeypatch, version, said
):
    """A runtime that is not there gives no `info` either: doctor says the
    plainer of the two first."""
    slow_podman(None, None)

    async def call(*args, **_):
        return version

    monkeypatch.setattr(docker, "docker_call", call)
    ok, detail = await docker.DockerBackend().health(_SANDBOX)
    assert not ok and said in detail
