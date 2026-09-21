from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_STOP = object()

SCHEMA_VERSION = 35

SCHEMA_SQL = """
CREATE TABLE work_items (
  id               TEXT PRIMARY KEY,
  bead_id          TEXT,
  title            TEXT NOT NULL,
  -- the brief this work item's agents are given, ahead of any attachment note
  description      TEXT,
  repo             TEXT NOT NULL,
  -- the template chosen at intake, or NULL when none was chosen explicitly
  -- (Kraft-cd47) -- resolved to the `default` template only at the point one
  -- is looked up, so NULL stays distinguishable from an item that named
  -- `chain_template: "default"` outright.
  chain_template   TEXT,
  chain_definition TEXT NOT NULL,
  current_node_id  TEXT,
  status           TEXT NOT NULL CHECK (status IN
                     ('active', 'needs_human', 'completed', 'paused', 'abandoned',
                      'rate_limited', 'waiting')),
  -- steer text a human left while paused, consumed by the next agent launch
  pending_steer_context TEXT,
  -- cross-repo (06): submodule paths chosen at intake, and what happens to the
  -- root repo's pointer once they merge
  submodules       TEXT,
  root_merge_policy TEXT,
  -- documents attached at intake (Kraft-dgh): [{"kind": "spec"|"plan", "path": ...}]
  attachments      TEXT,
  -- the commit a work item's diff is measured against (Kraft-8mu.2)
  base_ref         TEXT,
  -- the bd workspace this item's bead lives in, when it is not the instance-wide
  -- KRAFT_BD_CWD: an auto-intaken bead is adopted from its own repo's .beads
  -- and can only be closed there (Kraft-8mu.5.2). NULL means KRAFT_BD_CWD.
  -- Rows created since Kraft-ibwj always set it, to the resolved workspace --
  -- KRAFT_BD_CWD if set, else the item's repo. Only older rows are NULL.
  bead_cwd         TEXT,
  -- the git branch this item's worktree, merge request and merge all name.
  -- Computed once at intake (Kraft-nhps) so the three call sites cannot drift.
  -- NULL on items created before the column, which keep `kraft/<uuid>`.
  branch           TEXT,
  -- sub-bead ids this item's description names (Kraft-p8q1): JSON list, extracted
  -- from `description` at intake, e.g. ["Kraft-p8q1", "Kraft-ikze"]. Closed
  -- alongside `bead_id` on completion -- see `_close_beads`.
  implements_beads TEXT,
  -- set while status = 'rate_limited': when the agent's rate limit resets and
  -- `rate_limit_retry.poller` may relaunch the item. NULL otherwise.
  retry_at         TEXT,
  -- the `claude` CLI's own session id for this item's escalation thread
  -- (distinct from any worker_sessions.id) -- see `escalate.py`. NULL until
  -- the first escalation turn.
  escalation_session_id TEXT,
  -- whether this item's `auto_escalate` gates may be reviewed by an agent
  -- before a human sees them (Kraft-zr3s). Off unless a human asked for it.
  auto_gate        INTEGER NOT NULL DEFAULT 0,
  -- a work item's own model/effort override (Kraft-4k6l): JSON object with
  -- up to keys model, escalate_model, effort. NULL means "use the template's
  -- own binding". Read fresh at every dispatch, not snapshotted into
  -- chain_definition, so a paused item can be made cheaper or stronger
  -- before its next retry.
  agent_overrides TEXT,
  -- per-item spend cap (UI v2 · 04). `budget_set` distinguishes "never
  -- customized" (0, `budget_usd` ignored, policy default applies) from an
  -- explicit choice (1): `budget_usd` NULL then means "no cap", and a
  -- number means that cap. See `store.budget.effective_work_item_cap`,
  -- the only place that resolves these two columns.
  budget_set INTEGER NOT NULL DEFAULT 0,
  budget_usd REAL,
  -- per-node field overrides materialized on top of this item's own
  -- chain_definition (UI v2 · 04): JSON {node_id: {field: value}}. Applies
  -- only to fields this MR added support for (today: auto_escalate). NULL
  -- or {} means no overrides. `store.chain.effective_chain` folds this over
  -- chain_definition at read time -- nothing else reads chain_definition's
  -- node fields directly once this exists.
  node_overrides TEXT,
  -- who and when a completed/abandoned item was archived (UI v2 · 03).
  -- NULL means "not archived". Never set on any other status -- archiving
  -- does not change `status` -- "Ended as" keeps reading completed/
  -- abandoned. 'you' | 'auto', enforced in kraft.store, not by a CHECK:
  -- the two writers are archive_work_item's only two callers.
  archived_at      TEXT,
  archived_by      TEXT,
  -- GitLab pipeline pinned by the last `on.ci.poll` read, stored as
  -- "<head_sha>:<pipeline_id>" (Kraft-ivh1). Empty until the first poll,
  -- cleared on retry_after_cap alongside the ci_wait/ci_infra counters.
  ci_pipeline_ref  TEXT,
  -- the step group `current_node_id` last began. 0 unless a wait or a retry
  -- resumed the node past its first group. Reset whenever the node changes.
  current_step     INTEGER NOT NULL DEFAULT 0,
  -- template schema V1's immutable work-item input (`MaterializedChain.to_json`).
  -- Beside `chain_definition`, not replacing it -- see _MIGRATIONS[32].
  materialized_chain TEXT,
  -- the materialization of the run fork this item is executing
  -- (`RunFork.materialized_chain`, copied here so every reader of the row
  -- gets it). NULL until the first retry: the intake snapshot is the run.
  run_chain        TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE TABLE events (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  work_item_id TEXT NOT NULL REFERENCES work_items(id),
  type         TEXT NOT NULL,
  payload      TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE INDEX idx_events_work_item ON events(work_item_id, seq);

CREATE TABLE worker_sessions (
  id             TEXT PRIMARY KEY,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  node_id        TEXT NOT NULL,
  hook_point     TEXT NOT NULL,
  pid            INTEGER,
  pid_start_time REAL,
  log_path       TEXT NOT NULL,
  result_path    TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN
                   ('pending', 'running', 'done', 'failed', 'capped_out', 'paused', 'unknown',
                    'done_with_concerns', 'needs_context', 'rate_limited', 'config_error',
                    'waiting', 'conflict', 'infra', 'infra_stop')),
  attempt        INTEGER NOT NULL DEFAULT 1,
  thread         INTEGER NOT NULL DEFAULT 1,
  session_summary_ref TEXT,
  created_at     TEXT NOT NULL,
  -- usage capture (handoff spec §8): stamped when the session starts and ends
  started_at     TEXT,
  round          INTEGER NOT NULL DEFAULT 0,
  model          TEXT,
  tokens_in      INTEGER,
  tokens_out     INTEGER,
  cost_usd       REAL,
  wall_ms        INTEGER,
  exited_at      TEXT,
  -- the worktree HEAD at the moment this measuring task ran (Kraft-lu2) -- NULL
  -- for a builtin/agent task that stamps nothing, and for every historical row
  head_sha       TEXT,
  -- the exact command a subprocess session ran (Kraft-s7c04.35) -- NULL for
  -- every non-subprocess kind and for every row written before this column
  -- existed
  command        TEXT
);

CREATE INDEX idx_worker_sessions_status ON worker_sessions(status);

CREATE TABLE auth_sessions (
  id           TEXT PRIMARY KEY,
  label        TEXT,
  ip           TEXT,
  created_at   TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at   TEXT NOT NULL
);

CREATE TABLE retry_counters (
  work_item_id  TEXT NOT NULL REFERENCES work_items(id),
  key           TEXT NOT NULL,
  count         INTEGER NOT NULL DEFAULT 0,
  cap_attempts  INTEGER NOT NULL,
  cap_wall_s    INTEGER NOT NULL,
  started_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (work_item_id, key)
);

CREATE TABLE work_item_repos (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  -- absolute path: the worktree root for role='root', worktree/submodule_path
  -- for role='submodule'
  repo_path      TEXT NOT NULL,
  role           TEXT NOT NULL CHECK (role IN ('root', 'submodule')),
  -- repo-relative path under the worktree, NULL for role='root'
  submodule_path TEXT,
  -- deepest submodule = 1, root = max (design 3a: a submodule must merge
  -- before the parent whose pointer names it)
  merge_rank     INTEGER NOT NULL,
  bead_id        TEXT,
  -- JSON {"number": int, "url": str} once this repo's merge request exists
  mr_ref         TEXT,
  merge_state    TEXT NOT NULL DEFAULT 'pending' CHECK (merge_state IN
                   ('pending', 'open', 'merged', 'failed')),
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE INDEX idx_work_item_repos_item ON work_item_repos(work_item_id, merge_rank);

-- One row per retry (`retry-creates-an-immutable-run-fork`): the run a retry
-- forked from (`parent`, NULL for the intake run), what it retried, and its own
-- copy of the materialization with any retry override applied. Never updated
-- or deleted -- `_RUN_FORK_TRIGGERS` refuses both.
CREATE TABLE run_forks (
  id                 TEXT PRIMARY KEY,
  work_item_id       TEXT NOT NULL REFERENCES work_items(id),
  parent             TEXT REFERENCES run_forks(id),
  scope              TEXT NOT NULL CHECK (scope IN ('work_item', 'node', 'step', 'task')),
  -- the canonical path retried, NULL for a work-item restart
  path               TEXT,
  -- the events seq the fork starts after: everything at or before it is the
  -- prior runs' data, kept
  after_seq          INTEGER NOT NULL,
  materialized_chain TEXT NOT NULL,
  -- what the retry changed (path, task_config, policy), NULL when nothing
  override           TEXT,
  created_at         TEXT NOT NULL
);

CREATE INDEX idx_run_forks_item ON run_forks(work_item_id)
"""

#: A trigger body holds `;`, which the naive split of `SCHEMA_SQL` would cut, so
#: the two statements that make a fork immutable live here and run after it.
_RUN_FORK_TRIGGERS = [
    "CREATE TRIGGER run_forks_immutable BEFORE UPDATE ON run_forks "
    "BEGIN SELECT RAISE(ABORT, 'a run fork is immutable'); END",
    "CREATE TRIGGER run_forks_undeletable BEFORE DELETE ON run_forks "
    "BEGIN SELECT RAISE(ABORT, 'a run fork is immutable'); END",
]

_MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE retry_counters (
  work_item_id  TEXT NOT NULL REFERENCES work_items(id),
  key           TEXT NOT NULL,
  count         INTEGER NOT NULL DEFAULT 0,
  cap_attempts  INTEGER NOT NULL,
  cap_wall_s    INTEGER NOT NULL,
  started_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (work_item_id, key)
)"""
    ],
    2: ["ALTER TABLE worker_sessions ADD COLUMN session_summary_ref TEXT"],
    3: [
        "ALTER TABLE worker_sessions ADD COLUMN started_at TEXT",
        "ALTER TABLE worker_sessions ADD COLUMN round INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE worker_sessions ADD COLUMN model TEXT",
        "ALTER TABLE worker_sessions ADD COLUMN tokens_in INTEGER",
        "ALTER TABLE worker_sessions ADD COLUMN tokens_out INTEGER",
        "ALTER TABLE worker_sessions ADD COLUMN cost_usd REAL",
        "ALTER TABLE worker_sessions ADD COLUMN wall_ms INTEGER",
    ],
    # 'paused' has to join the status CHECK, and SQLite cannot alter a
    # constraint — so work_items is rebuilt the documented 12-step way.
    4: [
        """CREATE TABLE work_items_new (
  id               TEXT PRIMARY KEY,
  bead_id          TEXT,
  title            TEXT NOT NULL,
  repo             TEXT NOT NULL,
  chain_template   TEXT NOT NULL,
  chain_definition TEXT NOT NULL,
  current_node_id  TEXT,
  status           TEXT NOT NULL CHECK (status IN
                     ('active', 'needs_human', 'completed', 'paused')),
  pending_steer_context TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
)""",
        """INSERT INTO work_items_new (id, bead_id, title, repo, chain_template,
  chain_definition, current_node_id, status, created_at, updated_at)
SELECT id, bead_id, title, repo, chain_template, chain_definition, current_node_id,
       status, created_at, updated_at FROM work_items""",
        "DROP TABLE work_items",
        "ALTER TABLE work_items_new RENAME TO work_items",
    ],
    6: [
        "ALTER TABLE work_items ADD COLUMN submodules TEXT",
        "ALTER TABLE work_items ADD COLUMN root_merge_policy TEXT",
    ],
    5: [
        """CREATE TABLE auth_sessions (
  id           TEXT PRIMARY KEY,
  label        TEXT,
  ip           TEXT,
  created_at   TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at   TEXT NOT NULL
)""",
    ],
    7: ["ALTER TABLE work_items ADD COLUMN attachments TEXT"],
    8: ["ALTER TABLE work_items ADD COLUMN base_ref TEXT"],
    10: ["ALTER TABLE work_items ADD COLUMN bead_cwd TEXT"],
    # 'done_with_concerns' and 'needs_context' join the status CHECK, and SQLite
    # cannot alter a constraint — so worker_sessions is rebuilt the same 12-step way.
    9: [
        """CREATE TABLE worker_sessions_new (
  id             TEXT PRIMARY KEY,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  node_id        TEXT NOT NULL,
  hook_point     TEXT NOT NULL,
  pid            INTEGER,
  pid_start_time REAL,
  log_path       TEXT NOT NULL,
  result_path    TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN
                   ('pending', 'running', 'done', 'failed', 'capped_out', 'paused', 'unknown',
                    'done_with_concerns', 'needs_context')),
  attempt        INTEGER NOT NULL DEFAULT 1,
  session_summary_ref TEXT,
  created_at     TEXT NOT NULL,
  started_at     TEXT,
  round          INTEGER NOT NULL DEFAULT 0,
  model          TEXT,
  tokens_in      INTEGER,
  tokens_out     INTEGER,
  cost_usd       REAL,
  wall_ms        INTEGER,
  exited_at      TEXT
)""",
        """INSERT INTO worker_sessions_new (id, work_item_id, node_id, hook_point, pid,
  pid_start_time, log_path, result_path, status, attempt, session_summary_ref,
  created_at, started_at, round, model, tokens_in, tokens_out, cost_usd, wall_ms,
  exited_at)
SELECT id, work_item_id, node_id, hook_point, pid, pid_start_time, log_path,
       result_path, status, attempt, session_summary_ref, created_at, started_at,
       round, model, tokens_in, tokens_out, cost_usd, wall_ms, exited_at
FROM worker_sessions""",
        "DROP TABLE worker_sessions",
        "ALTER TABLE worker_sessions_new RENAME TO worker_sessions",
        "CREATE INDEX idx_worker_sessions_status ON worker_sessions(status)",
    ],
    # 'abandoned' joins the status CHECK, and SQLite cannot alter a constraint —
    # so work_items is rebuilt the same 12-step way migration 4 used (Kraft-x85).
    11: [
        """CREATE TABLE work_items_new (
  id               TEXT PRIMARY KEY,
  bead_id          TEXT,
  title            TEXT NOT NULL,
  repo             TEXT NOT NULL,
  chain_template   TEXT NOT NULL,
  chain_definition TEXT NOT NULL,
  current_node_id  TEXT,
  status           TEXT NOT NULL CHECK (status IN
                     ('active', 'needs_human', 'completed', 'paused', 'abandoned')),
  pending_steer_context TEXT,
  submodules       TEXT,
  root_merge_policy TEXT,
  attachments      TEXT,
  base_ref         TEXT,
  bead_cwd         TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
)""",
        """INSERT INTO work_items_new (id, bead_id, title, repo, chain_template,
  chain_definition, current_node_id, status, pending_steer_context, submodules,
  root_merge_policy, attachments, base_ref, bead_cwd, created_at, updated_at)
SELECT id, bead_id, title, repo, chain_template, chain_definition, current_node_id,
       status, pending_steer_context, submodules, root_merge_policy, attachments,
       base_ref, bead_cwd, created_at, updated_at FROM work_items""",
        "DROP TABLE work_items",
        "ALTER TABLE work_items_new RENAME TO work_items",
    ],
    12: ["ALTER TABLE work_items ADD COLUMN description TEXT"],
    13: ["ALTER TABLE work_items ADD COLUMN branch TEXT"],
    # Data only, no schema change: every worker_sessions row was written with the
    # literal attempt = 1 (Kraft-kq8m). Number each (work item, node, hook point)
    # 1..N in (created_at, id) order — the row-value comparison breaks a shared
    # timestamp, so the numbering has no duplicates and no gaps.
    14: [
        """UPDATE worker_sessions SET attempt = (
  SELECT COUNT(*) FROM worker_sessions p
   WHERE p.work_item_id = worker_sessions.work_item_id
     AND p.node_id      = worker_sessions.node_id
     AND p.hook_point   = worker_sessions.hook_point
     AND (p.created_at, p.id) <= (worker_sessions.created_at, worker_sessions.id))"""
    ],
    15: ["ALTER TABLE work_items ADD COLUMN implements_beads TEXT"],
    # 'rate_limited' joins both CHECKs, and work_items gains `retry_at`; SQLite
    # cannot alter a constraint, so both tables are rebuilt the same 12-step way
    # migrations 4 and 9 used.
    16: [
        """CREATE TABLE work_items_new (
  id               TEXT PRIMARY KEY,
  bead_id          TEXT,
  title            TEXT NOT NULL,
  description      TEXT,
  repo             TEXT NOT NULL,
  chain_template   TEXT NOT NULL,
  chain_definition TEXT NOT NULL,
  current_node_id  TEXT,
  status           TEXT NOT NULL CHECK (status IN
                     ('active', 'needs_human', 'completed', 'paused', 'abandoned',
                      'rate_limited')),
  pending_steer_context TEXT,
  submodules       TEXT,
  root_merge_policy TEXT,
  attachments      TEXT,
  base_ref         TEXT,
  bead_cwd         TEXT,
  branch           TEXT,
  implements_beads TEXT,
  retry_at         TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
)""",
        """INSERT INTO work_items_new (id, bead_id, title, description, repo, chain_template,
  chain_definition, current_node_id, status, pending_steer_context, submodules,
  root_merge_policy, attachments, base_ref, bead_cwd, branch, implements_beads,
  created_at, updated_at)
SELECT id, bead_id, title, description, repo, chain_template, chain_definition,
       current_node_id, status, pending_steer_context, submodules, root_merge_policy,
       attachments, base_ref, bead_cwd, branch, implements_beads, created_at, updated_at
FROM work_items""",
        "DROP TABLE work_items",
        "ALTER TABLE work_items_new RENAME TO work_items",
        """CREATE TABLE worker_sessions_new (
  id             TEXT PRIMARY KEY,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  node_id        TEXT NOT NULL,
  hook_point     TEXT NOT NULL,
  pid            INTEGER,
  pid_start_time REAL,
  log_path       TEXT NOT NULL,
  result_path    TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN
                   ('pending', 'running', 'done', 'failed', 'capped_out', 'paused', 'unknown',
                    'done_with_concerns', 'needs_context', 'rate_limited')),
  attempt        INTEGER NOT NULL DEFAULT 1,
  session_summary_ref TEXT,
  created_at     TEXT NOT NULL,
  started_at     TEXT,
  round          INTEGER NOT NULL DEFAULT 0,
  model          TEXT,
  tokens_in      INTEGER,
  tokens_out     INTEGER,
  cost_usd       REAL,
  wall_ms        INTEGER,
  exited_at      TEXT
)""",
        """INSERT INTO worker_sessions_new (id, work_item_id, node_id, hook_point, pid,
  pid_start_time, log_path, result_path, status, attempt, session_summary_ref,
  created_at, started_at, round, model, tokens_in, tokens_out, cost_usd, wall_ms,
  exited_at)
SELECT id, work_item_id, node_id, hook_point, pid, pid_start_time, log_path,
       result_path, status, attempt, session_summary_ref, created_at, started_at,
       round, model, tokens_in, tokens_out, cost_usd, wall_ms, exited_at
FROM worker_sessions""",
        "DROP TABLE worker_sessions",
        "ALTER TABLE worker_sessions_new RENAME TO worker_sessions",
        "CREATE INDEX idx_worker_sessions_status ON worker_sessions(status)",
    ],
    # chain_template drops NOT NULL (Kraft-cd47): SQLite cannot alter a column
    # constraint, so work_items is rebuilt the same way migration 11 was. Every
    # existing row keeps its current value -- only new rows can write NULL.
    # Carries implements_beads (15) and retry_at (16) forward too, since this
    # rebuild runs after both and would otherwise drop them silently.
    17: [
        """CREATE TABLE work_items_new (
  id               TEXT PRIMARY KEY,
  bead_id          TEXT,
  title            TEXT NOT NULL,
  description      TEXT,
  repo             TEXT NOT NULL,
  chain_template   TEXT,
  chain_definition TEXT NOT NULL,
  current_node_id  TEXT,
  status           TEXT NOT NULL CHECK (status IN
                     ('active', 'needs_human', 'completed', 'paused', 'abandoned',
                      'rate_limited')),
  pending_steer_context TEXT,
  submodules       TEXT,
  root_merge_policy TEXT,
  attachments      TEXT,
  base_ref         TEXT,
  bead_cwd         TEXT,
  branch           TEXT,
  implements_beads TEXT,
  retry_at         TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
)""",
        """INSERT INTO work_items_new (id, bead_id, title, description, repo, chain_template,
  chain_definition, current_node_id, status, pending_steer_context, submodules,
  root_merge_policy, attachments, base_ref, bead_cwd, branch, implements_beads,
  retry_at, created_at, updated_at)
SELECT id, bead_id, title, description, repo, chain_template, chain_definition,
       current_node_id, status, pending_steer_context, submodules, root_merge_policy,
       attachments, base_ref, bead_cwd, branch, implements_beads, retry_at, created_at,
       updated_at
  FROM work_items""",
        "DROP TABLE work_items",
        "ALTER TABLE work_items_new RENAME TO work_items",
    ],
    18: [
        """CREATE TABLE work_item_repos (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  repo_path      TEXT NOT NULL,
  role           TEXT NOT NULL CHECK (role IN ('root', 'submodule')),
  submodule_path TEXT,
  merge_rank     INTEGER NOT NULL,
  bead_id        TEXT,
  mr_ref         TEXT,
  merge_state    TEXT NOT NULL DEFAULT 'pending' CHECK (merge_state IN
                   ('pending', 'open', 'merged', 'failed')),
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
)""",
        "CREATE INDEX idx_work_item_repos_item ON work_item_repos(work_item_id, merge_rank)",
    ],
    19: ["ALTER TABLE work_items ADD COLUMN escalation_session_id TEXT"],
    20: ["ALTER TABLE work_items ADD COLUMN auto_gate INTEGER NOT NULL DEFAULT 0"],
    21: ["ALTER TABLE work_items ADD COLUMN agent_overrides TEXT"],
    # 'config_error' has to join the status CHECK (a launch failure that could
    # not even start is not a test failure -- Kraft-579), and SQLite cannot
    # alter a constraint, so worker_sessions is rebuilt the documented way.
    # head_sha (Kraft-lu2) rides along in the same rebuild rather than paying
    # for a second one -- one more column, zero extra risk.
    22: [
        """CREATE TABLE worker_sessions_new (
  id             TEXT PRIMARY KEY,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  node_id        TEXT NOT NULL,
  hook_point     TEXT NOT NULL,
  pid            INTEGER,
  pid_start_time REAL,
  log_path       TEXT NOT NULL,
  result_path    TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN
                   ('pending', 'running', 'done', 'failed', 'capped_out', 'paused', 'unknown',
                    'done_with_concerns', 'needs_context', 'rate_limited', 'config_error')),
  attempt        INTEGER NOT NULL DEFAULT 1,
  session_summary_ref TEXT,
  created_at     TEXT NOT NULL,
  started_at     TEXT,
  round          INTEGER NOT NULL DEFAULT 0,
  model          TEXT,
  tokens_in      INTEGER,
  tokens_out     INTEGER,
  cost_usd       REAL,
  wall_ms        INTEGER,
  exited_at      TEXT,
  head_sha       TEXT
)""",
        """INSERT INTO worker_sessions_new (id, work_item_id, node_id, hook_point, pid,
  pid_start_time, log_path, result_path, status, attempt, session_summary_ref,
  created_at, started_at, round, model, tokens_in, tokens_out, cost_usd, wall_ms,
  exited_at)
SELECT id, work_item_id, node_id, hook_point, pid, pid_start_time, log_path,
       result_path, status, attempt, session_summary_ref, created_at, started_at,
       round, model, tokens_in, tokens_out, cost_usd, wall_ms, exited_at
FROM worker_sessions""",
        "DROP TABLE worker_sessions",
        "ALTER TABLE worker_sessions_new RENAME TO worker_sessions",
        "CREATE INDEX idx_worker_sessions_status ON worker_sessions(status)",
    ],
    # 'waiting' joins both CHECKs (Kraft-ru98): a ci_poll wait becomes a row the
    # scheduler owns rather than a coroutine blocking on `_wait_for_ci`. SQLite
    # cannot alter a constraint, so both tables are rebuilt the same 12-step way
    # migration 16 used, carrying every column added since forward (22's head_sha
    # and 'config_error' included).
    23: [
        """CREATE TABLE work_items_new (
  id               TEXT PRIMARY KEY,
  bead_id          TEXT,
  title            TEXT NOT NULL,
  description      TEXT,
  repo             TEXT NOT NULL,
  chain_template   TEXT,
  chain_definition TEXT NOT NULL,
  current_node_id  TEXT,
  status           TEXT NOT NULL CHECK (status IN
                     ('active', 'needs_human', 'completed', 'paused', 'abandoned',
                      'rate_limited', 'waiting')),
  pending_steer_context TEXT,
  submodules       TEXT,
  root_merge_policy TEXT,
  attachments      TEXT,
  base_ref         TEXT,
  bead_cwd         TEXT,
  branch           TEXT,
  implements_beads TEXT,
  retry_at         TEXT,
  escalation_session_id TEXT,
  auto_gate        INTEGER NOT NULL DEFAULT 0,
  agent_overrides  TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
)""",
        """INSERT INTO work_items_new (id, bead_id, title, description, repo, chain_template,
  chain_definition, current_node_id, status, pending_steer_context, submodules,
  root_merge_policy, attachments, base_ref, bead_cwd, branch, implements_beads,
  retry_at, escalation_session_id, auto_gate, agent_overrides, created_at, updated_at)
SELECT id, bead_id, title, description, repo, chain_template, chain_definition,
       current_node_id, status, pending_steer_context, submodules, root_merge_policy,
       attachments, base_ref, bead_cwd, branch, implements_beads, retry_at,
       escalation_session_id, auto_gate, agent_overrides, created_at, updated_at
  FROM work_items""",
        "DROP TABLE work_items",
        "ALTER TABLE work_items_new RENAME TO work_items",
        """CREATE TABLE worker_sessions_new (
  id             TEXT PRIMARY KEY,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  node_id        TEXT NOT NULL,
  hook_point     TEXT NOT NULL,
  pid            INTEGER,
  pid_start_time REAL,
  log_path       TEXT NOT NULL,
  result_path    TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN
                   ('pending', 'running', 'done', 'failed', 'capped_out', 'paused', 'unknown',
                    'done_with_concerns', 'needs_context', 'rate_limited', 'config_error',
                    'waiting')),
  attempt        INTEGER NOT NULL DEFAULT 1,
  session_summary_ref TEXT,
  created_at     TEXT NOT NULL,
  started_at     TEXT,
  round          INTEGER NOT NULL DEFAULT 0,
  model          TEXT,
  tokens_in      INTEGER,
  tokens_out     INTEGER,
  cost_usd       REAL,
  wall_ms        INTEGER,
  exited_at      TEXT,
  head_sha       TEXT
)""",
        """INSERT INTO worker_sessions_new (id, work_item_id, node_id, hook_point, pid,
  pid_start_time, log_path, result_path, status, attempt, session_summary_ref,
  created_at, started_at, round, model, tokens_in, tokens_out, cost_usd, wall_ms,
  exited_at, head_sha)
SELECT id, work_item_id, node_id, hook_point, pid, pid_start_time, log_path,
       result_path, status, attempt, session_summary_ref, created_at, started_at,
       round, model, tokens_in, tokens_out, cost_usd, wall_ms, exited_at, head_sha
FROM worker_sessions""",
        "DROP TABLE worker_sessions",
        "ALTER TABLE worker_sessions_new RENAME TO worker_sessions",
        "CREATE INDEX idx_worker_sessions_status ON worker_sessions(status)",
    ],
    # UI v2 · 04: per-item budget cap and per-node overrides. Both plain
    # additive columns -- no CHECK involved, so no rebuild.
    24: [
        "ALTER TABLE work_items ADD COLUMN budget_set INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE work_items ADD COLUMN budget_usd REAL",
        "ALTER TABLE work_items ADD COLUMN node_overrides TEXT",
    ],
    25: [
        "ALTER TABLE work_items ADD COLUMN archived_at TEXT",
        "ALTER TABLE work_items ADD COLUMN archived_by TEXT",
    ],
    # `ci_poll`'s honest verdicts (Kraft-cbr): a settled-green-but-confirmed-
    # unmergeable pipeline is 'conflict', not 'failed' (Task 2); a settled red
    # pipeline whose every failed job is the forge's own fault is 'infra'
    # while it is still retrying (Task 4), and 'infra_stop' once that retry
    # budget is spent. SQLite cannot alter a constraint, so worker_sessions is
    # rebuilt the same way migration 22/23 did.
    26: [
        """CREATE TABLE worker_sessions_new (
  id             TEXT PRIMARY KEY,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  node_id        TEXT NOT NULL,
  hook_point     TEXT NOT NULL,
  pid            INTEGER,
  pid_start_time REAL,
  log_path       TEXT NOT NULL,
  result_path    TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN
                   ('pending', 'running', 'done', 'failed', 'capped_out', 'paused', 'unknown',
                    'done_with_concerns', 'needs_context', 'rate_limited', 'config_error',
                    'waiting', 'conflict', 'infra', 'infra_stop')),
  attempt        INTEGER NOT NULL DEFAULT 1,
  session_summary_ref TEXT,
  created_at     TEXT NOT NULL,
  started_at     TEXT,
  round          INTEGER NOT NULL DEFAULT 0,
  model          TEXT,
  tokens_in      INTEGER,
  tokens_out     INTEGER,
  cost_usd       REAL,
  wall_ms        INTEGER,
  exited_at      TEXT,
  head_sha       TEXT
)""",
        """INSERT INTO worker_sessions_new (id, work_item_id, node_id, hook_point, pid,
  pid_start_time, log_path, result_path, status, attempt, session_summary_ref,
  created_at, started_at, round, model, tokens_in, tokens_out, cost_usd, wall_ms,
  exited_at, head_sha)
SELECT id, work_item_id, node_id, hook_point, pid, pid_start_time, log_path,
       result_path, status, attempt, session_summary_ref, created_at, started_at,
       round, model, tokens_in, tokens_out, cost_usd, wall_ms, exited_at, head_sha
FROM worker_sessions""",
        "DROP TABLE worker_sessions",
        "ALTER TABLE worker_sessions_new RENAME TO worker_sessions",
        "CREATE INDEX idx_worker_sessions_status ON worker_sessions(status)",
    ],
    # Pin `on.ci.poll` to the pipeline it already saw pending, instead of
    # re-resolving "latest on branch" every re-entry (Kraft-ivh1).
    27: ["ALTER TABLE work_items ADD COLUMN ci_pipeline_ref TEXT"],
    # Escalation worker sessions get a thread number (design:
    # ESCALATION_THREADS_SPEC.md, reproduced in the Kraft-dkb6g spec) --
    # every other hook_point's sessions stay implicitly thread 1.
    28: ["ALTER TABLE worker_sessions ADD COLUMN thread INTEGER NOT NULL DEFAULT 1"],
    # Every `model` in this table was written by `usage._model_of` reading the
    # FIRST `modelUsage` key, which is Claude Code's own haiku warm-up rather
    # than the model that did the work (Kraft-s7c04.15). NULL already means "no
    # model reported" to every reader, so the column is emptied rather than left
    # asserting something false: a wrong value that looks like a right one is
    # what made every cost-by-model reading of this table invalid without
    # anyone noticing. Rows written from here on are correct; a before/after
    # comparison across this line must exclude NULL, not average it in.
    29: ["UPDATE worker_sessions SET model = NULL"],
    30: ["ALTER TABLE worker_sessions ADD COLUMN command TEXT"],
    # The step group a node last began, so a CI wait and a fix-loop retry resume
    # at the group that stopped instead of at group zero.
    31: ["ALTER TABLE work_items ADD COLUMN current_step INTEGER NOT NULL DEFAULT 0"],
    # Template schema V1: the immutable materialized work-item input, and the
    # run-fork lineage Phase 5 fills.
    #
    # Additive, beside `chain_definition`, rather than a conversion of it. A V1
    # MaterializedChain has no `gate_after` and its gates are ordered nodes, so
    # there is no faithful mechanical translation of a legacy row: a half-run
    # item would resume against a chain whose node order differs from the one it
    # started on. V1 is declared incompatible
    # (`REQ template-v1-is-not-backward-compatible`) and the update path warns,
    # requires acceptance and backs up. So existing rows keep their legacy column
    # and stay readable by the legacy path until they finish or are cancelled.
    32: [
        "ALTER TABLE work_items ADD COLUMN materialized_chain TEXT",
        "ALTER TABLE work_items ADD COLUMN run_fork_parent TEXT",
    ],
    # The agent's progress event was renamed `task_progress` -> `plan_progress`
    # (it reports a plan task, and V1 made "task" a chain-task word). Every
    # reader -- the Timeline's grouping, the board's "Task N of M" -- matches
    # the new name only, so stored rows are renamed once here rather than each
    # reader learning both (Kraft-7hy7x).
    33: ["UPDATE events SET type = 'plan_progress' WHERE type = 'task_progress'"],
    # Run forks (`retry-creates-an-immutable-run-fork`). The fork's lineage lives
    # on its own row, so the column reserved for it on the work item goes, and
    # the item gains the current fork's materialization in its place.
    34: [
        "ALTER TABLE work_items DROP COLUMN run_fork_parent",
        "ALTER TABLE work_items ADD COLUMN run_chain TEXT",
        *(s.strip() for s in SCHEMA_SQL.split(";") if "CREATE TABLE run_forks" in s),
        "CREATE INDEX idx_run_forks_item ON run_forks(work_item_id)",
        *_RUN_FORK_TRIGGERS,
    ],
}

# Two branches picking the same migration key merges as a silent last-write-wins
# dict literal, not a git conflict -- nothing forces the numbers apart (Kraft-cd47
# collided with the `implements_beads` migration this way; git happened to flag it
# because both edits touched the same line, but a different line split wouldn't
# have). Catch a gap or a duplicate at import time instead of at some future
# upgrader's runtime KeyError.
assert sorted(_MIGRATIONS) == list(range(min(_MIGRATIONS), SCHEMA_VERSION)), (
    "_MIGRATIONS keys must be contiguous, one per version, up to SCHEMA_VERSION - 1"
)


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # PRAGMAs run as individual statements (not DML) so they don't open a txn.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version == SCHEMA_VERSION:
        return
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"database schema v{version} is newer than code v{SCHEMA_VERSION}")
    if version != 0:
        # Forward-only migration: apply each version step's statements, bump
        # user_version after each, all-or-nothing.
        #
        # Foreign keys go off for the duration: a step that rebuilds a table
        # (v4's work_items) drops and renames it, and `events.work_item_id`
        # references it. This is the documented 12-step rebuild, and the pragma
        # is a no-op inside a transaction, so it has to be set out here.
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN")
            for v in range(version, SCHEMA_VERSION):
                for stmt in _MIGRATIONS[v]:
                    conn.execute(stmt)
                conn.execute(f"PRAGMA user_version = {v + 1}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
        return
    # Explicit transaction: sqlite3 with isolation_level='' does NOT auto-open txns for DDL.
    # Must BEGIN explicitly to ensure all DDL + user_version bump commit atomically or not at all.
    try:
        conn.execute("BEGIN")
        # ponytail: naive ';' split — safe, the schema has no embedded semicolons
        for stmt in (s.strip() for s in SCHEMA_SQL.split(";")):
            if stmt:
                conn.execute(stmt)
        for stmt in _RUN_FORK_TRIGGERS:
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


class Database:
    def __init__(
        self,
        writer: sqlite3.Connection,
        reader: sqlite3.Connection,
        *,
        on_commit: Callable[[], None] | None = None,
    ) -> None:
        self._writer = writer
        self._reader = reader
        self._on_commit = on_commit
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def set_on_commit(self, cb: Callable[[], None] | None) -> None:
        self._on_commit = cb

    @classmethod
    async def open(
        cls, path: str | Path, *, on_commit: Callable[[], None] | None = None
    ) -> Database:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = _connect(path)
        migrate(writer)
        reader = _connect(path)
        self = cls(writer, reader, on_commit=on_commit)
        self._task = asyncio.create_task(self._run())
        return self

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                fn, fut = item
                try:
                    result = fn(self._writer)
                    self._writer.commit()
                except BaseException as exc:  # noqa: BLE001 - re-raised to caller
                    # Inform the caller FIRST: if rollback() itself raises, the
                    # exception escaping _run kills the writer task and wedges
                    # every pending/future write(). A failed rollback still
                    # propagates (dirty txn must not be silently committed by
                    # the next write), but only after the caller has its result.
                    if not fut.done():
                        fut.set_exception(exc)
                    self._writer.rollback()
                else:
                    if not fut.done():
                        fut.set_result(result)
                    if self._on_commit is not None:
                        try:
                            self._on_commit()
                        except Exception:
                            logger.exception("on_commit listener raised")
            finally:
                self._queue.task_done()

    async def write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        if self._task is None or self._task.done():
            raise RuntimeError("db writer is not running")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put((fn, fut))
        return await fut

    def read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        # SELECT-only work leaves no Python-level txn to roll back; the read
        # snapshot is actually released when CPython finalizes the temp cursor.
        # Keep this call anyway: harmless, and correct if a read fn does DML.
        self._reader.rollback()
        return fn(self._reader)

    async def close(self) -> None:
        await self._queue.put(_STOP)
        if self._task is not None:
            await self._task
        self._writer.close()
        self._reader.close()
