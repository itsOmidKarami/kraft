"""Shared scaffolding for the `test_store_*` files split out of `test_store.py`."""

import json

from kraft import db, store

CHAIN = json.dumps(
    {
        "template_id": "quick-task",
        "nodes": [
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
            {"id": "verify", "tasks": ["on.test.run"], "gate_after": None},
        ],
    }
)


async def open_db(tmp_path):
    return await db.Database.open(tmp_path / "orchestrator.db")


def mk_item(database, wid="w1"):
    return database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id="B-1",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition=CHAIN,
        )
    )
