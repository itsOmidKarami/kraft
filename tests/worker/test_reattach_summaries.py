"""The summary a session adopted or resolved across a restart records: the
one Kraft asked it for, as `run_task` records it on its own exit
(`kraft-records-the-summary-it-asked-for`). In its own file because
`test_reattach.py` is at its line budget (`dev/check_tests.py`)."""

from __future__ import annotations

import json

import pytest

from kraft import escalate, item_paths, store
from kraft.worker import reattach

from .test_reattach import _CHAIN, DEAD, IMPLEMENT


@pytest.mark.parametrize(
    ("hook", "summary", "reported", "recorded"),
    [
        (IMPLEMENT, "# done", None, "items/w1/sessions/s1/out/summary.md"),
        (IMPLEMENT, "", "items/w2/sessions/s9/out/summary.md", None),
        ("escalation", "# done", None, "items/w1/sessions/escalation-1/out/summary.md"),
    ],
    ids=["written", "worker-ref-under-items-refused", "escalation-thread-s"],
)
async def test_a_session_resolved_after_a_restart_records_the_summary_kraft_asked_for(
    item_on, database, run_dirs, hook, summary, reported, recorded
):
    await item_on(_CHAIN, "implementation")
    own = item_paths.session_files(run_dirs, "w1", "s1")
    if hook == "escalation":
        files = escalate.thread_files(database, run_dirs, "w1", 1, "s1")
    else:
        files = own
    files.writable.mkdir(parents=True)
    own.folder.mkdir(parents=True, exist_ok=True)
    own.log.write_text("")
    result = {"status": "done", **({"session_summary_ref": reported} if reported else {})}
    files.result.write_text(json.dumps(result))
    files.summary.write_text(summary)
    await database.write(
        lambda c: store.create_session(
            c,
            id="s1",
            work_item_id="w1",
            node_id="implementation",
            hook_point=hook,
            log_path=str(own.log),
            result_path=str(files.result),
        )
    )
    await database.write(lambda c: store.session_running(c, "s1", *DEAD, work_item_id="w1"))

    summary_, resumed = await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)
    for task in resumed.values():  # an escalation's resume, not under test here
        task.cancel()

    assert summary_.resolved_from_file == ["s1"]
    row = database.read(
        lambda c: c.execute("SELECT * FROM worker_sessions WHERE id = 's1'").fetchone()
    )
    assert (row["status"], row["session_summary_ref"]) == ("done", recorded)
