"""Add-on updates at their edges: a shutdown in the middle of one, and what a
candidate or a collection can do to the instance that reads it."""

import os
import threading

import pytest
from support.addons import AGENT, chain, instance, make_collection, publish

from kraft.addons import fetch, update
from kraft.addons.config import AddonEntry, AddonsLock, CollectionConfig

RELEASE = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}


@pytest.fixture
def two_collections(tmp_path):
    """`release@acme` and `tools@other`, each installed and auto-updating, and
    a newer release published to both: `(config dir, run/addons)`."""
    acme = make_collection(tmp_path, {"release": RELEASE})
    other = make_collection(tmp_path, {"tools": RELEASE}, name="other")
    config_dir, addons_dir = instance(
        tmp_path, {"acme": {"git": acme.as_uri()}, "other": {"git": other.as_uri()}}
    )
    entries = {i: AddonEntry(auto_update=True) for i in ("release@acme", "tools@other")}
    results = update.update(
        list(entries),
        accept=lambda review: True,
        entries=entries,
        config_dir=config_dir,
        addons_root=addons_dir,
    )
    assert [r.outcome for r in results] == ["applied", "applied"]
    publish(acme, "release", version="1.1.0", skills={"notes": "new"})
    publish(other, "tools", version="1.1.0", skills={"notes": "new"})
    return config_dir, addons_dir


def test_auto_update_stops_between_collections_once_cancelled(two_collections, monkeypatch):
    """A shutdown does not start the next collection's fetch, and what it did
    not do is not recorded as an outcome: a restart reports honestly."""
    config_dir, addons_dir = two_collections
    cancel = threading.Event()
    real, seen = update.update, []

    def one_at_a_time(ids, **kw):
        seen.append(list(ids))
        cancel.set()
        return real(ids, **kw)

    monkeypatch.setattr(update, "update", one_at_a_time)

    outcomes = update.auto_update(config_dir, addons_dir, cancel=cancel)

    assert seen == [["release@acme"]]
    assert outcomes["release@acme"]["outcome"] == "applied"
    assert outcomes["tools@other"] == {
        "outcome": "failed",
        "kind": "cancelled",
        "message": "stopped at shutdown",
    }
    assert list(update.read_status(addons_dir)) == ["release@acme"]


def test_installing_a_namespace_an_installed_addon_already_references_is_refused(tmp_path):
    """`a` was taken while no add-on `b` existed, its skill `b:method` naming
    nothing. Installing `b` now would make that reference an add-on dependency."""
    reaches = {**AGENT, "skill": "b:method"}
    a = {"library": {"tasks": {"base": reaches}}, "chains": {"go": chain("base")}}
    repo = make_collection(tmp_path, {"a": a, "b": RELEASE})
    config_dir, addons_dir = instance(tmp_path, {"acme": {"git": repo.as_uri()}})

    def install(addon_id):
        (result,) = update.update(
            [addon_id],
            accept=lambda review: True,
            entries={addon_id: True},
            config_dir=config_dir,
            addons_root=addons_dir,
        )
        return result

    assert install("a@acme").outcome == "applied"

    refused = install("b@acme")

    assert refused.outcome == "refused"
    (why,) = refused.problems
    assert "there are no add-on dependencies" in why and why.startswith("a@acme: ")
    assert list(AddonsLock.load(config_dir / "addons.lock").addons) == ["a@acme"]


@pytest.mark.parametrize(
    "make",
    [
        lambda p: os.mkfifo(p),
        lambda p: p.symlink_to(__file__),
        lambda p: p.write_bytes(b"{" + b" " * fetch.MAX_JSON + b"}"),
    ],
    ids=["fifo", "symlink", "oversized"],
)
def test_a_directory_collections_catalogue_is_read_safely(tmp_path, make):
    """A FIFO would block the read for ever, a symlink reads a file outside the
    collection, and a huge file is read whole into memory."""
    (tmp_path / ".kraft").mkdir()
    make(tmp_path / ".kraft" / "collection.json")

    with pytest.raises(fetch.FetchError) as refused:
        update.catalogue("acme", CollectionConfig(path=str(tmp_path)), tmp_path / "addons")

    assert refused.value.kind == "refused"
