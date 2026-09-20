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
