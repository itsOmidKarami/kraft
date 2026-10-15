"""The wire's envelope: what a refused call reads as at either front door."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

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


def _serve(monkeypatch, handler) -> None:
    """Point the client at `handler` instead of a server."""
    monkeypatch.setattr(
        client.transport,
        "http",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://k"),
    )


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
    _serve(monkeypatch, answer)
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


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, client.Failure.UNAUTHORIZED),
        (404, client.Failure.NOT_FOUND),
        (409, client.Failure.REFUSED),
        (422, client.Failure.REFUSED),
        (500, client.Failure.SERVER_ERROR),
    ],
    ids=["401", "404", "409", "422", "500"],
)
def test_a_refused_call_says_what_kind_of_refusal_it_is(monkeypatch, status, kind):
    """The console tells gone from refused from broken by kind, never by
    parsing the sentence; the sentence is the one the CLI always printed."""
    _serve(monkeypatch, lambda request: httpx.Response(status, json={"detail": "no"}))
    with pytest.raises(client.ClientError) as caught:
        asyncio.run(client.get_work_item("w1"))
    assert (caught.value.kind, caught.value.status, str(caught.value)) == (
        kind,
        status,
        f"kraft {status}: no",
    )


def _raising(exc_type):
    def handler(request):
        raise exc_type("down", request=request)

    return handler


@pytest.mark.parametrize(
    ("handler", "kind", "said"),
    [
        (_raising(httpx.ConnectError), client.Failure.UNREACHABLE, "no Kraft server at "),
        (
            _raising(httpx.ReadTimeout),
            client.Failure.INTERRUPTED,
            "Kraft did not answer GET /work-items/w1 in time; it may still finish it",
        ),
        (
            _raising(httpx.ReadError),
            client.Failure.INTERRUPTED,
            "lost the connection to Kraft during GET /work-items/w1; it may still finish it",
        ),
        (
            _raising(httpx.RemoteProtocolError),
            client.Failure.INTERRUPTED,
            "lost the connection to Kraft during GET /work-items/w1; it may still finish it",
        ),
        (
            lambda request: httpx.Response(200, text="<html>proxy</html>"),
            client.Failure.BAD_RESPONSE,
            "kraft 200: the answer was not JSON",
        ),
    ],
    ids=["connect", "timeout", "dropped", "protocol", "not-json"],
)
def test_a_call_with_no_usable_answer_is_one_sentence(monkeypatch, handler, kind, said):
    """A server restarting under a call, or a proxy answering for it, reached
    the CLI and MCP as an httpx or JSON traceback."""
    _serve(monkeypatch, handler)
    with pytest.raises(client.ClientError) as caught:
        asyncio.run(client.get_work_item("w1"))
    assert caught.value.kind == kind
    assert said in str(caught.value)


def test_the_client_raises_no_bare_value_error():
    """The console catches `ClientError` and lets anything else crash as a
    bug, so a refusal raised as a plain ValueError would crash it."""
    bare = [
        f"{path.name}:{node.lineno}"
        for path in sorted(Path(client.__file__).parent.glob("*.py"))
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and getattr(node.exc.func, "id", None) == "ValueError"
    ]
    assert bare == []


@pytest.mark.parametrize(
    ("call", "kind"),
    [
        (lambda: client.health(), client.Failure.OTHER_INSTANCE),
        (lambda: client.reject_gate("  ", "spec_approval", "w1"), client.Failure.REFUSED),
        (lambda: client.get_work_item(), client.Failure.REFUSED),
    ],
    ids=["other-instance", "blank-note", "no-item"],
)
def test_a_refusal_the_client_makes_itself_is_a_client_error(monkeypatch, tmp_path, call, kind):
    """The client's own checks refuse before or after a request; the console
    tells them from a crash the same way it does a server's refusal."""
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    monkeypatch.chdir(tmp_path)  # not a Kraft worktree, so no item to default to
    _serve(monkeypatch, lambda request: httpx.Response(200, json={"run_dir": "/elsewhere"}))
    with pytest.raises(client.ClientError) as caught:
        asyncio.run(call())
    assert caught.value.kind == kind
