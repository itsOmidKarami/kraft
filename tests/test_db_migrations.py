"""Forward migrations: an old database reaches the current schema with its rows intact."""

import sqlite3

import pytest
from support import schema

from kraft import db


def test_migrations_keys_are_contiguous():
    """Two branches adding a migration under the same key merge as a silent
    last-write-wins dict literal, not a guaranteed git conflict -- nothing else
    catches a collision or a gap in the numbering (Kraft-cd47 hit this)."""
    keys = sorted(db._MIGRATIONS)
    assert keys == list(range(min(keys), db.SCHEMA_VERSION))


# The worker_sessions columns schema v4 added. Old-schema fixtures are built by
# subtraction from the current SCHEMA_SQL, so `_build_old_db` has to name what
# to remove for every version.
V4_COLS = (
    "-- usage capture",
    "started_at     TEXT,",
    "round          INTEGER",
    "model          TEXT",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "wall_ms",
)


def _build_old_db(conn, version, *, drop_lines=(), skip_stmts=(), replace=()):
    """Hand-build a pre-current schema: SCHEMA_SQL minus some lines/statements.

    `replace` is (needle, replacement-line) pairs for a line that has to change
    shape rather than disappear — a CHECK constraint that gained a value, say.
    """

    def rewrite(ln):
        for needle, new in replace:
            if needle in ln:
                return new
        return ln

    # columns and tables introduced after `version` were not there yet — decided
    # before the schema is built, or the drop never reaches it
    if version < 2:
        skip_stmts = (*skip_stmts, "retry_counters")
    if version < 3:
        drop_lines = (*drop_lines, "session_summary_ref")
    if version < 4:
        drop_lines = (*drop_lines, *V4_COLS)
    if version < 6:
        skip_stmts = (*skip_stmts, "auth_sessions")
    if version < 18:
        skip_stmts = (*skip_stmts, "work_item_repos")
    if version < 7:
        drop_lines = (*drop_lines, "submodules", "root_merge_policy", "-- cross-repo")
    if version < 8:
        drop_lines = (*drop_lines, "attachments      TEXT,")
    if version < 9:
        drop_lines = (*drop_lines, "base_ref         TEXT,")
    if version < 11:
        drop_lines = (*drop_lines, "bead_cwd")
    if version < 13:
        drop_lines = (*drop_lines, "description", "-- the brief this work item's")
    if version < 14:
        drop_lines = (
            *drop_lines,
            "branch           TEXT,",
            "-- the git branch this item's",
            "-- Computed once at intake",
            "-- NULL on items created before the column",
        )
    if version < 16:
        drop_lines = (
            *drop_lines,
            "implements_beads TEXT,",
            "-- sub-bead ids this item's description names",
            "-- from `description` at intake",
            "-- alongside `bead_id` on completion",
        )
    if version < 17:
        drop_lines = (*drop_lines, "retry_at         TEXT,", "-- set while status = 'rate_limited'")
    if version < 20:
        drop_lines = (*drop_lines, "escalation_session_id TEXT,")
    if version < 21:
        drop_lines = (*drop_lines, "auto_gate        INTEGER NOT NULL DEFAULT 0,")
    if version < 22:
        drop_lines = (*drop_lines, "agent_overrides TEXT,")
    if version < 25:
        drop_lines = (
            *drop_lines,
            "budget_set INTEGER NOT NULL DEFAULT 0,",
            "budget_usd REAL,",
            "node_overrides TEXT,",
        )
    if version < 26:
        drop_lines = (
            *drop_lines,
            "archived_at      TEXT,",
            "archived_by      TEXT,",
            "-- who and when a completed/abandoned item was archived",
            '-- NULL means "not archived". Never set on any other status',
            "-- does not change `status`",
            "-- abandoned. 'you' | 'auto', enforced in kraft.store, not by a CHECK:",
            "-- the two writers are archive_work_item's only two callers.",
        )
    if version < 28:
        drop_lines = (
            *drop_lines,
            "ci_pipeline_ref  TEXT,",
            "-- GitLab pipeline pinned by the last `on.ci.poll` read",
        )
    if version < 29:
        drop_lines = (*drop_lines, "thread         INTEGER NOT NULL DEFAULT 1,")
    if version < 32:
        drop_lines = (
            *drop_lines,
            "current_step     INTEGER",
            "-- the step group `current_node_id`",
            "-- resumed the node past its first",
        )
    if version < 31:
        drop_lines = (
            *drop_lines,
            "command        TEXT",
            "-- the exact command a subprocess session ran (Kraft-s7c04.35)",
            "-- every non-subprocess kind and for every row written before this column",
            "-- existed",
        )
        # `command` is the last worker_sessions column -- dropping it leaves
        # head_sha's own trailing comma dangling before the closing `);`.
        replace = (*replace, ("head_sha       TEXT,", "head_sha       TEXT"))
    if version < 33:
        drop_lines = (
            *drop_lines,
            "materialized_chain TEXT,",
            "run_fork_parent  TEXT,",
            "-- template schema V1's immutable work-item input",
            "-- Beside `chain_definition`, not replacing it",
            "-- the run this one forked from (Phase 5 retry forks)",
        )
    schema = "\n".join(
        rewrite(ln) for ln in db.SCHEMA_SQL.splitlines() if not any(d in ln for d in drop_lines)
    )
    conn.execute("BEGIN")
    for stmt in (x.strip() for x in schema.split(";")):
        if stmt and not any(skip in stmt for skip in skip_stmts):
            conn.execute(stmt)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()


def test_migrate_v1_to_v2_adds_retry_counters(tmp_path):
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 1)
    schema.insert_item(conn)
    conn.commit()
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert "retry_counters" in schema.tables(conn2)
    assert conn2.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1  # data preserved


def test_migrate_mid_step_failure_rolls_back_whole_run(monkeypatch, tmp_path):
    """A v1 database has two steps to apply. If the second raises, the first must
    be rolled back too and user_version must stay where it started — the runner's
    BEGIN spans the whole range, not one step (Kraft-g5x)."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 1)
    assert "retry_counters" not in schema.tables(conn)

    broken = dict(db._MIGRATIONS)
    broken[2] = ["INVALID SQL STATEMENT"]
    monkeypatch.setattr(db, "_MIGRATIONS", broken)

    with pytest.raises(sqlite3.OperationalError):
        db.migrate(conn)

    # step 1 (retry_counters) must not have survived the failure of step 2
    assert "retry_counters" not in schema.tables(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1

    # and the database is still migratable once the broken step is gone
    monkeypatch.undo()
    db.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert "retry_counters" in schema.tables(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(worker_sessions)").fetchall()}
    assert "session_summary_ref" in cols


def test_migrate_v4_to_v5_rebuilds_work_items_for_the_paused_status(tmp_path):
    """v4's status CHECK has no 'paused'; SQLite cannot alter a constraint, so the
    table is rebuilt. Rows and the events FK have to survive it."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn,
        4,
        drop_lines=(
            "pending_steer_context",
            "-- steer text",
            "                     ('active', 'needs_human', 'completed', 'paused', 'abandoned',",
            "                      'rate_limited', 'waiting')),",
        ),
        replace=(
            (
                "status           TEXT NOT NULL CHECK (status IN",
                "  status           TEXT NOT NULL CHECK (status IN "
                "('active', 'needs_human', 'completed')),",
            ),
        ),
    )
    schema.insert_item(conn)
    conn.execute(
        "INSERT INTO events (work_item_id, type, payload, created_at) "
        "VALUES ('w1','node_started','{}','now')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE work_items SET status = 'paused' WHERE id = 'w1'")
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn2.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1
    assert conn2.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    conn2.execute("UPDATE work_items SET status = 'paused' WHERE id = 'w1'")
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(work_items)").fetchall()}
    assert "pending_steer_context" in cols
    # the events FK still points somewhere real after the drop/rename
    assert conn2.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migrate_v9_to_v10_widens_worker_sessions_status(tmp_path):
    """v9's worker_sessions CHECK has no 'done_with_concerns'/'needs_context'; SQLite
    cannot alter a constraint, so the table is rebuilt. Every column has to survive
    the rebuild, and the status index has to come back too."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn,
        9,
        replace=(
            (
                "status         TEXT NOT NULL CHECK (status IN",
                "  status         TEXT NOT NULL CHECK (status IN "
                "('pending', 'running', 'done', 'failed', 'capped_out', 'paused', 'unknown')),",
            ),
            ("'unknown',", ""),
            ("done_with_concerns", ""),
            ("                    'waiting', 'conflict', 'infra', 'infra_stop')),", ""),
        ),
    )
    schema.insert_item(conn)
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, "
        "pid_start_time, log_path, result_path, status, attempt, session_summary_ref, "
        "created_at, started_at, round, model, tokens_in, tokens_out, cost_usd, "
        "wall_ms, exited_at) VALUES ('s1', 'w1', 'verify', 'on.test.run', 123, 456.7, "
        "'/l', '/r', 'unknown', 2, '.engineering/sessions/s1.md', 'now', 'started', "
        "3, 'claude', 10, 20, 0.5, 1000, 'exited')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE worker_sessions SET status = 'done_with_concerns' WHERE id = 's1'")
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION

    row = conn2.execute("SELECT * FROM worker_sessions WHERE id = 's1'").fetchone()
    assert row["work_item_id"] == "w1"
    assert row["node_id"] == "verify"
    assert row["hook_point"] == "on.test.run"
    assert row["pid"] == 123
    assert row["pid_start_time"] == 456.7
    assert row["log_path"] == "/l"
    assert row["result_path"] == "/r"
    assert row["status"] == "unknown"
    # migration 14 (Kraft-kq8m) recomputes attempt from the real rows rather than
    # carrying the fixture's literal 2 forward: this is the only session in its
    # (work_item_id, node_id, hook_point), so the backfill numbers it 1.
    assert row["attempt"] == 1
    assert row["session_summary_ref"] == ".engineering/sessions/s1.md"
    assert row["created_at"] == "now"
    assert row["started_at"] == "started"
    assert row["round"] == 3
    # migration 29 (Kraft-s7c04.15) empties `model` rather than carry the
    # fixture's value forward: every value ever written to this column came from
    # `usage._model_of` reading the first `modelUsage` key, which is the haiku
    # warm-up. The measured columns beside it are untouched.
    assert row["model"] is None
    assert row["tokens_in"] == 10
    assert row["tokens_out"] == 20
    assert row["cost_usd"] == 0.5
    assert row["wall_ms"] == 1000
    assert row["exited_at"] == "exited"

    # both new statuses are now accepted by the rebuilt CHECK
    conn2.execute("UPDATE worker_sessions SET status = 'done_with_concerns' WHERE id = 's1'")
    conn2.execute("UPDATE worker_sessions SET status = 'needs_context' WHERE id = 's1'")


def test_migrate_v26_to_v27_widens_worker_sessions_status_for_ci_verdicts(tmp_path):
    """v26's worker_sessions CHECK has no 'conflict'/'infra'/'infra_stop' --
    `ci_poll`'s honest verdicts (Kraft-cbr, Kraft-bjjm, Kraft-h81i). SQLite
    cannot alter a constraint, so the table is rebuilt; every column has to
    survive it."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn,
        26,
        replace=(
            (
                "                    'waiting', 'conflict', 'infra', 'infra_stop')),",
                "                    'waiting')),",
            ),
        ),
    )
    schema.insert_item(conn)
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, "
        "pid_start_time, log_path, result_path, status, attempt, session_summary_ref, "
        "created_at, started_at, round, model, tokens_in, tokens_out, cost_usd, "
        "wall_ms, exited_at) VALUES ('s1', 'w1', 'mr_checks', 'on.ci.poll', 123, 456.7, "
        "'/l', '/r', 'waiting', 1, NULL, 'now', 'started', 0, NULL, NULL, NULL, NULL, "
        "NULL, NULL)"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE worker_sessions SET status = 'conflict' WHERE id = 's1'")
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION

    row = conn2.execute("SELECT * FROM worker_sessions WHERE id = 's1'").fetchone()
    assert row["work_item_id"] == "w1"
    assert row["node_id"] == "mr_checks"
    assert row["status"] == "waiting"

    conn2.execute("UPDATE worker_sessions SET status = 'conflict' WHERE id = 's1'")
    conn2.execute("UPDATE worker_sessions SET status = 'infra' WHERE id = 's1'")
    conn2.execute("UPDATE worker_sessions SET status = 'infra_stop' WHERE id = 's1'")

    index_names = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_worker_sessions_status" in index_names


def test_a_fresh_schema_and_a_fully_migrated_one_agree(tmp_path):
    """SCHEMA_SQL and the migration path must agree -- a fresh install and an
    upgraded one are the same database. Every column of every table, from the
    oldest schema `_build_old_db` can build."""
    fresh = db._connect(tmp_path / "fresh.db")
    db.migrate(fresh)
    old = db._connect(tmp_path / "old.db")
    _build_old_db(old, 1)
    db.migrate(old)

    def shape(conn):
        return {
            table: {
                (r["name"], r["type"], r["notnull"], r["dflt_value"], r["pk"])
                for r in conn.execute(f"PRAGMA table_info({table})")
            }
            for table in schema.tables(conn)
            if not table.startswith("sqlite_")
        }

    assert shape(old) == shape(fresh)


ADDED_COLUMNS = [
    (2, "worker_sessions", ("session_summary_ref",), None),
    (7, "work_items", ("attachments",), None),
    # keyed v8 -> v9: origin/main's `attachments` took key 7 first
    (8, "work_items", ("base_ref",), None),
    # an auto-intaken bead's own workspace (Kraft-8mu.5.2); NULL means
    # KRAFT_BD_CWD
    (10, "work_items", ("bead_cwd",), None),
    (12, "work_items", ("description",), None),
    # NULL keeps an in-flight item on the `kraft/<id>` branch its worktree
    # is already checked out on
    (13, "work_items", ("branch",), None),
    # NULL: no sub-beads extracted (Kraft-p8q1)
    (15, "work_items", ("implements_beads",), None),
    (19, "work_items", ("escalation_session_id",), None),
    (20, "work_items", ("auto_gate",), 0),
    (21, "work_items", ("agent_overrides",), None),
    (25, "work_items", ("archived_at", "archived_by"), None),
    (27, "work_items", ("ci_pipeline_ref",), None),
    # 1 for every pre-existing session (Kraft-dkb6g)
    (28, "worker_sessions", ("thread",), 1),
    (30, "worker_sessions", ("command",), None),
    (31, "work_items", ("current_step",), 0),
    # template schema V1 is additive: an existing item keeps the
    # `chain_definition` it has and gains two NULL columns (`_MIGRATIONS[32]`)
    (32, "work_items", ("materialized_chain", "run_fork_parent"), None),
]


@pytest.mark.parametrize(
    ("version", "table", "columns", "old_row_value"),
    ADDED_COLUMNS,
    ids=[f"v{v}-{'+'.join(cols)}" for v, _, cols, _ in ADDED_COLUMNS],
)
def test_an_old_db_gains_the_column_and_keeps_its_rows(
    tmp_path, version, table, columns, old_row_value
):
    """Built at `version` (the shape before `_MIGRATIONS[version]`), holding a
    work item and a session, then migrated: the column is there, the rows are
    intact and read the column's value for a row written before it existed."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, version)
    schema.insert_item(conn, chain_definition='{"nodes": []}')
    schema.insert_session(conn, status="done")
    conn.commit()
    conn.close()

    conn = db._connect(path)
    db.migrate(conn)
    assert schema.version(conn) == db.SCHEMA_VERSION
    assert set(columns) <= schema.columns(conn, table)
    item = conn.execute("SELECT * FROM work_items WHERE id = 'w1'").fetchone()
    assert (item["title"], item["status"], item["chain_definition"]) == (
        "t",
        "active",
        '{"nodes": []}',
    )
    assert (
        conn.execute("SELECT status FROM worker_sessions WHERE id = 's1'").fetchone()[0] == "done"
    )
    row = conn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchone()
    assert [row[c] for c in columns] == [old_row_value] * len(columns)


def test_migrate_v16_to_v17_drops_chain_template_not_null(tmp_path):
    """SQLite cannot alter a column constraint, so v16's `chain_template TEXT
    NOT NULL` is rebuilt the same 4-step way migration 11 was (Kraft-cd47).
    Existing rows keep their value; a new row may now write NULL. The rebuild
    also carries `implements_beads` (migration 15) forward, since v16 already
    has it."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn,
        16,
        replace=(("chain_template   TEXT,", "chain_template   TEXT NOT NULL,"),),
    )
    schema.insert_item(conn)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
            "status, created_at, updated_at) VALUES ('w2','t','/r',NULL,'{}',"
            "'active','now','now')"
        )
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    # the pre-existing row survived the rebuild with its value intact
    row = conn2.execute("SELECT chain_template FROM work_items WHERE id='w1'").fetchone()
    assert row["chain_template"] == "quick-task"
    # and implements_beads (migration 15) survived the rebuild too
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(work_items)")}
    assert "implements_beads" in cols
    # and the column now accepts NULL
    conn2.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w2','t','/r',NULL,'{}',"
        "'active','now','now')"
    )
    row2 = conn2.execute("SELECT chain_template FROM work_items WHERE id='w2'").fetchone()
    assert row2[0] is None


def test_migration_14_backfills_worker_session_attempts(tmp_path):
    """Every row ever written carried the literal attempt = 1 (Kraft-kq8m), so
    fixing the INSERT alone leaves the observed items wrong for the life of the
    database. The backfill numbers each (work item, node, hook point) 1..N oldest
    first, with (created_at, id) breaking a shared timestamp."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 13)
    schema.insert_item(conn)
    for sid, hook, created in [
        ("s1", "on.ci.poll", "2026-01-01T00:00:00"),
        ("s2", "on.ci.poll", "2026-01-01T00:00:01"),
        # s3 shares s2's timestamp: the id has to break the tie, or the two
        # collide on one number and the third is never used
        ("s3", "on.ci.poll", "2026-01-01T00:00:01"),
        ("s4", "on.mr.open", "2026-01-01T00:00:00"),
    ]:
        conn.execute(
            "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, "
            "log_path, result_path, status, attempt, created_at) "
            "VALUES (?, 'w1', 'mr_checks', ?, '/l', '/r', 'done', 1, ?)",
            (sid, hook, created),
        )
    conn.commit()
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    got = {r["id"]: r["attempt"] for r in conn2.execute("SELECT id, attempt FROM worker_sessions")}
    assert got == {"s1": 1, "s2": 2, "s3": 3, "s4": 1}


def test_migration_29_clears_the_unreliable_model_column(tmp_path):
    """Every `model` ever written came from `usage._model_of` reading the FIRST
    `modelUsage` key, which is Claude Code's haiku warm-up rather than the model
    that did the work (Kraft-s7c04.15). NULL already means "no model reported"
    to every reader, so the column is emptied rather than left asserting
    something false -- a cost-by-model comparison across this migration has to
    exclude NULL rows, and cannot if a wrong value looks like a right one."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 29)
    conn.execute("PRAGMA user_version = 29")
    schema.insert_item(conn)
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
        "result_path, status, created_at, model, cost_usd) VALUES "
        "('s1','w1','verify','on.review.local.run','/l','/r','done','now',"
        "'claude-haiku-4-5-20251001', 1.25)"
    )
    conn.commit()
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    row = conn2.execute("SELECT model, cost_usd, status FROM worker_sessions").fetchone()
    assert row["model"] is None
    # only the model is unreliable; everything measured alongside it survives
    assert row["cost_usd"] == 1.25
    assert row["status"] == "done"


def test_migrate_v16_to_v17_rebuilds_for_rate_limited(tmp_path):
    """v16's CHECKs have no 'rate_limited' and work_items has no retry_at;
    SQLite cannot alter a constraint, so both tables are rebuilt the same
    12-step way migration 4 and 9 used."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn,
        16,
        drop_lines=(
            "                      'rate_limited', 'waiting')),",
            "                    'waiting', 'conflict', 'infra', 'infra_stop')),",
        ),
        replace=(
            (
                "'paused', 'abandoned',",
                "                     ('active', 'needs_human', 'completed', 'paused', "
                "'abandoned')),",
            ),
            (
                "'done_with_concerns', 'needs_context', 'rate_limited', 'config_error',",
                "                    'done_with_concerns', 'needs_context')),",
            ),
        ),
    )
    schema.insert_item(conn)
    schema.insert_session(conn)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE work_items SET status = 'rate_limited' WHERE id = 'w1'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE worker_sessions SET status = 'rate_limited' WHERE id = 's1'")
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn2.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1
    assert conn2.execute("SELECT count(*) FROM worker_sessions").fetchone()[0] == 1

    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(work_items)")}
    assert "retry_at" in cols
    row = conn2.execute("SELECT retry_at FROM work_items WHERE id = 'w1'").fetchone()
    assert row["retry_at"] is None  # pre-existing row: NULL, not "not rate limited yet"

    conn2.execute("UPDATE work_items SET status = 'rate_limited', retry_at = 'x' WHERE id = 'w1'")
    conn2.execute("UPDATE worker_sessions SET status = 'rate_limited' WHERE id = 's1'")
    assert conn2.execute("PRAGMA foreign_key_check").fetchall() == []
    index_names = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_worker_sessions_status" in index_names


def test_migrate_v33_to_v34_renames_stored_task_progress_events(tmp_path):
    """Kraft-7hy7x: `task_progress` became `plan_progress`, and every reader
    (the Timeline's grouping, the board's "Task N of M") matches the new name
    only. An item filed before the rename keeps its progress because the stored
    rows are renamed once, here, rather than every reader learning both."""
    conn = db._connect(tmp_path / "orchestrator.db")
    _build_old_db(conn, 33)
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','default','{}',"
        "'active','now','now')"
    )
    for type_ in ("task_progress", "node_started", "task_progress"):
        conn.execute(
            "INSERT INTO events (work_item_id, type, payload, created_at) "
            "VALUES ('w1', ?, '{\"task\": 1, \"total\": 3}', 'now')",
            (type_,),
        )
    conn.commit()

    db.migrate(conn)

    types = [r[0] for r in conn.execute("SELECT type FROM events ORDER BY seq")]
    assert types == ["plan_progress", "node_started", "plan_progress"]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
