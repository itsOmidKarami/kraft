"""Whether the runtime is rootless, asked of it: what a launch does when it
does not answer in time."""

import os
import threading
import time
from pathlib import Path

import pytest

from kraft.config import ConfigError
from kraft.worker.backends import docker
from kraft.worker.sandbox import SandboxNotReady

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
        ("docker", "Docker version 29.3.1", ""),
        ("docker", "Docker version 29.3.1", "null"),
        ("docker", "Docker version 29.3.1", '{"name": "rootless"}'),
        ("podman", "podman version 4.9.3", None),
        ("podman", "podman version 4.9.3", ""),
        ("podman", "podman version 4.9.3", "Error: database is locked"),
    ],
    ids=[
        "docker-no-answer",
        "docker-empty",
        "docker-null",
        "docker-not-a-list",
        "podman-no-answer",
        "podman-empty",
        "podman-garbled",
    ],
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


def test_a_daemon_that_is_down_is_named_not_reported_as_slow(tmp_path, monkeypatch):
    """`docker info` failing at once, its daemon down, read as "did not answer
    within 30 s" one second after the item resumed."""
    down = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock."
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(
        f'#!/bin/sh\n[ "$1" = --version ] && {{ echo "Docker version 29"; exit 0; }}\n'
        f'echo "{down}" >&2; exit 1\n'
    )
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path / "templates"))
    monkeypatch.setattr(docker, "_INFO_FAILED", {})

    refusal = docker.detect_runtime().refusal()

    assert f"`docker info` failed: {down} Is its daemon running?" in refusal
    assert "within" not in refusal


@pytest.fixture
def slow_podman(tmp_path, monkeypatch):
    """`slow_podman(*answers)`: a podman whose `info` answers each ask in
    turn, None for one that timed out; detected once now, with `UNSURE_TTL`
    far from over. Returns the detection, and the list of each ask's
    timeout so far. `cli="docker"` makes it a docker instead."""
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "sandbox.yaml").write_text("cli: podman\n")
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    monkeypatch.setattr(docker, "_ask_limits", lambda cli, engine: docker.LIMITS)
    monkeypatch.setattr(docker, "missing_executable", lambda *a: _false())
    monkeypatch.setattr(docker, "_RUNTIME", None)
    # Recorded first, so the value `runtime()` writes is undone too.
    monkeypatch.setattr(docker, "_UNSURE_UNTIL", docker._UNSURE_UNTIL)
    monkeypatch.setattr(docker, "_REASK", docker._REASK)

    def go(*answers, during=lambda: None, cli="podman"):
        (templates / "sandbox.yaml").write_text(f"cli: {cli}\n")
        left = iter(answers)
        timeouts = []

        def ask(cli_, timeout=10, engine=None):
            timeouts.append(timeout)
            if len(timeouts) > 1:
                during()
            return cli, next(left), False

        monkeypatch.setattr(docker, "_ask", ask)
        unsure = docker.runtime()
        monkeypatch.setattr(docker, "_UNSURE_UNTIL", time.monotonic() + 3600)
        return unsure, timeouts

    return go


async def _false():
    return False


async def test_a_launch_asks_a_runtime_that_did_not_answer_again_at_once(slow_podman):
    """The e2e walk's flake: the first `info` timed out and the cached guess
    stopped the task. Now that guess refuses every launch, and the launch
    itself asks again rather than waiting out `UNSURE_TTL`."""
    unsure, timeouts = slow_podman(None, True)
    assert unsure.refusal() is not None
    with pytest.raises(docker.SandboxRefused, match="could not tell whether podman runs rootless"):
        docker.docker_argv(["true"], "/work", _SANDBOX, None)
    assert docker.runtime() is unsure  # a best-effort call does not ask again

    assert await docker.DockerBackend().probe(_SANDBOX, "sh", None) is None
    argv = docker.docker_argv(["true"], "/work", _SANDBOX, None)
    assert "--userns=keep-id" in argv
    # The second ask waits longer than the first: that slow answer took 4.7s.
    assert timeouts == [10, docker.REASK_TIMEOUT_S]


async def test_a_launch_asks_again_once_however_many_checks_it_makes(slow_podman):
    """The owner check would skip itself on "rootful", and the probe after
    it would then launch on the answer it got: the owner check refuses
    instead, and a launch costs one more ask, not one per check."""
    _, timeouts = slow_podman(None, None, True)
    backend = docker.DockerBackend()
    owner = await backend.owner_refusal(None, Path("/work"), "item", Path("/r.json"))
    assert owner is not None and "could not tell whether podman runs rootless" in owner
    reason = await backend.probe(_SANDBOX, "sh", None)
    assert reason is not None and "could not tell whether podman runs rootless" in reason
    assert len(timeouts) == 2


def test_a_tool_container_asks_again_too(slow_podman, tmp_path):
    """A harness's version check and codex's hook trust run in a one-shot
    container before the session's own checks: it asks again as they do."""
    slow_podman(None, True)
    assert "--userns=keep-id" in docker.oneshot(_SANDBOX, tmp_path, tmp_path).flags


async def test_a_setup_command_asks_again_before_it_runs(slow_podman, monkeypatch):
    """A repository's setup command is never probed; its `prepare` asks."""
    slow_podman(None, None)
    with pytest.raises(SandboxNotReady, match="could not tell whether podman runs rootless"):
        await docker.DockerBackend().prepare(_SANDBOX)


def test_no_answer_never_replaces_an_answer(slow_podman):
    """Doctor's refresh, or another launch's, getting no answer between one
    launch's probe and its `docker run` would refuse that `docker run`."""
    slow_podman(True, None)
    again = docker.runtime(refresh=True)
    assert again.identity_known and again.rootless is True


@pytest.mark.parametrize(
    ("answer", "root"), [(False, False), (None, None)], ids=["now-rootful", "still-no-answer"]
)
async def test_a_kept_rootless_docker_is_asked_again_before_the_next_launch(
    slow_podman, answer, root
):
    """A daemon switched from rootless to rootful, then a refresh that timed
    out, kept "rootless": rootless docker's `-u 0:0` is real root on a
    rootful one. The next launch asks again, and never runs on the guess."""
    _, timeouts = slow_podman(True, None, answer, cli="docker")
    assert docker.runtime(refresh=True).rootless is True  # still good for a launch probed
    reason = await docker.DockerBackend().probe(_SANDBOX, "sh", None)
    assert len(timeouts) == 3
    if root is None:
        assert reason is not None and "could not tell whether docker runs rootless" in reason
        with pytest.raises(docker.SandboxRefused, match="could not tell"):
            docker.docker_argv(["true"], "/work", _SANDBOX, None)
    else:
        assert reason is None
        argv = docker.docker_argv(["true"], "/work", _SANDBOX, None)
        assert argv[argv.index("-u") + 1] == f"{os.getuid()}:{os.getgid()}"


async def test_a_kept_rootless_podman_launches_without_asking_again(slow_podman):
    """keep-id runs as the operator's uid, rootful or not: a busy runner's
    timed-out refresh does not cost the next launch another ask."""
    _, timeouts = slow_podman(True, None)
    docker.runtime(refresh=True)
    assert await docker.DockerBackend().probe(_SANDBOX, "sh", None) is None
    assert "--userns=keep-id" in docker.docker_argv(["true"], "/work", _SANDBOX, None)
    assert len(timeouts) == 2


def test_a_kept_rootless_docker_is_asked_again_after_a_while(slow_podman, monkeypatch):
    slow_podman(True, None, False, cli="docker")
    assert docker.runtime(refresh=True).kept_root
    monkeypatch.setattr(docker, "_UNSURE_UNTIL", 0.0)
    again = docker.runtime()
    assert (again.rootless, again.identity_kept) == (False, False)


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
    slow_podman(None, None, None)

    async def call(*args, **_):
        return version

    monkeypatch.setattr(docker, "docker_call", call)
    ok, detail = await docker.DockerBackend().health(_SANDBOX)
    assert not ok and said in detail


async def test_doctor_asks_again_as_a_launch_would(slow_podman, monkeypatch, caplog):
    """Doctor's own refresh times out, its second ask answers: it passes
    the row a launch would start under."""
    slow_podman(None, None, True)

    async def call(*args, **_):
        return 0, "4.9.3\n"

    monkeypatch.setattr(docker, "docker_call", call)
    ok, detail = await docker.DockerBackend().health(_SANDBOX)
    assert ok, detail
    # Doctor is otherwise silent for the up to 30 s the second ask may take.
    assert "asking once more, waiting up to 30 s" in caplog.text


def test_a_cached_runtime_is_read_while_a_launch_asks_again(slow_podman):
    """The event loop reads the cached runtime (`docker_argv`, `oom_killed`,
    `launch_failed`) while a launch's thread waits on a slow `info`: the
    read must not wait with it, or the whole daemon stalls."""
    asking, answer = threading.Event(), threading.Event()

    def held():
        asking.set()
        answer.wait()

    unsure, _ = slow_podman(None, True, during=held)
    launch = threading.Thread(target=docker._launch_runtime)
    launch.start()
    assert asking.wait(5)
    read = []
    reader = threading.Thread(target=lambda: read.append(docker.runtime()))
    reader.start()
    reader.join(2)
    answered = not reader.is_alive()
    answer.set()
    launch.join(5)
    reader.join(5)
    assert answered and read == [unsure]
    assert docker.runtime().rootless is True


def test_a_second_ask_that_goes_unanswered_restarts_the_wait(slow_podman, monkeypatch):
    """A second ask can outlast `UNSURE_TTL` itself; counted from the
    detection, the next launch would detect and ask again at once."""

    def outlasts():
        monkeypatch.setattr(docker, "_UNSURE_UNTIL", 0.0)

    _, timeouts = slow_podman(None, None, True, during=outlasts)
    assert docker._launch_runtime().identity_known is False
    assert docker._launch_runtime().identity_known is False
    assert len(timeouts) == 2


def test_the_second_ask_does_not_ask_the_version_again(monkeypatch):
    """The engine is known from the first ask: the second's one `info` is
    all `REASK_TIMEOUT_S` has to cover."""
    ran = []

    def run(*argv, timeout=10):
        ran.append(argv[1])
        return "true false\n"

    monkeypatch.setattr(docker, "_run", run)
    assert docker._ask("podman", docker.REASK_TIMEOUT_S, "podman") == ("podman", True, False)
    assert ran == ["info"]


@pytest.fixture
def due(monkeypatch):
    """A cached runtime that did not say whether it is rootless, its
    `UNSURE_TTL` up: the next `runtime()` detects again."""
    unsure = docker.Runtime("podman", engine="podman", identity_known=False)
    monkeypatch.setattr(docker, "_RUNTIME", unsure)
    monkeypatch.setattr(docker, "_UNSURE_UNTIL", 0.0)
    monkeypatch.setattr(docker, "_DETECTING", None)
    monkeypatch.setattr(docker, "_REASK", None)
    return unsure


def _in_thread(call):
    """`call()` on a thread of its own: `(thread, results)`."""
    out = []
    thread = threading.Thread(target=lambda: out.append(call()), daemon=True)
    thread.start()
    return thread, out


def test_a_cached_runtime_is_read_while_another_thread_detects(due, monkeypatch):
    detecting, answer = threading.Event(), threading.Event()

    def held():
        detecting.set()
        answer.wait()
        return docker.Runtime("podman", engine="podman", rootless=True)

    monkeypatch.setattr(docker, "detect_runtime", held)
    detector, _ = _in_thread(docker.runtime)
    assert detecting.wait(5)
    reader, read = _in_thread(docker.runtime)
    reader.join(2)
    answered = not reader.is_alive()
    answer.set()
    detector.join(5)
    reader.join(5)
    assert answered and read == [due]
    assert docker.runtime().rootless is True


def test_a_detection_that_raises_lets_a_waiting_caller_go(monkeypatch):
    """With nothing cached, a second caller waits on the first's detection;
    when that raises, the second detects for itself instead of spinning."""
    monkeypatch.setattr(docker, "_RUNTIME", None)
    monkeypatch.setattr(docker, "_UNSURE_UNTIL", docker._UNSURE_UNTIL)
    monkeypatch.setattr(docker, "_DETECTING", None)
    monkeypatch.setattr(docker, "_REASK", None)
    detecting, fail = threading.Event(), threading.Event()
    calls = []

    def detect():
        calls.append(1)
        if len(calls) == 1:
            detecting.set()
            fail.wait()
            raise ConfigError("sandbox.yaml does not parse")
        return docker.Runtime("podman", engine="podman", rootless=True)

    monkeypatch.setattr(docker, "detect_runtime", detect)
    first, _ = _in_thread(lambda: pytest.raises(ConfigError, docker.runtime))
    assert detecting.wait(5)
    second, got = _in_thread(docker.runtime)
    fail.set()
    first.join(5)
    second.join(5)
    assert not second.is_alive(), "the second caller never stopped waiting"
    assert got[0].rootless is True


def test_the_event_loop_reads_the_cache_and_detects_aside(due, monkeypatch):
    """`docker_argv`, the relay argv and `launch_failed` run on the event
    loop: a detection that is due runs in a thread of its own."""
    published = threading.Event()

    def detect():
        published.set()
        return docker.Runtime("podman", engine="podman", rootless=True)

    monkeypatch.setattr(docker, "detect_runtime", detect)
    assert docker.cached_runtime() is due
    assert published.wait(5)
    for aside in [t for t in threading.enumerate() if t.name == "kraft-runtime"]:
        aside.join(5)
    assert docker._RUNTIME.rootless is True


def test_a_detection_that_raises_waits_before_the_next(due, monkeypatch):
    """A broken `sandbox.yaml` raises on every detection: the event loop's
    reads must not start one per call while the cached answer is unsure."""
    detected = []

    def detect():
        detected.append(1)
        raise ConfigError("sandbox.yaml does not parse")

    monkeypatch.setattr(docker, "detect_runtime", detect)
    with pytest.raises(ConfigError):
        docker.runtime()
    for _ in range(50):
        assert docker.cached_runtime() is due
    for aside in [t for t in threading.enumerate() if t.name == "kraft-runtime"]:
        aside.join(5)
    assert detected == [1]


class _Watched:
    """A lock that says when someone starts waiting for it."""

    def __init__(self):
        self.lock, self.waiting = threading.Lock(), threading.Event()

    def __enter__(self):
        self.waiting.set()
        self.lock.acquire()

    def __exit__(self, *exc):
        self.lock.release()


def test_a_read_during_a_publish_does_not_detect_again(due, monkeypatch):
    """A lock-free reader can catch a new no-answer runtime before its
    deadline: it must find the deadline under the lock, not detect again,
    which on the event loop would stall it."""
    watched = _Watched()
    monkeypatch.setattr(docker, "_LOCK", watched)
    detected = []
    monkeypatch.setattr(docker, "detect_runtime", lambda: detected.append(1) or due)
    with watched.lock:  # the publisher, half way through
        fresh = docker.Runtime("podman", engine="podman", identity_known=False)
        monkeypatch.setattr(docker, "_RUNTIME", fresh)
        reader, read = _in_thread(docker.runtime)
        assert watched.waiting.wait(5)
        monkeypatch.setattr(docker, "_UNSURE_UNTIL", time.monotonic() + 3600)
    reader.join(5)
    assert read == [fresh] and detected == []
