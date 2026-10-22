"""`store.reusable_session` across a new pass: a session from before a retry
or a span restart never stands in for the new pass's. Beside
`test_sessions.py`, which is at its line budget."""

import pytest
from support.store_fixtures import mk_item

from kraft import events

from .test_sessions import _exit, _reusable, _session


@pytest.mark.parametrize(
    "restart", ["work_item_retried", "base_change_restart", "merge_rejection_restart"]
)
async def test_reusable_session_excludes_a_session_from_before_a_restart(database, restart):
    """A retry or a span restart starts a new pass at the rounds the old one
    used, so a session from before it never stands in for the new pass's."""
    await mk_item(database)
    await _session(database, "s1", head_sha="sha-a")
    await _exit(database, "s1", "done")
    assert _reusable(database) is not None
    await database.write(lambda c: events.append(c, "w1", restart, {"nodes": ["verify"]}))
    assert _reusable(database) is None
