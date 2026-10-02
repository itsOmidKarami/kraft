"""The wire's envelope: what a refused call reads as at either front door."""

from __future__ import annotations

import pytest

from kraft import client


@pytest.mark.parametrize(
    "exc, line",
    [
        (ValueError("kraft 404: unknown work item"), "kraft: 404: unknown work item"),
        (ValueError("kraft: no gate is pending on w1"), "kraft: no gate is pending on w1"),
        (
            PermissionError("a worker session cannot act on its own work item (w1)."),
            "kraft: a worker session cannot act on its own work item (w1).",
        ),
    ],
    ids=["api-status", "client-check", "worker-guard"],
)
def test_a_refusal_reads_kraft_once(exc, line):
    """The CLI prints this line and MCP hands it to the agent. A message that
    already led with `kraft` once printed as `kraft: kraft 404: ...`."""
    assert client.refusal(exc) == line


def test_a_connect_the_server_has_not_answered_in_time_says_it_may_still_save(monkeypatch):
    """The client gave up at 30 s while the probe ran on for up to 120, then
    saved: a raw ReadTimeout, then "already connected"."""
    import asyncio

    import httpx

    from kraft import detect
    from kraft.client import actions

    async def slow(self, method, url, **kw):
        assert kw["timeout"] > detect._PROBE_TIMEOUT_S * 2
        raise httpx.ReadTimeout("slow", request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", slow)
    with pytest.raises(ValueError, match="check `kraft repo list`"):
        asyncio.run(actions.ensure_repo("/tmp"))
    with pytest.raises(ValueError, match="did not answer POST /repos/probe in time"):
        asyncio.run(actions.probe_repo("/tmp"))
