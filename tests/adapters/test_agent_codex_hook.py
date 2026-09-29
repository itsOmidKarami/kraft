"""`kraft.adapters.agent` on codex (Kraft-4in7z.3): policy with something to
enforce puts Kraft's PreToolUse hook on the command line, trusted for that
launch by the hash `codex app-server` lists -- on a resume too, since codex
reads its config again per invocation. No trust, no launch."""

import os
import shlex
import sys
from pathlib import Path

import pytest

from kraft.adapters import hook_install as hi
from kraft.adapters.agent import LaunchRefused
from kraft.paths import RunDirs

FAKE = shlex.join(
    [sys.executable, str(Path(__file__).parents[1] / "support/fake_codex_app_server.py")]
)
FAIL_CLOSED = "KRAFT_PERMISSION_FAIL_CLOSED"


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setattr(hi, "_codex_flags", {})


def _codex(run, tmp_path, **kw):
    return run(
        harness="codex",
        command=FAKE,
        cwd=str(tmp_path),
        run_dirs=RunDirs(base=tmp_path / "run"),
        **kw,
    )


def _hook_flags(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c" and cmd[i + 1].startswith("hooks.")]


def test_a_codex_launch_with_deny_tools_carries_its_trusted_hook(run, tmp_path):
    seen = _codex(run, tmp_path, deny_tools=("Bash",))
    cmd = seen["cmd"]
    hook, state = _hook_flags(cmd)
    assert cmd[2:9] == ["exec", "-c", hook, "-c", "features.hooks=true", "-c", state]
    assert hook.startswith("hooks.PreToolUse=") and "permission-hook codex" in hook
    assert state.startswith("hooks.state=") and "trusted_hash=" in state
    assert "Bash" not in cmd
    assert FAIL_CLOSED not in seen["env"]


def test_a_codex_launch_under_an_allowlist_runs_fail_closed(run, tmp_path):
    seen = _codex(run, tmp_path, allowed_tools=("Read", "WebSearch"))
    assert len(_hook_flags(seen["cmd"])) == 2
    assert seen["env"][FAIL_CLOSED] == "1"


def test_a_codex_resume_keeps_the_hook(run, tmp_path):
    cmd = _codex(run, tmp_path, deny_tools=("Bash",), resume_session_id="thread-1")["cmd"]
    hook, state = _hook_flags(cmd)
    assert cmd[2:11] == [
        "exec",
        "resume",
        "thread-1",
        "-c",
        hook,
        "-c",
        "features.hooks=true",
        "-c",
        state,
    ]


def test_a_codex_launch_with_nothing_to_enforce_gets_no_hook(run, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "exit")  # would refuse, were it asked
    seen = _codex(run, tmp_path, grants=("git-commit",))
    assert _hook_flags(seen["cmd"]) == []


def test_a_codex_launch_whose_hook_codex_will_not_trust_is_refused(run, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "distrust")
    with pytest.raises(LaunchRefused, match="permission hook.*did not trust"):
        _codex(run, tmp_path, grants=("git-push",))


@pytest.mark.parametrize("policy", [{"deny_tools": ("WebSearch",)}, {"allowed_tools": ("Read",)}])
def test_a_codex_launch_that_must_deny_web_search_is_refused(run, tmp_path, policy):
    """Codex's web search runs on OpenAI's side; its hook never sees it."""
    with pytest.raises(LaunchRefused, match="WebSearch"):
        _codex(run, tmp_path, **policy)


NETWORKED = {"kind": "docker", "image": "x", "network": {"runtime": {"allow": ["x.io"]}}}


@pytest.fixture
def image_codex(tmp_path, monkeypatch):
    """A fake `docker` on PATH that logs each argv to the returned file, then
    runs the command as `fake_docker_bin` does; FAKE_DOCKER_RUN_FAILS makes
    `docker run` fail as a runtime would before starting the container."""
    from support.harness import fake_docker_bin

    log = tmp_path / "docker.log"
    wrapper = tmp_path / "logging-docker"
    wrapper.mkdir()
    (wrapper / "docker").write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {log}\n'
        '[ -n "${FAKE_DOCKER_RUN_FAILS:-}" ] && [ "$1" = run ] && exit 125\n'
        f'exec {fake_docker_bin(tmp_path)}/docker "$@"\n'
    )
    (wrapper / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{wrapper}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "codex.log"))
    return log


def test_a_sandboxed_codex_under_network_hooks_through_the_shim(run, tmp_path, image_codex):
    """Its channel is the hook's route to Kraft: the launch runs, its hook the
    shim at its fixed container path, never the host's interpreter; and the
    hook is trusted by the codex the session runs -- the image's, asked in a
    container with no network -- never the host's, which may hash it otherwise."""
    for _ in range(2):  # never cached: the image under a tag can change
        cmd = _codex(run, tmp_path, deny_tools=("Bash",), sandbox=NETWORKED)["cmd"]
    hook, _ = _hook_flags(cmd)
    assert '"/opt/kraft/bin/kraft admin permission-hook codex"' in hook
    assert sys.executable not in hook
    asked = image_codex.read_text().splitlines()
    assert len(asked) == 4
    for argv in asked:
        before, image, after = argv.partition(" x ")
        assert before.startswith("run --rm ") and "--network=none" in before.split()
        assert "/opt/kraft/bin:ro" in before and after.startswith(f"{FAKE} app-server ")


@pytest.mark.parametrize(
    "why, match",
    [
        ("runtime-refuses", "SELinux"),
        ("run-fails", "exited before answering"),
        ("image-codex-distrusts", "did not trust"),
    ],
)
def test_a_sandboxed_codex_whose_image_cannot_vouch_for_the_hook_is_refused(
    run, tmp_path, monkeypatch, image_codex, why, match
):
    """Codex skips an untrusted hook without a word (measured, 0.155.0), so
    trust the image's codex could not confirm is no launch -- and never the
    host's codex asked instead."""
    from kraft.worker.backends import docker

    if why == "runtime-refuses":
        monkeypatch.setattr(docker, "_RUNTIME", docker.Runtime(selinux="refuse"))
    elif why == "run-fails":
        monkeypatch.setenv("FAKE_DOCKER_RUN_FAILS", "1")
    else:
        monkeypatch.setenv("FAKE_CODEX_MODE", "distrust")
    with pytest.raises(LaunchRefused, match=f"permission hook.*{match}"):
        _codex(run, tmp_path, deny_tools=("Bash",), sandbox=NETWORKED)
    codex_log = tmp_path / "codex.log"
    spawned = codex_log.read_text().splitlines() if codex_log.exists() else []
    assert len(spawned) == (2 if why == "image-codex-distrusts" else 0)
    assert image_codex.exists() == (why != "runtime-refuses")
