import asyncio
import json
import subprocess

from support.store_fixtures import CHAIN, mk_item, open_db

from kraft import db, events, store


def test_create_work_item_writes_row_and_event(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["status"] == "active"
            assert row["current_node_id"] is None
            assert row["bead_id"] == "B-1"
            evs = database.read(lambda c: events.read_after(c, 0))
            assert [e["type"] for e in evs] == ["work_item_created"]
            assert evs[0]["payload"] == {"title": "t", "repo": "/r", "chain_template": "quick-task"}
        finally:
            await database.close()

    asyncio.run(scenario())


def test_create_work_item_round_trips_a_description(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B-1",
                    title="short label",
                    description="the long brief the spec is written from",
                    repo="/r",
                    chain_template="quick-task",
                    chain_definition=CHAIN,
                )
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["description"] == "the long brief the spec is written from"

            # The event payload stays a scannable label: the brief does not go in it.
            evts = database.read(lambda c: events.read_after(c, 0, "w1"))
            created = [e for e in evts if e["type"] == "work_item_created"]
            assert len(created) == 1
            assert "description" not in created[0]["payload"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_create_work_item_without_a_description_stores_null(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["description"] is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_auto_gate_defaults_off_and_round_trips(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id=None,
                    title="t",
                    repo="/r",
                    chain_template="default",
                    chain_definition="{}",
                )
            )
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w2",
                    bead_id=None,
                    title="t",
                    repo="/r",
                    chain_template="default",
                    chain_definition="{}",
                    auto_gate=True,
                )
            )
            rows = {
                r["id"]: r["auto_gate"]
                for r in database.read(
                    lambda c: c.execute("SELECT id, auto_gate FROM work_items").fetchall()
                )
            }
            assert rows == {"w1": 0, "w2": 1}
        finally:
            await database.close()

    asyncio.run(scenario())


def test_mark_needs_human_and_completed(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database, "wh")
            await database.write(lambda c: store.mark_needs_human(c, "wh", "verify", "boom"))
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id='wh'").fetchone()
            )
            assert row["status"] == "needs_human"
            ev = database.read(lambda c: events.read_after(c, 0))[-1]
            assert ev["type"] == "work_item_needs_human"
            assert ev["payload"] == {"node_id": "verify", "reason": "boom"}

            await mk_item(database, "wc")
            await database.write(lambda c: store.mark_completed(c, "wc"))
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id='wc'").fetchone()
            )
            assert row["status"] == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_needs_human_names_the_stop_it_is_about_not_an_older_failure(tmp_path):
    """Kraft-eh6p's "view log" button hangs off `session_id`. A node that failed
    once, was retried, and then stopped for a *question* must not hand the human
    the older failure's log: it looks like the answer and is not. And a stop with
    no session to explain it (a budget breach) must offer no button at all."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database, "wh")
            for sid, status in (("older", "failed"), ("asked", "needs_context")):
                await database.write(
                    lambda c, sid=sid: store.create_session(
                        c,
                        id=sid,
                        work_item_id="wh",
                        node_id="verify",
                        hook_point="on.test.run",
                        log_path="/l",
                        result_path="/r",
                    )
                )
                await database.write(
                    lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                )

            await database.write(
                lambda c: store.mark_needs_human(c, "wh", "verify", "needs_context: which db?")
            )
            asked = database.read(lambda c: events.read_after(c, 0, "wh"))[-1]

            # A later session that explains nothing (a budget-refused launch) —
            # the button goes away rather than pointing back at "asked".
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="refused",
                    work_item_id="wh",
                    node_id="verify",
                    hook_point="on.implementation.start",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.mark_needs_human(c, "wh", "verify", "over budget"))
            broke = database.read(lambda c: events.read_after(c, 0, "wh"))[-1]
            return asked["payload"], broke["payload"]
        finally:
            await database.close()

    asked, broke = asyncio.run(scenario())

    assert asked["session_id"] == "asked", "the stop names an older, unrelated failure"
    assert "session_id" not in broke, "a stop no session explains still offered a log"


def test_set_base_ref(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/r",
        chain_template="quick-task",
        chain_definition="{}",
    )
    assert conn.execute("SELECT base_ref FROM work_items WHERE id='w1'").fetchone()[0] is None
    store.set_base_ref(conn, "w1", "abc123")
    assert conn.execute("SELECT base_ref FROM work_items WHERE id='w1'").fetchone()[0] == "abc123"


def test_set_ci_pipeline_ref_writes_the_column(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/r",
        chain_template="quick-task",
        chain_definition="{}",
    )
    assert (
        conn.execute("SELECT ci_pipeline_ref FROM work_items WHERE id='w1'").fetchone()[0] is None
    )
    store.set_ci_pipeline_ref(conn, "w1", "abc123:456")
    assert (
        conn.execute("SELECT ci_pipeline_ref FROM work_items WHERE id='w1'").fetchone()[0]
        == "abc123:456"
    )


def test_set_escalation_session(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/r",
        chain_template="quick-task",
        chain_definition="{}",
    )
    row = conn.execute("SELECT escalation_session_id FROM work_items WHERE id='w1'").fetchone()
    assert row[0] is None
    store.set_escalation_session(conn, "w1", "cli-session-abc")
    row = conn.execute("SELECT escalation_session_id FROM work_items WHERE id='w1'").fetchone()
    assert row[0] == "cli-session-abc"


def test_branch_name_slugs_the_title_and_stays_a_legal_ref(tmp_path):
    """Every branch this produces has to survive `git check-ref-format`, over
    the shapes a Kraft title actually takes: punctuation, non-ASCII, git's
    reserved characters, and the 360-character paragraph `mr_title` notes."""
    wid = "b63d95be41884b6e83a423c114a97ce3"
    readable_merge_records = (
        "Readable merge records: branch slugs & GFM tables!",
        "kraft/readable-merge-records-branch-slugs-gfm-tables-b63d95be",
    )
    # git refuses ~ ^ : and spaces in a ref; .. is reserved
    caret_and_colon = (
        "Fix ~caret^ and :colon and .. spaces",
        "kraft/fix-caret-and-colon-and-spaces-b63d95be",
    )
    # clipped at 48 characters, with no trailing separator left behind
    clipped_title = (
        "Kraft-8mu.5.2 — session lifecycle: pause, abandon and reattach must run",
        "kraft/kraft-8mu-5-2-session-lifecycle-pause-abandon-an-b63d95be",
    )
    cases = dict(
        (
            readable_merge_records,
            caret_and_colon,
            clipped_title,
            ("A" * 360, "kraft/" + "a" * 48 + "-b63d95be"),
            # only the first line of a multi-line title
            ("Branch slugs\n\nand a second paragraph", "kraft/branch-slugs-b63d95be"),
            # nothing to slug: the pre-Kraft-nhps name, which is always valid
            ("我的任务", f"kraft/{wid}"),
            ("...", f"kraft/{wid}"),
            ("   ", f"kraft/{wid}"),
            ("", f"kraft/{wid}"),
        )
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for title, expected in cases.items():
        got = store.branch_name(title, wid)
        assert got == expected, f"{title!r} -> {got!r}"
        assert (
            subprocess.run(
                ["git", "check-ref-format", "--branch", got],
                cwd=repo,
                capture_output=True,
            ).returncode
            == 0
        ), f"{got!r} is not a legal branch name"


def test_create_work_item_stores_the_branch(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B-1",
                    title="Readable merge records",
                    repo="/r",
                    chain_template="quick-task",
                    chain_definition=CHAIN,
                )
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["branch"] == "kraft/readable-merge-records-w1"
            assert store.branch_for(row) == "kraft/readable-merge-records-w1"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_branch_for_falls_back_when_the_row_predates_the_column(tmp_path):
    """The no-stranding guarantee: an in-flight item whose row was written
    before the migration keeps the `kraft/<id>` branch its worktree is on."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: c.execute("UPDATE work_items SET branch = NULL WHERE id='w1'")
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert store.branch_for(row) == "kraft/w1"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_mark_rate_limited_sets_status_and_retry_at(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.mark_rate_limited(c, "w1", "implementation", "2026-09-10T00:00:00Z")
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, retry_at FROM work_items WHERE id='w1'"
                ).fetchone()
            )
            assert row["status"] == "rate_limited"
            assert row["retry_at"] == "2026-09-10T00:00:00Z"
            ev = database.read(lambda c: events.read_after(c, 0))[-1]
            assert ev["type"] == "work_item_rate_limited"
            assert ev["payload"] == {
                "node_id": "implementation",
                "retry_at": "2026-09-10T00:00:00Z",
            }
        finally:
            await database.close()

    asyncio.run(scenario())


def test_mark_waiting_sets_the_status_retry_at_and_event(tmp_path):
    """The direct mirror of mark_rate_limited: the poller reads status +
    retry_at, and the event is what the timeline shows a human."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.mark_waiting(c, "w1", "mr_checks", "2099-01-01T00:00:00+00:00")
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, retry_at FROM work_items WHERE id='w1'"
                ).fetchone()
            )
            assert row["status"] == "waiting"
            assert row["retry_at"] == "2099-01-01T00:00:00+00:00"
            ev = database.read(lambda c: events.read_after(c, 0))[-1]
            assert ev["type"] == "work_item_waiting"
            assert ev["payload"] == {
                "node_id": "mr_checks",
                "retry_at": "2099-01-01T00:00:00+00:00",
            }
        finally:
            await database.close()

    asyncio.run(scenario())


def test_mark_needs_human_clears_retry_at(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.mark_rate_limited(c, "w1", "implementation", "2026-09-10T00:00:00Z")
            )
            await database.write(
                lambda c: store.mark_needs_human(c, "w1", "implementation", "boom")
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, retry_at FROM work_items WHERE id='w1'"
                ).fetchone()
            )
            assert row["status"] == "needs_human"
            assert row["retry_at"] is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_set_title_records_an_event(tmp_path):
    """A title edit gets its own event type. Not a shared `work_item_edited`
    with `set_description`: the description is prepended to every agent
    instruction and the title is a label, so a timeline that cannot tell them
    apart answers neither question."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(lambda c: store.set_title(c, "w1", "a better label"))
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["title"] == "a better label"

            evs = database.read(lambda c: events.read_after(c, 0, "w1"))
            edits = [e for e in evs if e["type"] == "work_item_title_edited"]
            assert [e["payload"]["title"] for e in edits] == ["a better label"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_create_work_item_stores_attachments_and_events_them(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/repo",
        chain_template="default",
        chain_definition="{}",
        attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
    )
    row = conn.execute("SELECT attachments FROM work_items WHERE id='w1'").fetchone()
    assert json.loads(row["attachments"]) == [{"kind": "plan", "path": ".engineering/plans/p.md"}]
    types = [e["type"] for e in events.read_after(conn, 0, "w1")]
    assert "work_item_attachments" in types


def test_create_work_item_without_attachments_stores_null(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/repo",
        chain_template="default",
        chain_definition="{}",
    )
    row = conn.execute("SELECT attachments FROM work_items WHERE id='w1'").fetchone()
    assert row["attachments"] is None
    types = [e["type"] for e in events.read_after(conn, 0, "w1")]
    assert "work_item_attachments" not in types


def test_merge_rank_order_puts_the_deepest_path_first():
    assert store.merge_rank_order(["libs/a", "vendor/deep/b", "x"]) == [
        "vendor/deep/b",
        "libs/a",
        "x",
    ]


def test_add_repo_and_repos_for_round_trip(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/r",
        chain_template="default",
        chain_definition="{}",
    )
    sub_id = store.add_repo(
        conn,
        work_item_id="w1",
        repo_path="/wt/repos/pkg",
        role="submodule",
        submodule_path="repos/pkg",
        merge_rank=1,
    )
    store.add_repo(conn, work_item_id="w1", repo_path="/wt", role="root", merge_rank=2)

    repos = store.repos_for(conn, "w1")

    assert [r["role"] for r in repos] == ["submodule", "root"]
    assert repos[0]["path"] == "/wt/repos/pkg"
    assert repos[0]["state"] == "pending"
    assert repos[0]["mr_ref"] is None

    store.update_repo_state(
        conn, sub_id, merge_state="merged", mr_ref={"number": 3, "url": "http://x/3"}
    )
    repos = store.repos_for(conn, "w1")
    assert repos[0]["state"] == "merged"
    assert repos[0]["mr_ref"] == {"number": 3, "url": "http://x/3"}


def test_repos_for_is_empty_for_a_single_repo_item(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/r",
        chain_template="default",
        chain_definition="{}",
    )
    assert store.repos_for(conn, "w1") == []


def _mk_item(conn, wid="w1", status="active"):
    store.create_work_item(
        conn,
        id=wid,
        bead_id="B-1",
        title="t",
        repo="/r",
        chain_template="quick-task",
        chain_definition=CHAIN,
    )
    conn.execute("UPDATE work_items SET status = ? WHERE id = ?", (status, wid))


def test_archive_sets_archived_at_and_by_without_touching_status(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    _mk_item(conn, status="completed")

    store.archive_work_item(conn, "w1", "you")

    row = conn.execute(
        "SELECT status, archived_at, archived_by FROM work_items WHERE id='w1'"
    ).fetchone()
    assert row["status"] == "completed"
    assert row["archived_by"] == "you"
    assert row["archived_at"]


def test_restore_clears_archived_columns(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    _mk_item(conn, status="abandoned")
    store.archive_work_item(conn, "w1", "auto")

    store.restore_work_item(conn, "w1")

    row = conn.execute("SELECT archived_at, archived_by FROM work_items WHERE id='w1'").fetchone()
    assert row["archived_at"] is None
    assert row["archived_by"] is None


def test_archive_appends_work_item_archived_event(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    _mk_item(conn, status="completed")

    store.archive_work_item(conn, "w1", "you")

    evs = [e for e in events.read_after(conn, 0, "w1") if e["type"] == "work_item_archived"]
    assert evs and evs[0]["payload"] == {"by": "you"}


def test_recent_auto_pickups_excludes_manually_created_items(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id=None,
        title="manual",
        repo="/a",
        chain_template="default",
        chain_definition="{}",
    )
    store.create_work_item(
        conn,
        id="w2",
        bead_id="B-1",
        title="auto",
        repo="/a",
        chain_template="default",
        chain_definition="{}",
        source="auto_intake",
        bead_priority=2,
    )
    conn.commit()
    pickups = store.recent_auto_pickups(conn)
    assert [p["work_item_id"] for p in pickups] == ["w2"]
    assert pickups[0]["priority"] == 2


def test_last_auto_pickup_at_only_tracks_auto_intake_repos(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B-1",
        title="auto",
        repo="/a",
        chain_template="default",
        chain_definition="{}",
        source="auto_intake",
    )
    store.create_work_item(
        conn,
        id="w2",
        bead_id=None,
        title="manual",
        repo="/b",
        chain_template="default",
        chain_definition="{}",
    )
    conn.commit()
    last = store.last_auto_pickup_at(conn)
    assert "/a" in last
    assert "/b" not in last


def test_claim_for_run_is_atomic_between_two_callers(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(lambda c: store.pause_work_item(c, "w1", []))
            first = await database.write(
                lambda c: store.claim_for_run(c, "w1", from_statuses=["paused"])
            )
            second = await database.write(
                lambda c: store.claim_for_run(c, "w1", from_statuses=["paused"])
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, retry_at FROM work_items WHERE id='w1'"
                ).fetchone()
            )
            return first, second, row["status"], row["retry_at"]
        finally:
            await database.close()

    first, second, status, retry_at = asyncio.run(scenario())
    assert (first, second) == (True, False)
    assert status == "active"
    assert retry_at is None


def test_claim_for_run_refuses_a_status_outside_the_set(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)  # created 'active'
            claimed = await database.write(
                lambda c: store.claim_for_run(c, "w1", from_statuses=["paused"])
            )
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id='w1'").fetchone()
            )
            return claimed, row["status"]
        finally:
            await database.close()

    claimed, status = asyncio.run(scenario())
    assert claimed is False


def test_claim_for_run_limit_lets_only_one_of_two_racing_items_through(tmp_path):
    """Kraft-m43g, Kraft-nxht: two different items resumed/retried within
    milliseconds of each other must not both win the last slot. A snapshot
    `active_count()` read ahead of the claim can't catch this -- two reads can
    both see the same free slot before either write lands -- so the capacity
    check has to run inside the same `UPDATE` as the flip, the way this test
    drives it directly rather than hoping asyncio scheduling hits the race."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database, wid="w0")  # created 'active', occupies one slot
            await mk_item(database, wid="w1")
            await mk_item(database, wid="w2")
            await database.write(lambda c: store.pause_work_item(c, "w1", []))
            await database.write(lambda c: store.pause_work_item(c, "w2", []))
            # limit=2, one slot already taken by w0: exactly one of the two
            # paused items below may claim the last slot.
            first = await database.write(
                lambda c: store.claim_for_run(c, "w2", from_statuses=["paused"], limit=2)
            )
            second = await database.write(
                lambda c: store.claim_for_run(c, "w1", from_statuses=["paused"], limit=2)
            )
            statuses = database.read(
                lambda c: {
                    r["id"]: r["status"]
                    for r in c.execute("SELECT id, status FROM work_items").fetchall()
                }
            )
            return first, second, statuses
        finally:
            await database.close()

    first, second, statuses = asyncio.run(scenario())
    assert (first, second) == (True, False)
    assert statuses == {"w0": "active", "w1": "paused", "w2": "active"}


def test_claim_for_run_limit_admits_when_a_slot_is_free(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database, wid="w1")
            await database.write(lambda c: store.pause_work_item(c, "w1", []))
            claimed = await database.write(
                lambda c: store.claim_for_run(c, "w1", from_statuses=["paused"], limit=1)
            )
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id='w1'").fetchone()
            )
            return claimed, row["status"]
        finally:
            await database.close()

    claimed, status = asyncio.run(scenario())
    assert claimed is True
    assert status == "active"


def test_pause_for_broken_base_pauses_and_records_who_and_what(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database, "w1")
            await mk_item(database, "w2")
            await database.write(
                lambda c: store.pause_for_broken_base(
                    c, "w2", broken_by="w1", follow_up_bead="Kraft-xyz"
                )
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, retry_at FROM work_items WHERE id = 'w2'"
                ).fetchone()
            )
            evts = database.read(lambda c: events.read_after(c, 0, "w2"))
            return row["status"], row["retry_at"], evts
        finally:
            await database.close()

    status, retry_at, evts = asyncio.run(scenario())
    assert status == "paused"
    assert retry_at is None
    payload = next(e["payload"] for e in evts if e["type"] == "paused_by_broken_base")
    assert payload == {"broken_by": "w1", "follow_up_bead": "Kraft-xyz"}


def test_current_step_round_trips_and_resets_when_the_node_moves(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)

            def step():
                return database.read(
                    lambda c: c.execute(
                        "SELECT current_step FROM work_items WHERE id='w1'"
                    ).fetchone()[0]
                )

            await database.write(lambda c: store.enter_node(c, "w1", "a"))
            await database.write(lambda c: store.set_current_step(c, "w1", 3))
            assert step() == 3
            await database.write(lambda c: store.enter_node(c, "w1", "a"))
            assert step() == 3, "re-entering the same node keeps the cursor"
            await database.write(lambda c: store.enter_node(c, "w1", "b"))
            assert step() == 0
        finally:
            await database.close()

    asyncio.run(scenario())
