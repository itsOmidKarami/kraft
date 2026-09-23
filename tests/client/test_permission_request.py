"""client.permission_request in enforce mode reports a failure as
`unavailable`, never as a policy denial (Kraft-4in7z)."""

import asyncio

from kraft.client import reads


def test_enforce_without_a_session_is_unavailable(monkeypatch):
    monkeypatch.delenv("KRAFT_SESSION_ID", raising=False)
    got = asyncio.run(reads.permission_request("Bash", {}, mode="enforce"))
    assert got["behavior"] == "unavailable"


def test_prompt_without_a_session_still_denies(monkeypatch):
    monkeypatch.delenv("KRAFT_SESSION_ID", raising=False)
    assert asyncio.run(reads.permission_request("Bash", {}))["behavior"] == "deny"


def test_enforce_with_the_server_down_is_unavailable(monkeypatch):
    monkeypatch.setenv("KRAFT_SESSION_ID", "s1")

    async def down(*_a, **_k):
        raise ValueError("no server")

    monkeypatch.setattr(reads.transport, "_post", down)
    got = asyncio.run(reads.permission_request("Bash", {}, mode="enforce"))
    assert got["behavior"] == "unavailable"
