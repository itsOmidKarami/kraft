"""client.permission_request in enforce mode reports a failure as
`unavailable`, never as a policy denial (Kraft-4in7z)."""

import asyncio

import pytest

from kraft.client import reads

#: A session id minted before per-item ids.
_OLD = "8cbfe6e27c1044b3e445c0f0d726357d"


def test_enforce_without_a_session_is_unavailable(monkeypatch):
    monkeypatch.delenv("KRAFT_SESSION_ID", raising=False)
    got = asyncio.run(reads.permission_request("Bash", {}, mode="enforce"))
    assert got["behavior"] == "unavailable"


def test_prompt_without_a_session_still_denies(monkeypatch):
    monkeypatch.delenv("KRAFT_SESSION_ID", raising=False)
    assert asyncio.run(reads.permission_request("Bash", {}))["behavior"] == "deny"


def test_enforce_with_the_server_down_is_unavailable(monkeypatch):
    monkeypatch.setenv("KRAFT_SESSION_ID", "w1/s1")

    async def down(*_a, **_k):
        raise ValueError("no server")

    monkeypatch.setattr(reads.transport, "_post", down)
    got = asyncio.run(reads.permission_request("Bash", {}, mode="enforce"))
    assert got["behavior"] == "unavailable"


@pytest.mark.parametrize(
    ("ref", "path"),
    [
        ("k7m2x9qa/p3n8w2", "/work-items/k7m2x9qa/sessions/p3n8w2/permission"),
        (_OLD, f"/worker-sessions/{_OLD}/permission"),
        ("k7m2x9qa/", None),
        ("p3n8w2", None),
    ],
    ids=["under-its-item", "pre-upgrade-bare", "malformed", "bare-per-item-id"],
)
def test_the_ask_goes_to_the_session_kraft_session_id_names(monkeypatch, ref, path):
    """`<wid>/<sid>` is asked under its item; a bare id, from a session
    launched before per-item ids, by the flat route that still answers it;
    anything else is denied without asking, a bare id of any other length
    included: it is no session's on its own."""
    monkeypatch.setenv("KRAFT_SESSION_ID", ref)
    asked = []

    async def post(p, body, **kw):
        asked.append(p)
        return 200, {"behavior": "allow", "updatedInput": {}}

    monkeypatch.setattr(reads.transport, "_post", post)
    got = asyncio.run(reads.permission_request("Bash", {}))
    assert asked == ([path] if path else [])
    assert got["behavior"] == ("allow" if path else "deny")
