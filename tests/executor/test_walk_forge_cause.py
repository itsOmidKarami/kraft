"""A forge task's failure names its cause (`forge_auth`, `forge_unreachable`) in
the stop's facts (Kraft-9d8b2.67)."""

from datetime import UTC, datetime, timedelta

import pytest

from kraft import waits
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
    def __init__(self, message, **error):
        self.message, self.error = message, error

    async def open_mr(self, **kwargs):
        raise _forge.ForgeError(self.message, **self.error)


@pytest.mark.parametrize(
    ("message", "cause", "reason_ends", "error"),
    [
        (
            "gh pr create failed: HTTP 401: Bad credentials",
            "forge_auth",
            "Fix them, then retry.",
            {},
        ),
        (
            "git push failed: fatal: Authentication failed for 'https://x'",
            "forge_auth",
            "Fix them, then retry.",
            {},
        ),
        (
            "gh pr create failed: dial tcp: Could not resolve host: github.com",
            "forge_unreachable",
            "Check its network, then retry.",
            {},
        ),
        ("boom: no capacity", None, "Reinstall and restart, or skip the node.", {}),
        # The record the task left decides, not its log's wording.
        ("boom: no capacity", "forge_auth", "Fix them, then retry.", {"reason": "forbidden"}),
        (
            "401 Unauthorized",
            None,
            "Reinstall and restart, or skip the node.",
            {"reason": "conflict"},
        ),
    ],
    ids=[
        "gh-401",
        "git-push-auth",
        "unreachable",
        "neither",
        "record-forbidden",
        "record-beats-text",
    ],
)
@pytest.mark.parametrize(
    ("fix_loop", "stop_kind"), [(True, "infra"), (False, "failed")], ids=["fix-loop", "no-loop"]
)
async def test_a_forge_task_failure_names_its_cause_in_the_stops_facts(
    item_on,
    tmp_path,
    fake_agent,
    monkeypatch,
    message,
    cause,
    reason_ends,
    error,
    fix_loop,
    stop_kind,
):
    """Kraft-9d8b2.67: a forge CLI failure is a failed task, not a stop of its
    own -- an `infra` one when its node has a fix loop (a worker commit cannot
    change it), else `failed`. Either way `facts.cause` carries what its log
    says, which is what the card's action is chosen from. An `infra` stop's
    reason ends with what to do about that cause, never with "Reinstall and
    restart" beside a credential the forge refused."""
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _RaisingWith(message, **error))
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


async def test_a_forge_rate_limit_parks_the_item_until_the_forge_allows_it(
    item_on, tmp_path, fake_agent, monkeypatch
):
    """The walk routes a forge task's `rate_limited` session to
    `stops.stop_for_rate_limit`: the item is rate limited with the retry time the
    forge named, not failed and not waiting on a person."""
    moment = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(waits, "now", lambda: moment)
    error = {"status": 403, "reason": "rate_limited", "retry_after": 30}
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _RaisingWith("slow down", **error))
    it = await item_on([_exec("checks", _forge_task("open", "mr.open_draft"))])

    assert await _walk(it, repo_entry=FORGE_REPO, policy=_loop_policy(tmp_path)) == "rate_limited"

    row = it.row()
    assert (row["status"], row["stop_kind"]) == ("rate_limited", "rate_limit")
    assert row["retry_at"] == (moment + timedelta(seconds=30)).isoformat()
    assert not it.events("work_item_needs_human")
