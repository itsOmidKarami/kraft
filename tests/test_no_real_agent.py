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
from conftest import _REAL_AGENT_BINARIES

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


def test_the_fixture_gives_escalation_a_fake_claude_so_the_guard_has_nothing_to_catch(tmp_path):
    """A guard that fires is not a fix.

    `escalate.dispatch` selects the `claude` harness by name
    (`escalate._ESCALATION_HARNESS`) because escalation is not a chain node and
    nothing declares one for it. No fixture wrote a `claude` overlay, so every
    test reaching an escalation resolved the *bundled* `command: [claude]` --
    round 2 made that a named `AssertionError` instead of a silent real agent
    turn, which is better and still not a fix.

    `seed_v1_library` now overlays the bundled declaration with its `command:`
    swapped for the fake agent. The bundled one, not the minimal `_FAKE_HARNESS`:
    escalation asks for `autocompact`, `permission_mode` and `deny_tools`, and
    `run_agent_task` raises on a capability the harness has not declared.
    """
    import yaml
    from support.harness import seed_v1_library

    from kraft import escalate, harness
    from kraft.paths import default_harnesses_dir

    seed_v1_library(
        tmp_path / "templates", agent_command=str(Path("fixtures/fake-claude.sh").resolve())
    )
    # `default_harnesses_dir()` -- `$KRAFT_HOME/templates/harnesses` -- which is
    # where `kraft.harness.load` reads, and a *different* directory from
    # `KRAFT_TEMPLATES_DIR` under pytest (`conftest._isolated_kraft_home`).
    # Writing it beside the library instead is the bug round 2 had to fix.
    overlay = default_harnesses_dir() / f"{escalate._ESCALATION_HARNESS}.yaml"
    assert overlay.is_file(), "no overlay for the harness escalation selects"

    declared = yaml.safe_load(overlay.read_text())
    assert declared["id"] == escalate._ESCALATION_HARNESS
    assert Path(declared["command"][0]).name not in _REAL_AGENT_BINARIES
    # Every capability the bundled declaration has, so nothing escalation asks
    # for raises "declares no <name> capability".
    bundled = yaml.safe_load((harness.BUNDLED / "claude.yaml").read_text())
    assert set(declared["capabilities"]) == set(bundled["capabilities"])
