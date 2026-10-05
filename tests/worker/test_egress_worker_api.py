"""`kraft.worker.egress`'s worker API: host `kraft` through a session's
channel, served in-process as that session and only on its own item (spec
§5). The daemon's app is a stand-in that records every call it gets; its
threads live in a one-table database."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from kraft.worker import egress


class _Api:
    """The daemon's app: every call recorded, each answered `{"ok": path}`
    unless `answers` says otherwise."""

    def __init__(self):
        self.calls: list[dict] = []
        self.answers: dict[str, tuple[int, object]] = {}
        #: Seconds a path takes to answer.
        self.slow: dict[str, float] = {}
        #: When set, every call waits for it before answering.
        self.release: asyncio.Event | None = None
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE review_threads (id TEXT, work_item_id TEXT)")
        conn.executemany(
            "INSERT INTO review_threads VALUES (?, ?)",
            [("t-own", "w-own"), ("t-other", "w-other")],
        )
        self.app = Starlette(routes=[Route("/{path:path}", self._record, methods=["GET", "POST"])])
        self.app.state.mcp_token = "daemon-token"
        self.app.state.db = SimpleNamespace(read=lambda fn: fn(conn))

    async def _record(self, request):
        body = await request.body()
        self.calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "query": dict(request.query_params),
                "json": json.loads(body) if body else None,
                "session": request.headers.get("x-kraft-session-id"),
                "authorization": request.headers.get("authorization"),
            }
        )
        await asyncio.sleep(self.slow.get(request.url.path, 0))
        if self.release is not None:
            await self.release.wait()
        status, payload = self.answers.get(request.url.path, (200, {"ok": request.url.path}))
        return JSONResponse(payload, status_code=status)


@pytest.fixture
async def worker_api():
    """`(api, ask)`: `await ask(verb, form, headers=...)` posts `form` to
    `http://kraft/w/<verb>` through one session's channel (session `s-own`
    on item `w-own`) and returns `(status, body, events)`."""
    api = _Api()
    proxy = egress.EgressProxy(app=api.app, environ={})
    events: list[dict] = []

    async def record(payload):
        events.append(payload)

    session = egress.EgressSession(
        "s-own", egress.PhaseLists("runtime", ("**",), ()), record, "w-own"
    )
    server = await asyncio.start_server(lambda r, w: proxy.handle(r, w, session), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def ask(verb, form=None, *, method="POST", target=None, headers=()):
        body = urlencode(form or {}).encode()
        head = [
            f"{method} {target or f'http://kraft/w/{verb}'} HTTP/1.1",
            "Host: kraft",
            "Content-Type: application/x-www-form-urlencoded",
            f"Content-Length: {len(body)}",
            *headers,
        ]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write("\r\n".join(head).encode() + b"\r\n\r\n" + body)
        answer = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        status_line, _, rest = answer.partition(b"\r\n")
        return int(status_line.split()[1]), rest.partition(b"\r\n\r\n")[2], events

    yield api, ask
    server.close()


@pytest.mark.parametrize(
    ("verb", "form", "call"),
    [
        ("progress", {"task": "3"}, ("POST", "/api/work-items/w-own/progress", {"task": "3"})),
        ("retry", {}, ("POST", "/api/work-items/w-own/retry", {})),
        ("retry", {"steer": " go "}, ("POST", "/api/work-items/w-own/retry", {"steer": "go"})),
        (
            "reply",
            {"thread": "t-own", "body": "a & b=c", "claim": "fixed"},
            ("POST", "/api/threads/t-own/replies", {"body": "a & b=c", "claim": "fixed"}),
        ),
        ("show", {"id": "w-own"}, ("GET", "/api/work-items/w-own", None)),
        ("threads", {}, ("GET", "/api/work-items/w-own/threads", None)),
        ("events", {}, ("GET", "/api/work-items/w-own/events", None)),
        (
            "permission-hook",
            {"harness": "cursor", "stdin": '{"tool_name": "Shell"}'},
            (
                "POST",
                "/api/worker-sessions/s-own/permission-hook",
                {"harness": "cursor", "stdin": '{"tool_name": "Shell"}'},
            ),
        ),
    ],
    ids=["progress", "retry", "retry-steered", "reply", "show", "threads", "events", "hook"],
)
async def test_a_verb_is_its_api_call_made_as_the_channel_session(worker_api, verb, form, call):
    """The session and the credential are the channel's and the daemon's,
    whatever the worker sent in their place."""
    api, ask = worker_api
    api.answers["/api/worker-sessions/s-own/permission-hook"] = (200, {"body": "{}", "code": 0})
    status, _, events = await ask(
        verb,
        form,
        headers=["Authorization: Bearer stolen", "X-Kraft-Session-Id: s-other"],
    )
    assert status == 200
    [seen] = api.calls
    assert (seen["method"], seen["path"], seen["json"]) == call
    assert (seen["session"], seen["authorization"]) == ("s-own", "Bearer daemon-token")
    assert events == []


@pytest.mark.parametrize(
    ("verb", "form", "route"),
    [
        ("reply", {"thread": "t-other", "body": "x"}, "POST /api/threads/t-other/replies"),
        ("reply", {"thread": "t-gone", "body": "x"}, "POST /api/threads/t-gone/replies"),
        ("show", {"id": "w-other"}, "GET /api/work-items/w-other"),
        ("events", {"id": "w-other"}, "GET /api/work-items/w-other/events"),
        ("progress", {"task": "1", "id": "w-other"}, "POST /api/work-items/w-other/progress"),
        ("progress", {"task": "1", "id": "w-own/../w-other"}, None),
    ],
    ids=[
        "another-item-thread",
        "no-such-thread",
        "another-item",
        "another-item-events",
        "another-item-act",
        "traversal",
    ],
)
async def test_another_item_is_refused_and_recorded(worker_api, verb, form, route):
    api, ask = worker_api
    for _ in range(2):
        status, body, events = await ask(verb, form)
        assert status == 403, body
    assert api.calls == []
    [event] = events
    assert event["reason"] == "not this session's to call"
    assert event["route"] == (route or f"POST /api/work-items/{form['id']}/progress")


@pytest.mark.parametrize(
    ("method", "target", "route"),
    [
        ("POST", "http://kraft/w/nonsense", "POST /w/nonsense"),
        ("GET", "http://kraft/w/show", "GET /w/show"),
        ("POST", "http://kraft/api/work-items/w-own/skip", "POST /api/work-items/w-own/skip"),
    ],
    ids=["unknown-verb", "not-a-post", "the-api-itself"],
)
async def test_what_is_not_a_worker_verb_is_refused_and_recorded(worker_api, method, target, route):
    api, ask = worker_api
    status, _, events = await ask(None, method=method, target=target)
    assert status == 403
    assert api.calls == []
    assert [(e["route"], e["reason"]) for e in events] == [(route, "not a worker API verb")]


async def test_a_tunnel_to_kraft_is_refused(worker_api):
    api, ask = worker_api
    status, body, _ = await ask(None, method="CONNECT", target="kraft:80")
    assert status == 403
    assert b"it is plain http://kraft" in body
    assert api.calls == []


@pytest.mark.parametrize(
    ("answer", "status", "body"),
    [
        ((200, {"body": '{"permission": "deny"}', "code": 0}), 200, b'{"permission": "deny"}'),
        ((200, {"body": "{}", "code": 1}), 502, None),
        ((404, {"detail": "unknown session"}), 502, None),
    ],
    ids=["answered", "non-zero-code", "api-error"],
)
async def test_the_hook_verb_returns_the_hook_body_or_an_error(worker_api, answer, status, body):
    """The shim prints a 200's body as the hook's answer and fails closed on
    anything else, so only an answered hook may come back as a 200."""
    api, ask = worker_api
    api.answers["/api/worker-sessions/s-own/permission-hook"] = answer
    got, text, _ = await ask("permission-hook", {"harness": "cursor", "stdin": "{}"})
    assert got == status
    if body is not None:
        assert text == body


async def test_threads_open_leaves_out_the_resolved(worker_api):
    api, ask = worker_api
    threads = [{"id": "a", "state": "open"}, {"id": "b", "state": "resolved"}]
    api.answers["/api/work-items/w-own/threads"] = (200, threads)
    _, body, _ = await ask("threads", {"open": "1"})
    assert [t["id"] for t in json.loads(body)] == ["a"]


@pytest.mark.parametrize(
    ("form", "seqs"),
    [
        ({"after": "1"}, [2, 3]),
        ({"type": "judge_verdict"}, [1, 3]),
        ({"after": "1", "type": "judge_verdict"}, [3]),
    ],
    ids=["after", "type", "both"],
)
async def test_events_after_and_type_leave_out_the_rest(worker_api, form, seqs):
    """Cut from the answer, not asked of the API: the call carries no query."""
    api, ask = worker_api
    rows = [
        {"seq": 1, "type": "judge_verdict"},
        {"seq": 2, "type": "mr_opened"},
        {"seq": 3, "type": "judge_verdict"},
    ]
    api.answers["/api/work-items/w-own/events"] = (200, rows)
    _, body, _ = await ask("events", form)
    assert [e["seq"] for e in json.loads(body)] == seqs
    assert [c["query"] for c in api.calls] == [{}]


@pytest.mark.parametrize("after", ["-1", "x", "9" * 5000], ids=["negative", "word", "huge"])
async def test_events_with_an_after_that_is_no_whole_number_is_a_400(worker_api, after):
    api, ask = worker_api
    status, body, events = await ask("events", {"after": after})
    assert status == 400, body
    assert api.calls == []
    assert events == []


async def test_the_mcp_endpoint_takes_only_its_transport_methods(worker_api):
    api, ask = worker_api
    status, _, events = await ask(None, method="PUT", target="http://kraft/mcp")
    assert status == 403
    assert api.calls == []
    assert [(e["route"], e["reason"]) for e in events] == [
        ("PUT /mcp", "not this session's to call")
    ]


async def test_the_mcp_endpoint_streams_nothing(worker_api):
    """Stateless: no event stream to open, so a GET is answered at once
    rather than held open for good."""
    _, ask = worker_api
    status, body, _ = await ask(None, method="GET", target="http://kraft/mcp")
    assert (status, body) == (405, b"kraft: the session MCP server streams nothing\n")


async def test_a_call_the_daemon_does_not_answer_in_time_is_a_504(worker_api, monkeypatch):
    api, ask = worker_api
    monkeypatch.setattr(egress, "CALL_TIMEOUT", 0.2)
    api.slow["/api/work-items/w-own/progress"] = 3
    status, _, _ = await ask("progress", {"task": "1"})
    assert status == 504


async def test_a_call_past_the_sessions_limit_waits_for_one_to_finish(worker_api):
    api, ask = worker_api
    api.release = asyncio.Event()
    calls = [asyncio.create_task(ask("progress", {"task": "1"})) for _ in range(5)]

    async def held():
        while len(api.calls) < egress.MAX_WORKER_CALLS:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(held(), 5)
    await asyncio.sleep(0.2)
    assert len(api.calls) == egress.MAX_WORKER_CALLS
    api.release.set()
    assert [status for status, _, _ in await asyncio.gather(*calls)] == [200] * 5
