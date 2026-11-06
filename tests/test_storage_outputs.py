"""Step-output snapshots in the storage figure: governed, freed by archiving,
off the cache at once, and kept for an item still running."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kraft import storage, store

MB = 1024**2
AT = "2026-01-01T00:00:00+00:00"


def _fill(path, size=MB):
    """A file of `size` random bytes, its folders made."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x01" * size)


def _usage(governed, items=None, categories=None, documents=None, outputs=None):
    return storage.Usage(
        AT, governed, dict(items or {}), dict(categories or {}), documents or {}, outputs or {}
    )


def test_outputs_are_governed_freed_by_archiving_and_not_an_items_worktree(tmp_path):
    item = tmp_path / "items" / "w1"
    _fill(item / "outputs" / "build.main.t" / "junit.xml", 2 * MB)
    _fill(item / "docs" / "spec.md")
    _fill(item / "sandbox-home" / "cache")

    used = storage.measure(tmp_path)

    assert 2 * MB <= used.categories["outputs"] < 3 * MB
    assert used.outputs == {"w1": used.categories["outputs"]}
    assert "outputs" in storage.GOVERNED and "outputs" in storage.CATEGORIES
    assert used.items == {"w1": used.categories["sandboxes"]}, "items stay worktrees and homes"
    assert used.governed == sum(used.categories[c] for c in storage.GOVERNED)
    # Archiving keeps the documents and frees the rest.
    assert storage.archivable(used) == used.governed - used.categories["documents"]
    assert storage.archivable(used) >= used.categories["outputs"]


@pytest.mark.parametrize(
    "items", [{"w1": 40, "b": 80}, {"b": 80}], ids=["with-a-worktree", "in-place-has-none"]
)
def test_forget_drains_outputs_even_for_an_item_with_no_worktree(items):
    st = SimpleNamespace(
        storage_usage=_usage(
            160,
            items,
            {"worktrees": 100, "sandboxes": 20, "outputs": 30},
            outputs={"w1": 30},
        )
    )

    storage.forget(st, "w1")

    got = st.storage_usage
    assert got.outputs == {}
    assert got.categories["outputs"] == 0
    assert got.governed == 160 - 30 - items.get("w1", 0)


def test_forget_outputs_takes_an_abandoned_items_off_the_cache():
    st = SimpleNamespace(
        storage_usage=_usage(90, {}, {"outputs": 50}, outputs={"w1": 30, "w2": 20})
    )

    storage.forget_outputs(st, "w1")
    storage.forget_outputs(st, "never-measured")

    got = st.storage_usage
    assert (got.governed, got.outputs, got.categories["outputs"]) == (60, {"w2": 20}, 20)


@pytest.mark.parametrize(
    ("row", "kept"),
    [
        ({"archived_at": None}, True),
        ({"archived_at": "2026-01-02T00:00:00+00:00"}, False),
        (None, False),
    ],
    ids=["unarchived-in-place-item", "archived", "no-row"],
)
async def test_refresh_keeps_the_outputs_of_an_item_that_is_not_archived(
    tmp_path, database, monkeypatch, row, kept
):
    """The database decides, not a probe of the folders: an in-place item has
    no worktree to find, and its outputs stay until it is archived."""
    if row is not None:
        await database.write(
            lambda c: store.create_work_item(
                c,
                id="w1",
                bead_id="B",
                title="t",
                repo="/r",
                chain_template="quick-task",
                chain_definition="{}",
            )
        )
        await database.write(
            lambda c: c.execute(
                "UPDATE work_items SET archived_at = ? WHERE id = 'w1'", (row["archived_at"],)
            )
        )
    # Measured with a sandbox home no longer on disk: the probe forgets `items`, not outputs.
    monkeypatch.setattr(
        storage,
        "measure",
        lambda base, stop=None: _usage(
            12, {"w1": 2}, {"sandboxes": 2, "outputs": 10}, outputs={"w1": 10}
        ),
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            run_dirs=SimpleNamespace(base=tmp_path, worktrees=tmp_path), db=database
        )
    )

    used = await storage.refresh(app)

    assert (used.outputs, used.governed) == (({"w1": 10}, 10) if kept else ({}, 0))
    assert used.items == {}


def test_report_counts_outputs_with_what_archiving_frees():
    used = _usage(
        175,
        {"done": 30, "live": 50},
        {"worktrees": 80, "outputs": 95},
        outputs={"done": 25, "live": 10, "kept": 40, "ghost": 20},
    )
    rows = {
        "done": {
            "title": "d",
            "status": "completed",
            "archived_at": None,
            "updated_at": AT,
            "in_place": 0,
        },
        "live": {
            "title": "l",
            "status": "active",
            "archived_at": None,
            "updated_at": AT,
            "in_place": 0,
        },
        "kept": {
            "title": "k",
            "status": "completed",
            "archived_at": AT,
            "updated_at": AT,
            "in_place": 0,
        },
    }

    got = storage.report(None, used, rows)

    assert [(i["id"], i["bytes"], i["outputs_bytes"], i["reclaimable"]) for i in got["items"]] == [
        ("live", 50, 10, False),
        ("done", 30, 25, True),
        ("kept", 0, 40, False),
    ]
    assert got["reclaimable_bytes"] == 30 + 25
    assert got["orphans"] == [{"name": "ghost", "bytes": 20}]
