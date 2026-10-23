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
    ("GET", "/api/work-items/{}/events"),
    ("POST", "/api/work-items/{}/progress"),
    ("POST", "/api/work-items/{}/retry"),
]
#: A session launched before per-item ids, which the flat routes are kept for.
_OLD = "8cbfe6e27c1044b3e445c0f0d726357d"
_OWN_SESSION = [
    ("POST", "/api/worker-sessions/{}/permission", _OLD, True),
    ("POST", "/api/worker-sessions/{}/permission-hook", _OLD, True),
    ("POST", "/api/worker-sessions/{}/permission", "p3n8w2", False),
    ("POST", "/api/worker-sessions/{}/permission-hook", "p3n8w2", False),
]


@pytest.mark.parametrize(("method", "path"), _OWN_ITEM, ids=[p for _, p in _OWN_ITEM])
def test_an_item_route_is_allowed_for_the_session_own_item_only(method, path):
    assert _allowed(method, path.format("w-own"))
    assert not _allowed(method, path.format("w-other"))


@pytest.mark.parametrize(
    ("method", "path", "sid", "own"),
    _OWN_SESSION,
    ids=[
        "/api/worker-sessions/{}/permission",
        "/api/worker-sessions/{}/permission-hook",
        "per-item-session-permission",
        "per-item-session-permission-hook",
    ],
)
def test_a_session_route_is_allowed_for_the_session_itself_only(method, path, sid, own):
    """The flat route names a session by its id alone, so it is a session's
    own only for one launched before per-item ids, whose 32 characters are
    unique; a per-item session's id is another item's too, and its channel
    asks under its item instead."""
    scope = callback.SessionScope(sid, "w-own")
    assert callback.allowed(scope, method, path.format(sid), thread_owner=_THREADS.get) is own
    assert not callback.allowed(scope, method, path.format("s-other"), thread_owner=_THREADS.get)


_OWN_NESTED = [
    ("POST", "/api/work-items/{}/sessions/{}/permission"),
    ("POST", "/api/work-items/{}/sessions/{}/permission-hook"),
]


@pytest.mark.parametrize(("method", "path"), _OWN_NESTED, ids=[p for _, p in _OWN_NESTED])
def test_a_nested_session_route_is_allowed_for_the_session_itself_only(method, path):
    """Both segments, each matched whole: another item's session with the
    same id, another session of its own item, and a start of either id (the
    daemon would resolve an item's) are all someone else's."""
    assert _allowed(method, path.format("w-own", "s-own"))
    assert not _allowed(method, path.format("w-other", "s-own"))
    assert not _allowed(method, path.format("w-own", "s-other"))
    assert not _allowed(method, path.format("w-o", "s-own"))
    assert not _allowed(method, path.format("w-own", "s-o"))


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
        ("POST", "/api/work-items/w-own/events"),
        # Routes not on the list at all, even for the session's own item.
        ("POST", "/api/work-items/w-own/skip"),
        # Design §6 rule 2: never a person's decision on its own item, nor
        # work it files (Kraft-9efnk.14).
        ("POST", "/api/work-items/w-own/gates/review/approve"),
        ("POST", "/api/work-items/w-own/gates/review/reject"),
        ("POST", "/api/work-items/w-own/pause"),
        ("POST", "/api/work-items/w-own/resume"),
        ("POST", "/api/work-items"),
        ("GET", "/api/work-items"),
        ("GET", "/api/worker-sessions/s-own/log"),
        ("GET", "/api/work-items/w-own/sessions/s-own/log"),
        ("POST", "/api/work-items/w-own/sessions/s-own/permission/extra"),
        ("POST", "/api/work-items/w-own/sessions/w-own%2Fs-own/permission"),
        ("POST", "/api/threads/t-own/comments"),
        # Host git on a live sandboxed worktree is refused anyway (D16).
        ("GET", "/api/work-items/w-own/diff"),
        ("GET", "/api/work-items/w-own/compare"),
        # Shaped like a listed route, but not one path segment of plain id.
        ("POST", "/api/work-items/w-own/progress/extra"),
        ("POST", "/api/work-items/w-other/../w-own/progress"),
        ("POST", "/api/work-items/w-own%2F..%2Fw-other/progress"),
        ("GET", "/mcp/../api/work-items"),
        # Machines are a person's: no session lists, adds or removes one, or
        # dials in as one.
        ("GET", "/api/workers"),
        ("POST", "/api/workers"),
        ("DELETE", "/api/workers/gpu-box"),
        ("GET", "/api/workers/connect"),
    ],
)
def test_anything_else_is_refused(method, path):
    assert not _allowed(method, path)
