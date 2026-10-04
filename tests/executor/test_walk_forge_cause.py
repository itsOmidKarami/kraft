"""A forge task's failure names its cause (`forge_auth`, `forge_unreachable`) in
the stop's facts (Kraft-9d8b2.67)."""

import pytest

from kraft.adapters import forge as _forge

from .test_walk import (
    FORGE_REPO,
    _agent,
    _exec,
    _forge_task,
    _loop_policy,
    _RaisingForge,
    _walk,
)


class _RaisingWith(_RaisingForge):
    def __init__(self, message):
        self.message = message

    async def open_mr(self, **kwargs):
        raise _forge.ForgeError(self.message)


@pytest.mark.parametrize(
    ("message", "cause"),
    [
        ("gh pr create failed: HTTP 401: Bad credentials", "forge_auth"),
        ("git push failed: fatal: Authentication failed for 'https://x'", "forge_auth"),
        ("gh pr create failed: dial tcp: Could not resolve host: github.com", "forge_unreachable"),
        ("boom: no capacity", None),
    ],
    ids=["gh-401", "git-push-auth", "unreachable", "neither"],
)
@pytest.mark.parametrize(
    ("fix_loop", "stop_kind"), [(True, "infra"), (False, "failed")], ids=["fix-loop", "no-loop"]
)
async def test_a_forge_task_failure_names_its_cause_in_the_stops_facts(
    item_on, tmp_path, fake_agent, monkeypatch, message, cause, fix_loop, stop_kind
):
    """Kraft-9d8b2.67: a forge CLI failure is a failed task, not a stop of its
    own -- an `infra` one when its node has a fix loop (a worker commit cannot
    change it), else `failed`. Either way `facts.cause` carries what its log
    says, which is what the card's action is chosen from."""
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _RaisingWith(message))
    extra = {"fix_loop": {"tasks": [_agent("repair")]}} if fix_loop else {}
    it = await item_on([_exec("checks", _forge_task("open", "mr.open_draft"), **extra)])

    await _walk(it, repo_entry=FORGE_REPO, policy=_loop_policy(tmp_path))

    stop = it.events("work_item_needs_human")[-1]["payload"]
    assert (stop["kind"], stop.get("facts", {}).get("cause")) == (stop_kind, cause)
