"""The suite must never launch a real agent CLI (Kraft-jxu39).

The only reason a stray real launch has been cheap so far is that
`conftest._isolated_kraft_home` redirects `HOME` to an empty temp dir and CI has
no `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN`, so the binary resolves and
exits in milliseconds. On a developer machine with a key set the same call is a
real agent turn: network, tokens, tens of seconds. One was observed running.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from support.harness import REAL_AGENT_BINARIES as _REAL_AGENT_BINARIES

import kraft.adapters.subprocess as sp_mod


def test_a_real_agent_binary_is_refused_by_name(tmp_path):
    """The guard is on the command's *basename*, at `run_task`, where the argv is
    final -- not at `resolve_invocation`, because the defect class is a resolved
    launch whose command nobody expected. It names the test and the argv, so the
    next occurrence costs a read rather than an hour of counting progress
    characters."""
    with pytest.raises(AssertionError, match="real agent binary 'claude'"):
        asyncio.run(sp_mod.run_task(None, None, cmd=["claude", "-p", "hello"], cwd=tmp_path))


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
def test_the_real_executor_marker_opts_out(tmp_path):
    """`real_executor` is the existing opt-out (`tests/test_intake_poller.py`'s
    §5 regression test drives the real executor on purpose). It lets the launch
    through; `KRAFT_E2E` still gates the tests that mean to reach a real agent."""
    with pytest.raises(TypeError, match="missing 4 required keyword-only"):
        asyncio.run(sp_mod.run_task(None, None, cmd=["claude", "-p", "hello"], cwd=tmp_path))


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
