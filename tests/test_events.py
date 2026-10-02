import pytest

from kraft import events


async def _seed_work_item(database, wid="w1"):
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
            "status, created_at, updated_at) VALUES (?, 't', '/r', 'quick-task', '{}', "
            "'active', 'now', 'now')",
            (wid,),
        )
    )


async def test_append_returns_increasing_seq_and_read_after_is_exclusive(database):
    await _seed_work_item(database)
    seqs = []
    for i in range(5):
        seq = await database.write(lambda c, i=i: events.append(c, "w1", "node_started", {"i": i}))
        seqs.append(seq)
    assert seqs == sorted(seqs) and len(set(seqs)) == 5

    allev = database.read(lambda c: events.read_after(c, 0))
    assert [e["seq"] for e in allev] == seqs
    assert allev[0]["payload"] == {"i": 0}
    assert allev[0]["type"] == "node_started"

    tail = database.read(lambda c: events.read_after(c, seqs[2]))
    assert [e["seq"] for e in tail] == seqs[3:]


async def test_read_after_filters_by_work_item(database):
    await _seed_work_item(database, "w1")
    await _seed_work_item(database, "w2")
    await database.write(lambda c: events.append(c, "w1", "x", {}))
    await database.write(lambda c: events.append(c, "w2", "y", {}))
    await database.write(lambda c: events.append(c, "w1", "z", {}))

    w1 = database.read(lambda c: events.read_after(c, 0, "w1"))
    assert [e["type"] for e in w1] == ["x", "z"]


async def test_row_and_event_commit_atomically(database):
    await _seed_work_item(database)

    def change_and_event(c):
        c.execute("UPDATE work_items SET current_node_id = 'implementation' WHERE id = 'w1'")
        events.append(c, "w1", "node_completed", {"node": "env_setup"})
        raise RuntimeError("crash before commit")

    with pytest.raises(RuntimeError):
        await database.write(change_and_event)

    node = database.read(
        lambda c: c.execute("SELECT current_node_id FROM work_items WHERE id = 'w1'").fetchone()[
            "current_node_id"
        ]
    )
    evcount = database.read(lambda c: c.execute("SELECT count(*) FROM events").fetchone()[0])
    assert node is None
    assert evcount == 0

    # a successful write lands both
    await database.write(
        lambda c: (
            c.execute("UPDATE work_items SET current_node_id = 'implementation' WHERE id = 'w1'"),
            events.append(c, "w1", "node_completed", {"node": "env_setup"}),
        )
    )
    node = database.read(
        lambda c: c.execute("SELECT current_node_id FROM work_items WHERE id = 'w1'").fetchone()[
            "current_node_id"
        ]
    )
    evcount = database.read(lambda c: c.execute("SELECT count(*) FROM events").fetchone()[0])
    assert node == "implementation"
    assert evcount == 1


async def test_append_node_id_defaults_from_payload_node_id(database):
    """`events.append` fills `node_id` from the payload's own `node_id` key
    when the caller does not pass one (Kraft UI v2 · B13)."""
    await _seed_work_item(database)
    seq = await database.write(
        lambda c: events.append(c, "w1", "node_started", {"node_id": "spec"})
    )
    row = database.read(
        lambda c: c.execute("SELECT node_id FROM events WHERE seq = ?", (seq,)).fetchone()
    )
    assert row["node_id"] == "spec"


async def test_append_node_id_defaults_from_payload_node(database):
    """The payload's `node` key is the other name an emitter uses for its node
    (`store.chain.complete_node`'s historical shape); either defaults `node_id`."""
    await _seed_work_item(database)
    seq = await database.write(lambda c: events.append(c, "w1", "x", {"node": "implementation"}))
    row = database.read(
        lambda c: c.execute("SELECT node_id FROM events WHERE seq = ?", (seq,)).fetchone()
    )
    assert row["node_id"] == "implementation"


async def test_append_node_id_ignores_a_non_string_node_payload_value(database):
    """A `node` key that is not a node id (a dict, say) must not leak into
    `node_id` as serialized junk."""
    await _seed_work_item(database)
    seq = await database.write(lambda c: events.append(c, "w1", "x", {"node": {"not": "a string"}}))
    row = database.read(
        lambda c: c.execute("SELECT node_id FROM events WHERE seq = ?", (seq,)).fetchone()
    )
    assert row["node_id"] is None


@pytest.mark.parametrize("key", ["node_id", "node"])
async def test_append_explicit_node_id_wins_over_the_payload(database, key):
    """An emitter that knows its node passes it explicitly, and that wins even
    over a payload key that names a different node."""
    await _seed_work_item(database)
    seq = await database.write(
        lambda c: events.append(
            c,
            "w1",
            "gate_approved",
            {"gate": "spec_approval", key: "implementation"},
            node_id="spec_approval",
        )
    )
    row = database.read(
        lambda c: c.execute("SELECT node_id FROM events WHERE seq = ?", (seq,)).fetchone()
    )
    assert row["node_id"] == "spec_approval"


async def test_read_after_limit_caps_the_window(database):
    await _seed_work_item(database)
    seqs = [
        await database.write(lambda c, i=i: events.append(c, "w1", "x", {"i": i})) for i in range(5)
    ]
    page = database.read(lambda c: events.read_after(c, 0, "w1", limit=2))
    assert [e["seq"] for e in page] == seqs[:2]


async def test_read_before_returns_the_last_limit_events_oldest_first(database):
    await _seed_work_item(database)
    seqs = [
        await database.write(lambda c, i=i: events.append(c, "w1", "x", {"i": i})) for i in range(5)
    ]
    page = database.read(lambda c: events.read_before(c, seqs[4], "w1", limit=2))
    assert [e["seq"] for e in page] == seqs[2:4]
