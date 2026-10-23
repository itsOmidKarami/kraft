import json

import httpx

from kraft.remote.forwarder import Forwarder


async def test_a_loopback_http_call_becomes_a_handler_call_and_its_answer_comes_back():
    seen = []

    async def handler(method, target, headers, body):
        seen.append((method, target, headers.get("content-type"), json.loads(body)))
        return 201, {"content-type": "application/json"}, b'{"ok": true}'

    fwd = Forwarder(handler)
    port = await fwd.start()
    async with httpx.AsyncClient() as c:
        reply = await c.post(
            f"http://127.0.0.1:{port}/api/work-items/w1/progress?x=1", json={"task": "n.main.a"}
        )
    await fwd.close()
    assert reply.status_code == 201 and reply.json() == {"ok": True}
    assert seen == [
        ("POST", "/api/work-items/w1/progress?x=1", "application/json", {"task": "n.main.a"})
    ]


async def test_the_session_mcp_route_is_answered_501_and_never_forwarded():
    async def handler(*args):
        raise AssertionError("the MCP route must not be forwarded")

    fwd = Forwarder(handler)
    port = await fwd.start()
    async with httpx.AsyncClient() as c:
        reply = await c.post(f"http://127.0.0.1:{port}/mcp", json={})
    await fwd.close()
    assert reply.status_code == 501 and "not forwarded" in reply.json()["detail"]


async def test_a_handler_that_fails_is_a_502_not_a_hang():
    async def handler(*args):
        raise RuntimeError("boom")

    fwd = Forwarder(handler)
    port = await fwd.start()
    async with httpx.AsyncClient() as c:
        reply = await c.get(f"http://127.0.0.1:{port}/api/work-items/w1")
    await fwd.close()
    assert reply.status_code == 502
