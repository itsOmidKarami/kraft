"""The wire's envelope: what a refused call reads as at either front door."""

from __future__ import annotations

import asyncio

import httpx
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


HOSTILE = "../../etc/passwd"
ENCODED = "..%2F..%2Fetc%2Fpasswd"


@pytest.fixture
def sent(monkeypatch):
    """Every request path the client puts on the wire, as sent, each one
    answered `{}`."""
    paths = []

    def answer(request):
        paths.append(request.url.raw_path.decode())
        return httpx.Response(200, json={})

    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    monkeypatch.setenv("KRAFT_CLIENT", "cli")
    monkeypatch.setattr(
        client.transport,
        "http",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://k"),
    )
    return paths


@pytest.mark.parametrize(
    ("call", "path"),
    [
        (lambda: client.get_work_item(HOSTILE), f"/api/work-items/{ENCODED}"),
        (lambda: client.pause(work_item_id=HOSTILE), f"/api/work-items/{ENCODED}/pause"),
        (lambda: client.events(HOSTILE), f"/api/work-items/{ENCODED}/events"),
        (
            lambda: client.approve_gate(work_item_id=HOSTILE, gate=".."),
            f"/api/work-items/{ENCODED}/gates/%2E%2E/approve",
        ),
        (lambda: client.reply_to_thread(HOSTILE, "b"), f"/api/threads/{ENCODED}/replies"),
        (lambda: client.resolve_thread(".."), "/api/threads/%2E%2E/resolve"),
        (lambda: client.document("attachment:w1:spec"), "/api/documents/attachment%3Aw1%3Aspec"),
        (lambda: client.harness(HOSTILE), f"/api/harnesses/profiles/{ENCODED}"),
    ],
    ids=["item", "action", "events", "gate", "thread", "dot-segment", "doc", "profile"],
)
def test_an_id_is_one_path_segment_whatever_it_holds(sent, call, path):
    """Unencoded, httpx resolved the dot segments before sending: MCP's
    `get_work_item("../../etc/passwd")` became `GET /etc/passwd`, and
    `x/../<other>` reached another item's route."""
    asyncio.run(call())
    assert sent[0].split("?")[0] == path
