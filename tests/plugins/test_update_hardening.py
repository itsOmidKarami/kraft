"""Plugin updates at their edges: a shutdown in the middle of one, and what a
candidate or a collection can do to the instance that reads it."""

import threading

import pytest
from support.plugins import AGENT, chain, instance, make_collection, publish

from kraft.plugins import update
from kraft.plugins.config import PluginEntry

RELEASE = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}


@pytest.fixture
def two_collections(tmp_path):
    """`release@acme` and `tools@other`, each installed and auto-updating, and
    a newer release published to both: `(config dir, run/plugins)`."""
    acme = make_collection(tmp_path, {"release": RELEASE})
    other = make_collection(tmp_path, {"tools": RELEASE}, name="other")
    config_dir, plugins_dir = instance(
        tmp_path, {"acme": {"git": acme.as_uri()}, "other": {"git": other.as_uri()}}
    )
    entries = {i: PluginEntry(auto_update=True) for i in ("release@acme", "tools@other")}
    results = update.update(
        list(entries),
        accept=lambda review: True,
        entries=entries,
        config_dir=config_dir,
        plugins_root=plugins_dir,
    )
    assert [r.outcome for r in results] == ["applied", "applied"]
    publish(acme, "release", version="1.1.0", skills={"notes": "new"})
    publish(other, "tools", version="1.1.0", skills={"notes": "new"})
    return config_dir, plugins_dir


def test_auto_update_stops_between_collections_once_cancelled(two_collections, monkeypatch):
    """A shutdown does not start the next collection's fetch, and what it did
    not do is not recorded as an outcome: a restart reports honestly."""
    config_dir, plugins_dir = two_collections
    cancel = threading.Event()
    real, seen = update.update, []

    def one_at_a_time(ids, **kw):
        seen.append(list(ids))
        cancel.set()
        return real(ids, **kw)

    monkeypatch.setattr(update, "update", one_at_a_time)

    outcomes = update.auto_update(config_dir, plugins_dir, cancel=cancel)

    assert seen == [["release@acme"]]
    assert outcomes["release@acme"]["outcome"] == "applied"
    assert outcomes["tools@other"] == {
        "outcome": "failed",
        "kind": "cancelled",
        "message": "stopped at shutdown",
    }
    assert list(update.read_status(plugins_dir)) == ["release@acme"]
