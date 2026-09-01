import asyncio

import pytest

from kraft import db, events


async def _seed_work_item(database, wid="w1"):
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
            "status, created_at, updated_at) VALUES (?, 't', '/r', 'quick-task', '{}', "
            "'active', 'now', 'now')",
            (wid,),
        )
    )


def test_append_returns_increasing_seq_and_read_after_is_exclusive(tmp_path):
    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:
            await _seed_work_item(database)
            seqs = []
            for i in range(5):
                seq = await database.write(
                    lambda c, i=i: events.append(c, "w1", "node_started", {"i": i})
                )
                seqs.append(seq)
            assert seqs == sorted(seqs) and len(set(seqs)) == 5

            allev = database.read(lambda c: events.read_after(c, 0))
            assert [e["seq"] for e in allev] == seqs
            assert allev[0]["payload"] == {"i": 0}
            assert allev[0]["type"] == "node_started"

            tail = database.read(lambda c: events.read_after(c, seqs[2]))
            assert [e["seq"] for e in tail] == seqs[3:]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_read_after_filters_by_work_item(tmp_path):
    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:
            await _seed_work_item(database, "w1")
            await _seed_work_item(database, "w2")
            await database.write(lambda c: events.append(c, "w1", "x", {}))
            await database.write(lambda c: events.append(c, "w2", "y", {}))
            await database.write(lambda c: events.append(c, "w1", "z", {}))

            w1 = database.read(lambda c: events.read_after(c, 0, "w1"))
            assert [e["type"] for e in w1] == ["x", "z"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_row_and_event_commit_atomically(tmp_path):
    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:
            await _seed_work_item(database)

            def change_and_event(c):
                c.execute(
                    "UPDATE work_items SET current_node_id = 'implementation' WHERE id = 'w1'"
                )
                events.append(c, "w1", "node_completed", {"node": "env_setup"})
                raise RuntimeError("crash before commit")

            with pytest.raises(RuntimeError):
                await database.write(change_and_event)

            node = database.read(
                lambda c: c.execute(
                    "SELECT current_node_id FROM work_items WHERE id = 'w1'"
                ).fetchone()["current_node_id"]
            )
            evcount = database.read(
                lambda c: c.execute("SELECT count(*) FROM events").fetchone()[0]
            )
            assert node is None
            assert evcount == 0

            # a successful write lands both
            await database.write(
                lambda c: (
                    c.execute(
                        "UPDATE work_items SET current_node_id = 'implementation' WHERE id = 'w1'"
                    ),
                    events.append(c, "w1", "node_completed", {"node": "env_setup"}),
                )
            )
            node = database.read(
                lambda c: c.execute(
                    "SELECT current_node_id FROM work_items WHERE id = 'w1'"
                ).fetchone()["current_node_id"]
            )
            evcount = database.read(
                lambda c: c.execute("SELECT count(*) FROM events").fetchone()[0]
            )
            assert node == "implementation"
            assert evcount == 1
        finally:
            await database.close()

    asyncio.run(scenario())
