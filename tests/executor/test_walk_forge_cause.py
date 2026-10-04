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
    ("message", "cause", "reason_ends"),
    [
        (
            "gh pr create failed: HTTP 401: Bad credentials",
            "forge_auth",
            "Fix them, then retry.",
        ),
        (
            "git push failed: fatal: Authentication failed for 'https://x'",
            "forge_auth",
            "Fix them, then retry.",
        ),
        (
            "gh pr create failed: dial tcp: Could not resolve host: github.com",
            "forge_unreachable",
            "Check its network, then retry.",
        ),
        ("boom: no capacity", None, "Reinstall and restart, or skip the node."),
    ],
    ids=["gh-401", "git-push-auth", "unreachable", "neither"],
)
@pytest.mark.parametrize(
    ("fix_loop", "stop_kind"), [(True, "infra"), (False, "failed")], ids=["fix-loop", "no-loop"]
)
async def test_a_forge_task_failure_names_its_cause_in_the_stops_facts(
    item_on, tmp_path, fake_agent, monkeypatch, message, cause, reason_ends, fix_loop, stop_kind
):
    """Kraft-9d8b2.67: a forge CLI failure is a failed task, not a stop of its
    own -- an `infra` one when its node has a fix loop (a worker commit cannot
    change it), else `failed`. Either way `facts.cause` carries what its log
    says, which is what the card's action is chosen from. An `infra` stop's
    reason ends with what to do about that cause, never with "Reinstall and
    restart" beside a credential the forge refused."""
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _RaisingWith(message))
    extra = {"fix_loop": {"tasks": [_agent("repair")]}} if fix_loop else {}
    it = await item_on([_exec("checks", _forge_task("open", "mr.open_draft"), **extra)])

    await _walk(it, repo_entry=FORGE_REPO, policy=_loop_policy(tmp_path))

    stop = it.events("work_item_needs_human")[-1]["payload"]
    assert (stop["kind"], stop.get("facts", {}).get("cause")) == (stop_kind, cause)
    if stop_kind == "infra":
        assert stop["reason"].endswith(reason_ends)
        assert ("Reinstall" in stop["reason"]) == (cause is None)
        # Plain text on the card: a backtick would show literally.
        assert "`" not in stop["reason"]
