"""`dev/seed.py`'s pause must not depend on how fast the other items settle.

The KRAFT_SLOW item can only be paused while its fake agent sleeps. Settled in
filing order, it waited behind every other item, so the pause landed only when
they happened to settle inside that sleep (Task 6b review, finding 6). Paused
items are handled first, and `pause_mid_flight` waits on the item's own running
session, bounded, rather than on a margin.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "seed.py"


def _seed():
    spec = importlib.util.spec_from_file_location("_dev_seed", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_paused_item_is_settled_before_anything_it_would_otherwise_wait_behind():
    created = [
        ("a", "slow to settle", "completed"),
        ("b", "fails", "failed"),
        ("c", "the slow one", "paused"),
        ("d", "a gate", "gate"),
    ]
    order = [wid for wid, _title, _want in _seed().settle_order(created)]
    assert order[0] == "c"
    assert order[1:] == ["a", "b", "d"]


class _Answer:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return self

    def json(self):
        return self.body


class _NeverRan:
    """A server whose item was filed paused at capacity: no session, ever."""

    def __init__(self):
        self.posts = []

    def get(self, path):
        if path.endswith("/events"):
            return _Answer([])
        return _Answer({"status": "paused", "worker_sessions": []})

    def post(self, path):
        self.posts.append(path)
        return _Answer({})


def test_a_paused_item_that_never_ran_is_reported_as_never_started_not_as_paused():
    client = _NeverRan()
    assert _seed().pause_mid_flight(client, "w1", timeout=0.5) == "never started"
    assert client.posts == []
