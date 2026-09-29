"""`kraft.worker.callback`: the one route allowlist a worker's callbacks go
through, for its own session and item and nothing else."""

from __future__ import annotations

import pytest

from kraft.worker import callback

SCOPE = callback.SessionScope("s-own", "w-own")
_THREADS = {"t-own": "w-own", "t-other": "w-other"}


def _allowed(method, path):
    return callback.allowed(SCOPE, method, path, thread_owner=_THREADS.get)


_OWN_ITEM = [
    ("GET", "/api/work-items/{}"),
    ("GET", "/api/work-items/{}/threads"),
    ("GET", "/api/work-items/{}/diff"),
    ("GET", "/api/work-items/{}/compare"),
    ("POST", "/api/work-items/{}/progress"),
    ("POST", "/api/work-items/{}/retry"),
]
_OWN_SESSION = [
    ("POST", "/api/worker-sessions/{}/permission"),
    ("POST", "/api/worker-sessions/{}/permission-hook"),
]


@pytest.mark.parametrize(("method", "path"), _OWN_ITEM, ids=[p for _, p in _OWN_ITEM])
def test_an_item_route_is_allowed_for_the_session_own_item_only(method, path):
    assert _allowed(method, path.format("w-own"))
    assert not _allowed(method, path.format("w-other"))


@pytest.mark.parametrize(("method", "path"), _OWN_SESSION, ids=[p for _, p in _OWN_SESSION])
def test_a_session_route_is_allowed_for_the_session_itself_only(method, path):
    assert _allowed(method, path.format("s-own"))
    assert not _allowed(method, path.format("s-other"))


@pytest.mark.parametrize(
    ("thread", "expected"),
    [("t-own", True), ("t-other", False), ("t-gone", False)],
    ids=["own-item", "another-item", "no-such-thread"],
)
def test_a_reply_is_allowed_only_on_a_thread_of_the_session_own_item(thread, expected):
    assert _allowed("POST", f"/api/threads/{thread}/replies") is expected


@pytest.mark.parametrize("method", ["POST", "GET", "DELETE"])
def test_the_mcp_endpoint_is_the_channel_session_own(method):
    assert _allowed(method, "/mcp")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        # The right path, the wrong method: a write where only a read is listed.
        ("POST", "/api/work-items/w-own"),
        ("DELETE", "/api/work-items/w-own/threads"),
        # Routes not on the list at all, even for the session's own item.
        ("POST", "/api/work-items/w-own/skip"),
        ("POST", "/api/work-items/w-own/gates/review/approve"),
        ("GET", "/api/work-items"),
        ("GET", "/api/worker-sessions/s-own/log"),
        ("POST", "/api/threads/t-own/comments"),
        # Shaped like a listed route, but not one path segment of plain id.
        ("POST", "/api/work-items/w-own/progress/extra"),
        ("POST", "/api/work-items/w-other/../w-own/progress"),
        ("POST", "/api/work-items/w-own%2F..%2Fw-other/progress"),
        ("GET", "/mcp/../api/work-items"),
    ],
)
def test_anything_else_is_refused(method, path):
    assert not _allowed(method, path)
