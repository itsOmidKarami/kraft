"""`dev/seed.py`'s pause must not depend on how fast the other items settle.

The KRAFT_SLOW item can only be paused while its fake agent sleeps. Settled in
filing order, it waited behind every other item, so the pause landed only when
they happened to settle inside that sleep (Task 6b review, finding 6). Paused
items are handled first, and `pause_mid_flight` waits on the item's own running
session, bounded, rather than on a margin.
"""

import importlib.util
import types
from pathlib import Path

import pytest

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


class _NeverRunning:
    """A server whose item never shows a running session: filed paused at
    capacity (no session, ever), or its agent came and went between polls."""

    def __init__(self, status, sessions):
        self.status, self.sessions, self.posts = status, sessions, []

    def get(self, path):
        return _Answer({"status": self.status, "worker_sessions": self.sessions})

    def post(self, path):
        self.posts.append(path)
        return _Answer({})


@pytest.mark.parametrize(
    ("status", "sessions", "got", "said"),
    [
        pytest.param("paused", [], "never started", "no agent started", id="never-ran"),
        pytest.param(
            "completed",
            [{"status": "completed"}],
            "completed",
            "exited before it could be paused",
            id="ran-between-polls",
        ),
    ],
)
def test_a_paused_item_whose_agent_was_never_seen_running_is_not_reported_as_paused(
    capsys, status, sessions, got, said
):
    client = _NeverRunning(status, sessions)
    assert _seed().pause_mid_flight(client, "w1", timeout=0.5) == got
    assert client.posts == []
    assert said in capsys.readouterr().out


class _Stuck:
    """A server whose item sits on one node and never reaches what was asked."""

    def get(self, path):
        return _Answer({"status": "active", "current_node_id": "implement", "worker_sessions": []})


def test_a_wait_that_never_settles_keeps_saying_so_and_says_when_it_gives_up(monkeypatch, capsys):
    seed = _seed()
    clock = [0.0]
    fake = types.SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s)
    )
    monkeypatch.setattr(seed, "time", fake)
    assert seed.settle(_Stuck(), "w1234567", "completed", timeout=25) == "active"
    out = capsys.readouterr().out
    assert "w1234567 active (node implement)" in out
    assert "w1234567 still active (node implement), want completed -- 10s of 25s" in out
    assert "w1234567 gave up after 25s: still active, wanted completed" in out
