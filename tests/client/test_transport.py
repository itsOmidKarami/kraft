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


@pytest.mark.parametrize(
    "body, line",
    [
        ({"detail": "unknown work item"}, "unknown work item"),
        (
            {
                "detail": [
                    {
                        "type": "literal_error",
                        "loc": ["body", "label"],
                        "msg": "Input should be 'nit'",
                    },
                    {"type": "missing", "loc": ["body", "body"], "msg": "Field required"},
                    {
                        "type": "int_parsing",
                        "loc": ["query", "after"],
                        "msg": "Input should be an int",
                    },
                ]
            },
            "label: Input should be 'nit'; body: Field required; after: Input should be an int",
        ),
        ("plain text", "plain text"),
    ],
    ids=["kraft-detail", "pydantic-list", "not-json-object"],
)
def test_an_answers_reason_is_one_line(body, line):
    """FastAPI's 422 for a body that does not fit is a list of pydantic
    errors; it reached the CLI and MCP as a Python repr."""
    assert client.detail_of(body) == line


def test_a_models_own_refusal_reads_without_pydantics_prefix():
    """`kraft item comment --lines 4-2` printed `422: Value error, start_line
    must be ...`: the sentence is Kraft's, the "Value error, " pydantic's."""
    body = {
        "detail": [
            {
                "type": "value_error",
                "loc": ["body"],
                "msg": "Value error, start_line must be >= 1 and <= end_line",
            }
        ]
    }
    assert client.detail_of(body) == "start_line must be >= 1 and <= end_line"


def test_a_connect_the_server_has_not_answered_in_time_says_it_may_still_save(monkeypatch):
    """The client gave up at 30 s while the probe ran on for up to 120, then
    saved: a raw ReadTimeout, then "already connected"."""
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
