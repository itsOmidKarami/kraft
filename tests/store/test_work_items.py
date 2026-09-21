import json
import subprocess

import pytest
from support.store_fixtures import CHAIN, mk_item

from kraft import db, events, store


@pytest.fixture
def conn(tmp_path):
    """A migrated connection with no writer: for the store calls that are plain
    functions of a connection."""
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    yield conn
    conn.close()


def _mk_item(conn, wid="w1", status="active", **kw):
    fields = {"bead_id": "B-1", "title": "t", "repo": "/r", "chain_template": "quick-task"}
    store.create_work_item(conn, id=wid, chain_definition=CHAIN, **(fields | kw))
    conn.execute("UPDATE work_items SET status = ? WHERE id = ?", (status, wid))


def _row(database, wid="w1", columns="*"):
    return database.read(
        lambda c: c.execute(f"SELECT {columns} FROM work_items WHERE id = ?", (wid,)).fetchone()
    )


async def test_create_work_item_writes_row_and_event(database):
    await mk_item(database)
    row = _row(database)
    assert row["status"] == "active"
    assert row["current_node_id"] is None
    assert row["bead_id"] == "B-1"
    evs = database.read(lambda c: events.read_after(c, 0))
    assert [e["type"] for e in evs] == ["work_item_created"]
    assert evs[0]["payload"] == {"title": "t", "repo": "/r", "chain_template": "quick-task"}


_PLAN = [{"kind": "plan", "path": ".engineering/plans/p.md"}]


@pytest.mark.parametrize(
    ("kwargs", "column", "stored", "extra_event"),
    [
        ({"description": "the long brief"}, "description", "the long brief", None),
        ({}, "description", None, None),
        ({"attachments": _PLAN}, "attachments", json.dumps(_PLAN), "work_item_attachments"),
        ({}, "attachments", None, None),
        ({}, "auto_gate", 0, None),
        ({"auto_gate": True}, "auto_gate", 1, None),
        ({"title": "Readable merge records"}, "branch", "kraft/readable-merge-records-w1", None),
    ],
    ids=[
        "description",
        "no-description-is-null",
        "attachments",
        "no-attachments-is-null",
        "auto-gate-defaults-off",
        "auto-gate",
        "branch-from-the-title",
    ],
)
def test_create_work_item_stores_its_column(conn, kwargs, column, stored, extra_event):
    """Every intake field lands in its column. The `work_item_created` payload
    stays a scannable label (no description in it), and attachments get their
    own event: the timeline has to explain why the chain has no spec node."""
    _mk_item(conn, **kwargs)
    assert conn.execute(f"SELECT {column} FROM work_items WHERE id='w1'").fetchone()[0] == stored
    evs = events.read_after(conn, 0, "w1")
    assert set(evs[0]["payload"]) == {"title", "repo", "chain_template"}
    assert [e["type"] for e in evs[1:]] == ([extra_event] if extra_event else [])


@pytest.mark.parametrize(
    ("branch", "expected"),
    [("kraft/readable-w1", "kraft/readable-w1"), (None, "kraft/w1")],
    ids=["the-row-is-the-truth", "falls-back-when-the-row-predates-the-column"],
)
def test_branch_for(conn, branch, expected):
    """The no-stranding guarantee: an in-flight item whose row was written
    before the migration keeps the `kraft/<id>` branch its worktree is on."""
    _mk_item(conn)
    conn.execute("UPDATE work_items SET branch = ? WHERE id='w1'", (branch,))
    assert store.branch_for(conn.execute("SELECT * FROM work_items").fetchone()) == expected


async def test_mark_needs_human_and_completed(database):
    await mk_item(database, "wh")
    await database.write(lambda c: store.mark_needs_human(c, "wh", "verify", "boom"))
    assert _row(database, "wh")["status"] == "needs_human"
    ev = database.read(lambda c: events.read_after(c, 0))[-1]
    assert ev["type"] == "work_item_needs_human"
    assert ev["payload"] == {"node_id": "verify", "reason": "boom"}

    await mk_item(database, "wc")
    await database.write(lambda c: store.mark_completed(c, "wc"))
    assert _row(database, "wc")["status"] == "completed"


async def test_needs_human_names_the_stop_it_is_about_not_an_older_failure(database):
    """Kraft-eh6p's "view log" button hangs off `session_id`. A node that failed
    once, was retried, and then stopped for a *question* must not hand the human
    the older failure's log: it looks like the answer and is not. And a stop with
    no session to explain it (a budget breach) must offer no button at all."""
    await mk_item(database, "wh")

    def session(sid, hook_point="on.test.run"):
        return database.write(
            lambda c: store.create_session(
                c,
                id=sid,
                work_item_id="wh",
                node_id="verify",
                hook_point=hook_point,
                log_path="/l",
                result_path="/r",
            )
        )

    for sid, status in (("older", "failed"), ("asked", "needs_context")):
        await session(sid)
        await database.write(lambda c, sid=sid, status=status: store.session_exited(c, sid, status))
    await database.write(
        lambda c: store.mark_needs_human(c, "wh", "verify", "needs_context: which db?")
    )
    asked = database.read(lambda c: events.read_after(c, 0, "wh"))[-1]["payload"]

    # A later session that explains nothing (a budget-refused launch) —
    # the button goes away rather than pointing back at "asked".
    await session("refused", "on.implementation.start")
    await database.write(lambda c: store.mark_needs_human(c, "wh", "verify", "over budget"))
    broke = database.read(lambda c: events.read_after(c, 0, "wh"))[-1]["payload"]

    assert asked["session_id"] == "asked", "the stop names an older, unrelated failure"
    assert "session_id" not in broke, "a stop no session explains still offered a log"


@pytest.mark.parametrize(
    ("setter", "column", "value"),
    [
        (store.set_base_ref, "base_ref", "abc123"),
        (store.set_ci_pipeline_ref, "ci_pipeline_ref", "abc123:456"),
        (store.set_escalation_session, "escalation_session_id", "cli-session-abc"),
    ],
    ids=["base_ref", "ci_pipeline_ref", "escalation_session"],
)
def test_a_setter_writes_its_column(conn, setter, column, value):
    _mk_item(conn)
    assert conn.execute(f"SELECT {column} FROM work_items WHERE id='w1'").fetchone()[0] is None
    setter(conn, "w1", value)
    assert conn.execute(f"SELECT {column} FROM work_items WHERE id='w1'").fetchone()[0] == value


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


@pytest.mark.parametrize(
    ("mark", "status", "event", "node"),
    [
        (store.mark_rate_limited, "rate_limited", "work_item_rate_limited", "implementation"),
        (store.mark_waiting, "waiting", "work_item_waiting", "mr_checks"),
    ],
    ids=["rate_limited", "waiting"],
)
async def test_a_timed_stop_sets_the_status_retry_at_and_event(database, mark, status, event, node):
    """`mark_waiting` mirrors `mark_rate_limited`: the poller reads status +
    retry_at, and the event is what the timeline shows a human."""
    await mk_item(database)
    await database.write(lambda c: mark(c, "w1", node, "2099-01-01T00:00:00+00:00"))
    row = _row(database, columns="status, retry_at")
    assert (row["status"], row["retry_at"]) == (status, "2099-01-01T00:00:00+00:00")
    ev = database.read(lambda c: events.read_after(c, 0))[-1]
    assert ev["type"] == event
    assert ev["payload"] == {"node_id": node, "retry_at": "2099-01-01T00:00:00+00:00"}


async def test_mark_needs_human_clears_retry_at(database):
    await mk_item(database)
    await database.write(
        lambda c: store.mark_rate_limited(c, "w1", "implementation", "2026-09-10T00:00:00Z")
    )
    await database.write(lambda c: store.mark_needs_human(c, "w1", "implementation", "boom"))
    row = _row(database, columns="status, retry_at")
    assert row["status"] == "needs_human"
    assert row["retry_at"] is None


async def test_set_title_records_an_event(database):
    """A title edit gets its own event type. Not a shared `work_item_edited`
    with `set_description`: the description is prepended to every agent
    instruction and the title is a label, so a timeline that cannot tell them
    apart answers neither question."""
    await mk_item(database)
    await database.write(lambda c: store.set_title(c, "w1", "a better label"))
    assert _row(database)["title"] == "a better label"

    evs = database.read(lambda c: events.read_after(c, 0, "w1"))
    edits = [e for e in evs if e["type"] == "work_item_title_edited"]
    assert [e["payload"]["title"] for e in edits] == ["a better label"]


def test_merge_rank_order_puts_the_deepest_path_first():
    assert store.merge_rank_order(["libs/a", "vendor/deep/b", "x"]) == [
        "vendor/deep/b",
        "libs/a",
        "x",
    ]


def test_add_repo_and_repos_for_round_trip(conn):
    _mk_item(conn)
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


def test_repos_for_is_empty_for_a_single_repo_item(conn):
    _mk_item(conn)
    assert store.repos_for(conn, "w1") == []


def test_archive_sets_archived_at_and_by_without_touching_status(conn):
    _mk_item(conn, status="completed")

    store.archive_work_item(conn, "w1", "you")

    row = conn.execute(
        "SELECT status, archived_at, archived_by FROM work_items WHERE id='w1'"
    ).fetchone()
    assert row["status"] == "completed"
    assert row["archived_by"] == "you"
    assert row["archived_at"]
    evs = [e for e in events.read_after(conn, 0, "w1") if e["type"] == "work_item_archived"]
    assert [e["payload"] for e in evs] == [{"by": "you"}]


def test_restore_clears_archived_columns(conn):
    _mk_item(conn, status="abandoned")
    store.archive_work_item(conn, "w1", "auto")

    store.restore_work_item(conn, "w1")

    row = conn.execute("SELECT archived_at, archived_by FROM work_items WHERE id='w1'").fetchone()
    assert row["archived_at"] is None
    assert row["archived_by"] is None


def _auto_and_manual(conn):
    """`w1` filed by hand on /b, `w2` picked up by auto-intake on /a."""
    _mk_item(conn, "w1", bead_id=None, title="manual", repo="/b")
    _mk_item(conn, "w2", title="auto", repo="/a", source="auto_intake", bead_priority=2)
    conn.commit()


def test_recent_auto_pickups_excludes_manually_created_items(conn):
    _auto_and_manual(conn)
    pickups = store.recent_auto_pickups(conn)
    assert [p["work_item_id"] for p in pickups] == ["w2"]
    assert pickups[0]["priority"] == 2


def test_last_auto_pickup_at_only_tracks_auto_intake_repos(conn):
    _auto_and_manual(conn)
    last = store.last_auto_pickup_at(conn)
    assert "/a" in last
    assert "/b" not in last


async def test_claim_for_run_is_atomic_between_two_callers(database):
    await mk_item(database)
    await database.write(lambda c: store.pause_work_item(c, "w1", []))
    first = await database.write(lambda c: store.claim_for_run(c, "w1", from_statuses=["paused"]))
    second = await database.write(lambda c: store.claim_for_run(c, "w1", from_statuses=["paused"]))
    assert (first, second) == (True, False)
    row = _row(database, columns="status, retry_at")
    assert row["status"] == "active"
    assert row["retry_at"] is None


async def test_claim_for_run_refuses_a_status_outside_the_set(database):
    await mk_item(database)  # created 'active'
    claimed = await database.write(lambda c: store.claim_for_run(c, "w1", from_statuses=["paused"]))
    assert claimed is False
    assert _row(database)["status"] == "active"


async def test_claim_for_run_limit_lets_only_one_of_two_racing_items_through(database):
    """Kraft-m43g, Kraft-nxht: two different items resumed/retried within
    milliseconds of each other must not both win the last slot. A snapshot
    `active_count()` read ahead of the claim can't catch this -- two reads can
    both see the same free slot before either write lands -- so the capacity
    check has to run inside the same `UPDATE` as the flip, the way this test
    drives it directly rather than hoping asyncio scheduling hits the race."""
    for wid in ("w0", "w1", "w2"):  # w0 stays 'active' and occupies one slot
        await mk_item(database, wid=wid)
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
            r["id"]: r["status"] for r in c.execute("SELECT id, status FROM work_items").fetchall()
        }
    )
    assert (first, second) == (True, False)
    assert statuses == {"w0": "active", "w1": "paused", "w2": "active"}


async def test_claim_for_run_limit_admits_when_a_slot_is_free(database):
    await mk_item(database, wid="w1")
    await database.write(lambda c: store.pause_work_item(c, "w1", []))
    claimed = await database.write(
        lambda c: store.claim_for_run(c, "w1", from_statuses=["paused"], limit=1)
    )
    assert claimed is True
    assert _row(database)["status"] == "active"


async def test_pause_for_broken_base_pauses_and_records_who_and_what(database):
    await mk_item(database, "w1")
    await mk_item(database, "w2")
    await database.write(
        lambda c: store.pause_for_broken_base(c, "w2", broken_by="w1", follow_up_bead="Kraft-xyz")
    )
    row = _row(database, "w2", "status, retry_at")
    assert row["status"] == "paused"
    assert row["retry_at"] is None
    evts = database.read(lambda c: events.read_after(c, 0, "w2"))
    payload = next(e["payload"] for e in evts if e["type"] == "paused_by_broken_base")
    assert payload == {"broken_by": "w1", "follow_up_bead": "Kraft-xyz"}


async def test_current_step_round_trips_and_resets_when_the_node_moves(database):
    await mk_item(database)

    def step():
        return _row(database, columns="current_step")[0]

    await database.write(lambda c: store.enter_node(c, "w1", "a"))
    await database.write(lambda c: store.set_current_step(c, "w1", 3))
    assert step() == 3
    await database.write(lambda c: store.enter_node(c, "w1", "a"))
    assert step() == 3, "re-entering the same node keeps the cursor"
    await database.write(lambda c: store.enter_node(c, "w1", "b"))
    assert step() == 0
