"""The cause a stop for a human carries on its card (Task 6b review, finding 3).

`walk._stop_for_config_error` reads the task's own session log. Its edges must
never leave a stop *less* legible than the generic "see the session log" text:
a missing or undecodable log falls back to it, and only the newest session in
the status being explained is read.
"""

import asyncio
from types import SimpleNamespace

from support.store_fixtures import mk_item, open_db

from kraft import events
from kraft.executor import walk

NODE = SimpleNamespace(id="verify")
TASK = SimpleNamespace(path="verify.main.check", task=SimpleNamespace(id="check"))


def _session(c, sid, log_path, status, created_at):
    c.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, log_path, "
        "result_path, status, created_at) VALUES (?, 'w1', 'verify', ?, NULL, ?, 'x', ?, ?)",
        (sid, TASK.path, str(log_path), status, created_at),
    )


def _stop(tmp_path, sessions) -> str:
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            for s in sessions:
                await database.write(lambda c, s=s: _session(c, *s))
            await walk._stop_for_config_error(database, "w1", NODE, [TASK])
            evts = database.read(lambda c: events.read_after(c, 0, "w1"))
            return [e for e in evts if e["type"] == "work_item_needs_human"][-1]["payload"][
                "reason"
            ]
        finally:
            await database.close()

    return asyncio.run(scenario())


def test_a_missing_log_falls_back_to_the_generic_pointer(tmp_path):
    reason = _stop(tmp_path, [("s1", tmp_path / "gone.log", "config_error", "2026-01-01")])
    assert reason == "could not start check in node verify: see the session log"


def test_an_undecodable_log_falls_back_rather_than_escaping_the_walk(tmp_path):
    log = tmp_path / "s1.log"
    log.write_bytes(b"\xff\xfe\xfa not utf-8\n")
    reason = _stop(tmp_path, [("s1", log, "config_error", "2026-01-01")])
    assert reason == "could not start check in node verify: see the session log"


def test_the_newest_session_is_the_one_explained(tmp_path):
    """A retry that hits a config_error again for a new reason must not put the
    stale first cause on the card."""
    old, new = tmp_path / "old.log", tmp_path / "new.log"
    old.write_text("stale cause\n")
    new.write_text("current cause\n")
    reason = _stop(
        tmp_path,
        [("s1", old, "config_error", "2026-01-01"), ("s2", new, "config_error", "2026-01-02")],
    )
    assert reason.endswith(": current cause")


def test_only_a_session_in_the_explained_status_is_read(tmp_path):
    """A newer session of the same task in another status (a retry that got
    further, say) is not this stop's cause."""
    cause, other = tmp_path / "cause.log", tmp_path / "other.log"
    cause.write_text("the real cause\n")
    other.write_text("some agent output\n")
    reason = _stop(
        tmp_path,
        [("s1", cause, "config_error", "2026-01-01"), ("s2", other, "done", "2026-01-02")],
    )
    assert reason.endswith(": the real cause")
