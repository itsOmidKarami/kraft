"""`config_drafts` rows: the base each file joins with, one undo entry per
request, and a draft that goes away once it is back to the published files."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from support import schema

from kraft.drafts import store

CHAIN = "chains/x.yaml"
PUBLISHED = {CHAIN: "id: x\nnodes: []\n", "library.yaml": "tasks: {}\n"}


@pytest.fixture
def conn(tmp_path):
    return schema.fresh(tmp_path)


@pytest.fixture
def clock(monkeypatch):
    """`store._now`, moved on by `clock.tick(seconds)`."""

    class Clock:
        now = datetime(2026, 10, 1, tzinfo=UTC)

        def tick(self, seconds):
            self.now += timedelta(seconds=seconds)

    clock = Clock()
    monkeypatch.setattr(store, "_now", lambda: clock.now.isoformat())
    return clock


def put(conn, files, published=PUBLISHED, typed=CHAIN):
    return store.write(
        conn, "chains", "x", files, published, serialized=[], counts=(1, 0), typed=typed
    )


def test_a_file_keeps_the_base_it_joined_with(conn, clock):
    put(conn, {CHAIN: "id: x\nnodes: [a]\n"})
    # Published since: the chain keeps its first base, and the library joins
    # with the hash it has now.
    later = {CHAIN: "id: x\nnodes: [z]\n", "library.yaml": "tasks: {a: {}}\n"}
    put(conn, {CHAIN: "id: x\nnodes: [b]\n", "library.yaml": "tasks: {b: {}}\n"}, later)

    assert store.get(conn, "chains", "x")["base"] == {
        CHAIN: store.digest(PUBLISHED[CHAIN]),
        "library.yaml": store.digest(later["library.yaml"]),
    }


def test_typing_in_one_file_within_the_window_is_one_undo_step(conn, clock):
    put(conn, {CHAIN: "id: x\nnodes: [a]\n"})
    clock.tick(3)
    put(conn, {CHAIN: "id: x\nnodes: [ab]\n"})
    clock.tick(3)  # the window slides with each PUT
    put(conn, {CHAIN: "id: x\nnodes: [abc]\n"})
    assert len(store.get(conn, "chains", "x")["history"]) == 1

    clock.tick(5)
    put(conn, {CHAIN: "id: x\nnodes: [abcd]\n"})
    put(conn, {CHAIN: "id: x\nnodes: [op]\n"}, typed=None)  # an ops call never coalesces
    history = store.get(conn, "chains", "x")["history"]
    assert [e["files"] for e in history] == [
        {},
        {CHAIN: "id: x\nnodes: [abc]\n"},
        {CHAIN: "id: x\nnodes: [abcd]\n"},
    ]


def test_history_keeps_the_newest_80(conn, clock):
    for n in range(store.HISTORY_CAP + 5):
        put(conn, {CHAIN: f"id: x\nnodes: [{n}]\n"}, typed=None)
    history = store.get(conn, "chains", "x")["history"]
    assert len(history) == store.HISTORY_CAP
    assert history[-1]["files"] == {CHAIN: f"id: x\nnodes: [{store.HISTORY_CAP + 3}]\n"}


def test_undo_pops_one_request_and_back_to_published_drops_the_draft(conn, clock):
    put(conn, {CHAIN: "id: x\nnodes: [a]\n"})
    clock.tick(10)
    put(conn, {CHAIN: "id: x\nnodes: [b]\n"})

    assert store.undo(conn, "chains", "x", PUBLISHED, (1, 0)) is True
    assert store.get(conn, "chains", "x")["files"] == {CHAIN: "id: x\nnodes: [a]\n"}
    assert store.undo(conn, "chains", "x", PUBLISHED, (0, 0)) is False
    assert store.get(conn, "chains", "x") is None
    assert store.undo(conn, "chains", "x", PUBLISHED, (0, 0)) is None


def test_a_write_that_parses_to_the_published_file_drops_the_draft(conn, clock):
    put(conn, {CHAIN: "id: x\nnodes: [a]\n"})
    # Comments and layout differ; the parsed mapping does not.
    assert put(conn, {CHAIN: "# mine\nid: x\nnodes:   []\n"}) is False
    assert store.get(conn, "chains", "x") is None
    # Two texts that do not parse are not equal to each other.
    assert put(conn, {CHAIN: "nodes: [\n"}, {CHAIN: "nodes: {\n"}) is True


@pytest.mark.parametrize(
    ("area", "files"),
    [
        ("harnesses", ("harnesses.yaml", "policy.yaml")),
        ("repos", ("repos.yaml",)),
        ("policy", ("policy.yaml",)),
        ("intake", ("intake.yaml",)),
    ],
)
def test_a_config_area_registers_its_files_under_its_own_name_as_key(area, files):
    from kraft.drafts import areas

    areas.register()
    found = store.AREAS[area]
    assert found.files(area) == files
    assert found.valid(area)
    assert not found.valid("x")
    assert found.working is not None
