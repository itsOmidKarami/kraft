"""Seeding the state database an `Indexer` reads: a work item and a worker
session carrying a summary ref (`tests/index/`)."""

from __future__ import annotations


def seed_work_item(db, repo: str, wid: str = "w1"):
    return db.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at) VALUES "
            "(?, 'b1', 't', ?, 'quick-task', '{}', 'active', 'now', 'now')",
            (wid, repo),
        )
    )


def seed_session(
    db, sid, wid, ref, node="implementation", hook="on.implementation.start", thread=None
):
    from kraft import store

    return db.write(
        lambda c: (
            store.create_session(
                c,
                id=sid,
                work_item_id=wid,
                node_id=node,
                hook_point=hook,
                log_path="/dev/null",
                result_path="/dev/null",
            ),
            c.execute(
                "UPDATE worker_sessions SET session_summary_ref=?, thread=COALESCE(?, thread) "
                "WHERE work_item_id=? AND id=?",
                (ref, thread, wid, sid),
            ),
        )
    )
