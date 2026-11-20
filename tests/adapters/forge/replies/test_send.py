"""One posting point's sends: the class of a failed write, and what a send does with
each class, within its budgets."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from support.harness import v1_node, v1_task

from kraft import events
from kraft.adapters.forge.git import CliUnavailable
from kraft.adapters.forge.models import ForgeError, Posted
from kraft.adapters.forge.replies import record, send
from kraft.adapters.forge.replies.send import classify

from .test_record import _PAYLOAD


@pytest.mark.parametrize(
    "exc, failure",
    [
        (ForgeError("gh api … timed out after 120s"), "unclear"),
        (ForgeError("POST x: gh printed no HTTP response"), "unclear"),
        (ForgeError("POST x: did not answer in 120s", reason="unreachable"), "unclear"),
        (ForgeError("x (HTTP 502)", status=502, reason="unreachable"), "unclear"),
        (
            ForgeError("x (HTTP 429)", status=429, reason="rate_limited", retry_after=30),
            "rate_limited",
        ),
        (ForgeError("x (HTTP 403)", status=403, reason="rate_limited"), "rate_limited"),
        (ForgeError("graphql: slow down", status=200, reason="rate_limited"), "rate_limited"),
        (CliUnavailable("gh is not installed or not on PATH"), "deferred"),
        (ForgeError("x (HTTP 401)", status=401, reason="auth"), "deferred"),
        (ForgeError("x", reason="tls"), "deferred"),
        (
            ForgeError(
                "x (HTTP 403)", status=403, reason="forbidden", sso_url="https://github.com/sso"
            ),
            "deferred",
        ),
        (
            ForgeError("POST x: To get started with GitHub CLI, please run:  gh auth login"),
            "deferred",
        ),
        (ForgeError("POST x: could not resolve host: api.github.com"), "deferred"),
        (ForgeError("POST x: connection refused"), "deferred"),
        (ForgeError("POST x: connection timed out"), "unclear"),
        (
            ForgeError(
                "x: Validation Failed; user_id can only have one pending review per pull "
                "request (HTTP 422)",
                status=422,
            ),
            "deferred",
        ),
        (ForgeError("x (HTTP 422)", status=422), "refused"),
        (ForgeError("x (HTTP 400)", status=400), "refused"),
        (ForgeError("x (HTTP 403)", status=403, reason="forbidden"), "refused"),
        (ForgeError("x (HTTP 404)", status=404, reason="not_found"), "refused"),
        (ForgeError("x (HTTP 409)", status=409, reason="conflict"), "refused"),
        (ForgeError("x (HTTP 410)", status=410), "refused"),
        (ForgeError("x (HTTP 413)", status=413), "refused"),
        (ForgeError("graphql: not allowed", status=200, reason="forbidden"), "refused"),
        (ForgeError("graphql: odd", status=200), "refused"),
    ],
    ids=[
        "cli-timeout",
        "no-http-response",
        "http-timeout",
        "5xx",
        "429",
        "403-rate-limit",
        "graphql-rate-limit",
        "cli-never-started",
        "401",
        "tls",
        "403-sso",
        "not-signed-in",
        "dns-failure",
        "connection-refused",
        "connection-timed-out",
        "422-pending-review",
        "422-other",
        "400",
        "403-forbidden",
        "404",
        "409",
        "410",
        "413",
        "graphql-forbidden",
        "graphql-unknown",
    ],
)
def test_a_failed_write_is_classified(exc, failure):
    assert classify(exc) == failure


_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_PRIOR = {  # what `intend(prior=...)` records after the intent
    "unclear": lambda c, w, i: record.send_failed(c, w, i, "unclear", ForgeError("timed out")),
    "posted": lambda c, w, i: record.posted(c, w, i, Posted(("1", "N1"), "t", "b"), via="send"),
    "refused": lambda c, w, i: record.ended(c, w, i, "refused", "x"),
}
_UNCLEAR = ForgeError("POST x: timed out after 120s")
_DEFERRED = ForgeError("POST x: could not resolve host: api.github.com")
_LIMITED_30 = ForgeError("x (HTTP 429)", status=429, reason="rate_limited", retry_after=30)
_LIMITED_0 = ForgeError("x (HTTP 429)", status=429, reason="rate_limited", retry_after=0)
_REFUSED_404 = ForgeError("x (HTTP 404)", status=404, reason="not_found")


class _Point:
    def __init__(self, it, monkeypatch):
        self.it, self.db, self.item, self.t, self.slept = it, it.database, it.id, 0.0, []
        monkeypatch.setattr(send, "_clock", lambda: self.t)
        monkeypatch.setattr(send, "_now", lambda: _T0 + timedelta(seconds=self.t))
        monkeypatch.setattr(events, "_now", lambda: (_T0 + timedelta(seconds=self.t)).isoformat())
        monkeypatch.setattr(send, "_sleep", self._sleep)
        monkeypatch.setattr(send, "_last_write", {})
        monkeypatch.setattr(send, "_locks", {})
        self.pp = send.PostingPoint(self.db, self.item, account="github.com")

    async def _sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds

    async def intend(self, prior=None):
        intent = record.new_id(self.item)
        await self.db.write(lambda c: record.intend(c, self.item, intent, _PAYLOAD))
        if prior:
            await self.db.write(lambda c: _PRIOR[prior](c, self.item, intent))
        return self.reread(intent)

    def reread(self, intent):
        key = intent if isinstance(intent, str) else intent.id
        return self.db.read(lambda c: record.read(c, self.item))[key]

    def advance(self, seconds):
        self.t += seconds

    def later(self, seconds):
        """A later posting point, as in production: a new one, with a fresh budget."""
        self.t += seconds
        self.pp = send.PostingPoint(self.db, self.item, account="github.com")


@pytest.fixture
async def point(item_on, monkeypatch):
    return _Point(await item_on([v1_node("n", tasks=[v1_task("t")])]), monkeypatch)


async def _ok():
    return Posted(("1", "N1"), "t", "b")


def _fails(exc):
    async def write():
        raise exc

    return write


def _counting(calls):
    async def write():
        calls.append(1)
        return await _ok()

    return write


def _says(verdict):
    async def gate(intent):
        return verdict

    return gate


_go_ahead = _says(None)


async def _never(*args):
    raise AssertionError("must not be awaited")


@pytest.mark.parametrize(
    "prior", ["unclear", "posted", "refused"], ids=["unclear", "posted", "ended"]
)
async def test_send_skips_an_intent_that_is_not_pending(point, prior):
    intent = await point.intend(prior=prior)
    calls = []

    got = await point.pp.send(intent, gate=_go_ahead, write=_counting(calls))

    assert (got, calls) == ("skipped", [])


@pytest.mark.parametrize(
    "verdict, outcome",
    [(("refused", "a link"), "refused"), (("superseded", "new head"), "superseded")],
    ids=["refused", "superseded"],
)
async def test_the_gate_ends_an_intent_it_will_not_send(point, verdict, outcome):
    intent = await point.intend()

    got = await point.pp.send(intent, gate=_says(verdict), write=_never)

    assert got == outcome
    assert point.reread(intent).state == outcome


async def test_counted_sends_and_backoff(point):
    """Unclear and rate-limited sends count, three at most; a deferred one never
    counts but waits five minutes; a rate limit waits its retry_after. Each wait
    is crossed at a later posting point, as in production."""
    intent = await point.intend()

    async def send_(write):
        return await point.pp.send(point.reread(intent), gate=_go_ahead, write=write)

    assert await send_(_fails(_DEFERRED)) == "deferred"
    assert await send_(_ok) == "waiting"
    point.later(301)
    assert await send_(_fails(_LIMITED_30)) == "rate_limited"
    assert await send_(_ok) == "waiting"
    point.later(31)
    for _ in range(2):
        assert await send_(_fails(_LIMITED_0)) == "rate_limited"
    assert point.reread(intent).sends == 3
    assert await send_(_ok) == "given_up"
    assert point.reread(intent).state == "given_up"


@pytest.mark.parametrize(
    "exc, sent, state",
    [
        (None, "posted", "posted"),
        (_UNCLEAR, "unclear", "unclear"),
        (_REFUSED_404, "refused", "refused"),
        (OSError("broken pipe"), "unclear", "unclear"),
    ],
    ids=["posted", "unclear", "refused", "an-os-error-is-unclear"],
)
async def test_a_send_records_its_outcome(point, exc, sent, state):
    intent = await point.intend()

    got = await point.pp.send(intent, gate=_go_ahead, write=_ok if exc is None else _fails(exc))

    after = point.reread(intent)
    assert (got, after.state) == (sent, state)
    if exc is None:
        assert after.posted["ids"] == ["1", "N1"] and after.posted["via"] == "send"


@pytest.mark.parametrize(
    "accounts, slept",
    [(("github.com", "github.com"), [1.0]), (("github.com", "acct-b"), [])],
    ids=["same-account-waits-a-second", "another-account-does-not"],
)
async def test_writes_are_spaced_per_account(point, accounts, slept):
    for account in accounts:
        pp = send.PostingPoint(point.db, point.item, account=account)
        await pp.send(await point.intend(), gate=_go_ahead, write=_ok)

    assert point.slept == slept


async def test_a_crash_mid_send_leaves_the_intent_unclear(point):
    """A cancel (or a restart) after the write landed and before its outcome was
    recorded: the intent reads unclear, so the next posting point reconciles it
    before anything is resent."""
    intent = await point.intend()

    with pytest.raises(asyncio.CancelledError):
        await point.pp.send(intent, gate=_go_ahead, write=_fails(asyncio.CancelledError()))

    after = point.reread(intent)
    assert (after.state, after.sends) == ("unclear", 0)


async def test_a_posting_point_stops_sending_after_its_time_budget(point):
    first, second = await point.intend(), await point.intend()
    assert await point.pp.send(first, gate=_go_ahead, write=_ok) == "posted"
    point.advance(60)
    calls = []

    assert await point.pp.send(second, gate=_go_ahead, write=_counting(calls)) == "out_of_time"

    assert calls == [] and point.reread(second).state == "pending"
