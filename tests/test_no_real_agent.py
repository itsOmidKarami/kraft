"""The suite must never launch a real agent CLI (Kraft-jxu39).

The only reason a stray real launch has been cheap so far is that
`conftest._isolated_kraft_home` redirects `HOME` to an empty temp dir and CI has
no `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN`, so the binary resolves and
exits in milliseconds. On a developer machine with a key set the same call is a
real agent turn: network, tokens, tens of seconds. One was observed running.
Nor a real `gh`/`glab`, which is installed and authenticated on many machines.

The probes below each reach for a guarded CLI by a different road. The
`installed` fixture puts a stand-in for every guarded name last on `PATH`, the
way a real install sits there; a probe that reaches it got past the guard.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import real_binaries
from support.harness import REAL_AGENT_BINARIES as _REAL_AGENT_BINARIES
from support.real_binaries import GUARDED_BINARIES

import kraft.adapters.subprocess as sp_mod
from kraft import harness, store
from kraft.adapters import agent, hook_install
from kraft.adapters.forge import git as forge_git
from kraft.adapters.forge.models import ForgeError

pytest_plugins = ("pytester",)


def test_a_real_agent_binary_is_refused_by_name(tmp_path):
    """The guard is on the command's *basename*, at `run_task`, where the argv is
    final -- not at `resolve_invocation`, because the defect class is a resolved
    launch whose command nobody expected. It names the test and the argv, so the
    next occurrence costs a read rather than an hour of counting progress
    characters."""
    with pytest.raises(AssertionError, match="real agent binary 'claude'"):
        asyncio.run(sp_mod.run_task(None, None, cmd=["claude", "-p", "hello"], cwd=tmp_path))


def test_every_bundled_harness_executable_is_guarded():
    """A harness whose binary the guard does not name launches for real from a
    unit test. Cursor's is `agent`, not the `cursor-agent` listed first
    (Kraft-bosip)."""
    for h in harness.load(None).valid.values():
        assert h.command[0] in _REAL_AGENT_BINARIES, h.id


def test_a_fixture_agent_and_an_ordinary_subprocess_are_untouched(tmp_path):
    """Every fixture agent (`fixtures/fake-claude.sh`, `support/fake_agent.py`,
    `sys.executable`) and every non-agent subprocess a node runs (`pytest`,
    `just`, `git`) must pass through -- a guard that blocks those would be
    swapped out within a week."""
    for cmd in (
        [str(Path("fixtures/fake-claude.sh").resolve())],
        ["python", "-m", "pytest", "-q"],
        ["just", "test"],
        ["git", "status"],
    ):
        # Reaches the real `run_task`, which then fails on the arguments this
        # test does not supply -- proof the guard let it through, with no
        # subprocess actually spawned. A `TypeError` here and an `AssertionError`
        # above is the whole distinction.
        with pytest.raises(TypeError, match="missing 4 required keyword-only"):
            asyncio.run(sp_mod.run_task(None, None, cmd=cmd, cwd=tmp_path))


@pytest.mark.real_executor
def test_the_real_executor_marker_opts_out(tmp_path, real_binary_guard):
    """`real_executor` is the existing opt-out (`tests/test_intake_poller.py`'s
    §5 regression test drives the real executor on purpose). It turns the
    whole guard off: the launch gets through, and no stub is on `PATH`.
    `KRAFT_E2E` still gates the tests that mean to reach a real agent."""
    with pytest.raises(TypeError, match="missing 4 required keyword-only"):
        asyncio.run(sp_mod.run_task(None, None, cmd=["claude", "-p", "hello"], cwd=tmp_path))
    assert real_binary_guard is None
    assert real_binaries.ACTIVE is None


def test_the_fixture_gives_escalation_a_fake_agent_so_the_guard_has_nothing_to_catch(
    tmp_path, monkeypatch
):
    """A guard that fires is not a fix.

    `escalate.dispatch` launches `escalate.ESCALATION_TASK`, an ordinary agent
    task on the `claude` profile. `seed_v1_library` puts every shipped
    profile on the overlaid `fake` provider -- the *bundled* `claude`
    declaration with its `command:` swapped, because escalation asks for
    `autocompact`, `permission_mode` and `deny_tools` and `run_agent_task`
    raises on a capability the harness has not declared -- so a test reaching
    an escalation launches the fake, not a real agent turn.
    """
    import yaml
    from support.harness import seed_v1_library

    from kraft import escalate, harness
    from kraft.adapters import agent
    from kraft.paths import default_harnesses_dir

    templates = tmp_path / "templates"
    seed_v1_library(templates, agent_command=str(Path("fixtures/fake-claude.sh").resolve()))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))

    profile = agent.harness_profile(escalate.ESCALATION_TASK.harness, harness.load(None))
    declared = yaml.safe_load((default_harnesses_dir() / f"{profile.provider}.yaml").read_text())
    assert Path(declared["command"][0]).name not in _REAL_AGENT_BINARIES
    # Every capability the bundled declaration has, so nothing escalation asks
    # for raises "declares no <name> capability".
    bundled = yaml.safe_load((harness.BUNDLED / "claude.yaml").read_text())
    assert set(declared["capabilities"]) == set(bundled["capabilities"])


@pytest.mark.parametrize("fix_loop", [False, True], ids=["loopless", "fix-loop"])
def test_the_guard_fails_a_test_that_walks_into_a_real_agent(tmp_path, monkeypatch, fix_loop):
    """Kraft-cpotk: inside a walk, `measure_node` gathers its tasks with
    `return_exceptions=True`, and turned the guard's `AssertionError` into an
    ordinary failed task and then a `needs_human` stop -- so a test expecting a
    stop passed a real-agent regression. The guard has to reach the test."""
    from support.harness import fake_harness_home, make_repo, v1_chain, v1_walk

    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_HOME", str(fake_harness_home(tmp_path, ["claude"])))
    agent = {"id": "work", "kind": "agent", "harness": "fake", "prompt": "do it"}
    node = {"id": "impl", "kind": "exec", "tasks": [agent]}
    if fix_loop:
        node = {
            **node,
            "tasks": [{"id": "check", "kind": "subprocess", "command": "false"}],
            "fix_loop": {"tasks": [agent]},
        }
    chain = v1_chain([node], repo=repo)
    policy = None
    if fix_loop:
        from kraft import policy as _policy

        (tmp_path / "policy.yaml").write_text("default: { attempts: 2, wall_clock_s: 3600 }\n")
        policy = _policy.load_policy(tmp_path / "policy.yaml")

    with pytest.raises(AssertionError, match="real agent binary 'claude'"):
        asyncio.run(v1_walk(tmp_path, chain, repo=repo, policy=policy))


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A stand-in for an installed real CLI of every guarded name, last on
    `PATH`. Each records its own name in `reached()` when it runs, and nothing
    else: a probe that reaches one got past the guard."""
    bin_dir = tmp_path / "installed"
    marks = tmp_path / "reached"
    bin_dir.mkdir()
    marks.mkdir()
    for name in GUARDED_BINARIES:
        (bin_dir / name).write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marks / name))}\n")
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{os.environ['PATH']}{os.pathsep}{bin_dir}")
    return SimpleNamespace(bin=bin_dir, reached=lambda: sorted(p.name for p in marks.iterdir()))


def _refused(guard) -> list[str]:
    """The names the guard refused since the last call, forgotten after."""
    return [call.split("\t")[0] for call in guard.take()]


def test_a_refused_call_nobody_read_fails_the_test_at_teardown(pytester, real_binary_guard):
    """The suite's own conftest, around a test that runs a stub and asserts
    nothing about it: the test passes, and its teardown errors naming it."""
    pytester.makeconftest((Path(__file__).parent / "conftest.py").read_text())
    pytester.makepyfile(
        "import subprocess\n\n"
        "def test_x():\n"
        "    subprocess.run(['claude', '-v'], capture_output=True)\n"
    )
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*test_x ran a real agent or forge CLI*claude*"])
    # One log per process: this test's own guard saw the inner call too.
    assert _refused(real_binary_guard) == ["claude"]


def test_every_guarded_name_resolves_to_its_stub(real_binary_guard, installed):
    """Agents and forges alike, ahead of whatever this machine installed."""
    for name in sorted(GUARDED_BINARIES):
        assert shutil.which(name) == str(real_binary_guard.stubs / name), name
    stub = subprocess.run(["glab", "mr", "list"], capture_output=True, text=True)
    assert stub.returncode == 127
    assert real_binaries.MESSAGE.format(name="glab") in stub.stderr
    assert _refused(real_binary_guard) == ["glab"]


async def _exec_agy():
    proc = await asyncio.create_subprocess_exec(
        "agy", "--version", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    await proc.wait()


async def _codex_hook(tmp_path):
    with contextlib.suppress(hook_install.CodexTrustError):
        await hook_install._codex_hook(["codex"], [], tmp_path, "kraft hook", [], False)


async def _forge(tmp_path, cli):
    with pytest.raises(ForgeError, match="refused by the test suite's real-binary guard"):
        await forge_git.run_git(tmp_path, [cli, "pr", "view", "1"])


def _child(tmp_path, installed):
    """A Python child whose own `PATH` puts the stand-in first: `child_env`
    still puts the stubs ahead of it."""
    from support.server import child_env

    code = "import subprocess; subprocess.run(['cursor', '--version'])"
    system = os.pathsep.join(["/usr/bin", "/bin"])
    env = child_env({"PATH": f"{installed.bin}{os.pathsep}{system}"})
    subprocess.run([sys.executable, "-c", code], env=env, capture_output=True)


def _run(argv, **kw):
    return subprocess.run(argv, capture_output=True, **kw)


#: `(name it reaches, probe(tmp_path, installed))`, one road each.
_PROBES = {
    "subprocess.run": ("gemini", lambda t, i: _run(["gemini", "-v"])),
    "Popen": ("amp", lambda t, i: subprocess.Popen(["amp"], stderr=subprocess.DEVNULL).wait()),
    "shell=True": ("claude", lambda t, i: _run("claude -v", shell=True)),
    "sh -c": ("claude", lambda t, i: _run(["sh", "-c", "claude -v"])),
    "env wrapper": ("codex", lambda t, i: _run([shutil.which("env"), "codex"])),
    "os.system": ("cursor-agent", lambda t, i: os.system("cursor-agent -v 2>/dev/null")),
    "create_subprocess_exec": ("agy", lambda t, i: asyncio.run(_exec_agy())),
    "agent._cli_version": (
        "opencode",
        lambda t, i: asyncio.run(agent._cli_version(["opencode"], "opencode", t)),
    ),
    "hook_install._codex_hook": ("codex", lambda t, i: asyncio.run(_codex_hook(t))),
    "run_git gh": ("gh", lambda t, i: asyncio.run(_forge(t, "gh"))),
    "run_git glab": ("glab", lambda t, i: asyncio.run(_forge(t, "glab"))),
    "a child process": ("cursor", _child),
}


@pytest.mark.parametrize(("name", "probe"), _PROBES.values(), ids=_PROBES)
def test_every_road_to_a_real_cli_is_refused(name, probe, tmp_path, installed, real_binary_guard):
    """Each road resolves the name through `PATH`, so each reaches the stub
    and none the installed CLI; the stub's log is what fails the test, at
    teardown, whatever the product made of the exit code."""
    probe(tmp_path, installed)
    assert installed.reached() == []
    assert _refused(real_binary_guard) == [name]


@pytest.mark.parametrize(
    "cmd", [["sh", "-c", "claude -v"], ["env", "claude", "-v"]], ids=["sh-c", "env"]
)
async def test_a_wrapped_launch_through_run_task_is_refused(
    cmd, database, run_dirs, tmp_path, installed, real_binary_guard
):
    """`run_task`'s own check reads `cmd[0]` only; a wrapper around the agent
    still resolves it through the stubs, in the worker env `run_task` builds."""
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )
    await sp_mod.run_task(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="verify",
        hook_point="on.test.run",
        cmd=cmd,
        cwd=tmp_path,
    )
    assert installed.reached() == []
    assert _refused(real_binary_guard) == ["claude"]


@pytest.mark.parametrize("road", ["absolute path", "env without the stubs"])
def test_popen_refuses_an_installed_cli_reached_without_the_stubs(
    road, installed, real_binary_guard
):
    """A launch that never asks the stubs: the path to the installed binary
    itself, or an `env=` whose `PATH` leaves them out. `Popen` knows the real
    binaries' paths and refuses those, in the test."""
    real_binary_guard.real[os.path.realpath(installed.bin / "gh")] = "gh"
    if road == "absolute path":
        argv, env = [str(installed.bin / "gh"), "--version"], None
    else:
        argv, env = ["gh", "--version"], {"PATH": str(installed.bin)}
    with pytest.raises(AssertionError, match="started the real `gh`"):
        subprocess.run(argv, env=env, capture_output=True)
    assert installed.reached() == []
    assert _refused(real_binary_guard) == ["gh"]


def test_an_e2e_test_gets_back_only_the_clis_it_names():
    """`e2e("claude", "docker")` unshadows `claude` and nothing else."""
    assert real_binaries.shadowed_for({"claude", "docker"}) == GUARDED_BINARIES - {"claude"}
    stubs = real_binaries.stub_dir(real_binaries.shadowed_for({"gh"}))
    assert sorted(p.name for p in stubs.iterdir()) == sorted(GUARDED_BINARIES - {"gh"})
