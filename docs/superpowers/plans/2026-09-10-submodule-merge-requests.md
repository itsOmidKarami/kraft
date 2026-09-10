# Submodule Merge Requests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kraft can open, poll, review and merge a merge request inside a submodule, so a work item whose deliverable lives in a submodule no longer finishes green having pushed nothing of it anywhere.

**Architecture:** A new `work_item_repos` table is the source of truth for which repos (root + submodules) a work item touches and each one's merge state. `ensure_worktree` writes a row per declared submodule and checks the item's branch out inside it; a new post-implementation scan adds a row for any submodule the agent touched but nobody declared. `forge.run_task` becomes a loop over those rows (deepest submodule first, root last) instead of a single `repo: Path`; a single-repo item has no rows and the loop runs exactly once, unchanged from today. `_assert_clean` is taught to see a submodule's own dirty state instead of trusting the repo's `ignore` config.

**Tech Stack:** Python (FastAPI backend, sqlite3, asyncio subprocess), TypeScript/React (frontend), pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-09-10-submodule-merge-requests-design.md`

## Global Constraints

- Never run `git submodule update --init` with no path arguments — only the declared or discovered paths, per design §3 step 2. A blanket init on a workspace with submodules nobody asked for is its own outage.
- A single-repo work item (no `work_item_repos` rows) must be byte-for-byte unaffected by every change here — same log strings, same session count, same forge calls. Every task's tests assert this explicitly, not just the multi-repo path.
- Depends on Kraft-cppp / `Kraft-mxdx` (commit identity pinning in `ensure_worktree`) landing first — `_pin_identity(repo: Path, worktree: Path, work_item_id: str) -> None` in `src/kraft/builtins.py` is assumed to exist and is reused here, not re-implemented.
- No new dependency. Everything here is `git`, `sqlite3`, and code already in the repo.

---

## Design correction made while planning (read before Task 7)

The spec's "root policy" section assumed a submodule's post-merge SHA would be known by the time root's pointer-bump commit needs to exist. Tracing the actual dispatch (`executor._dispatch`'s `kind == "forge"` branch, `src/kraft/executor.py:539`) shows every chain node — `open_mr`, `mr_checks`, `merge` — fires **once for the whole item**; a per-repo loop lives *inside* one node call, not across separate passes. So at `open_mr` time no submodule has merged yet, and root's own MR (if `bump` opened one) would have to review a pointer that doesn't exist.

Resolved directly with the user rather than guessed: **root never opens a merge request over a pointer bump alone.** If root has no commits of its own (`forge._commits_on(root, branch)` is empty — true for every `bump_no_mr`/`skip` item, and for `bump` whenever root's own tree is untouched), it never goes through `open_mr`/`mr_checks`/`merge` at all. The pointer-bump commit, when one is needed, is created and pushed directly to root's default branch after every submodule row has actually merged (Task 7) — no MR, so the post-merge SHA it needs is genuinely available by then. Root only goes through the ordinary per-repo forge loop when it has real changes of its own, in which case (`bump` only) the pointer is added to that MR using the submodule's pre-merge branch SHA — the one SHA that exists at that point. This is a real, named limitation for the `gh` backend, which merges with `--squash` by default (`GhCli.merge`, `forge.py:648`) and can make a pre-merge SHA unreachable from the target branch afterward; fixing that for real is a two-pass chain restructure, out of scope for this item.

---

### Task 1: `work_item_repos` table + store functions

**Files:**
- Modify: `src/kraft/db.py` (schema, migration)
- Modify: `src/kraft/store.py` (CRUD + rewritten `repos_for`)
- Modify: `src/kraft/api.py:989` (call site)
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `store.add_repo(conn, *, work_item_id, repo_path, role, merge_rank, submodule_path=None, bead_id=None) -> int` (returns the new row's id)
- Produces: `store.repos_for(conn, work_item_id) -> list[dict]` (keys: `repo`, `path`, `role`, `merge_rank`, `state`, `mr_ref`) — **signature change**: was `repos_for(row, merged_nodes)` reading the `work_items.submodules` JSON column; now reads the `work_item_repos` table. Every other task in this plan reads/writes that table through `add_repo`/`update_repo_state`, never the JSON column directly.
- Produces: `store.update_repo_state(conn, repo_row_id, *, merge_state, mr_ref=None) -> None`
- Produces: `store.merge_rank_order(paths: list[str]) -> list[str]` (deepest path first, root last — the same sort `repos_for` used to do inline, now a named function so Task 4 and Task 5 don't each reimplement it)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store.py — near the existing repos_for tests, or a new section
import sqlite3

from kraft import db, store


def _conn(tmp_path) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.migrate(c)
    c.execute(
        "INSERT INTO work_items (id, title, repo, chain_definition, status, created_at, updated_at) "
        "VALUES ('w1', 't', '/r', '{}', 'active', '2026-01-01', '2026-01-01')"
    )
    return c


def test_merge_rank_order_puts_the_deepest_path_first():
    assert store.merge_rank_order(["libs/a", "vendor/deep/b", "x"]) == [
        "vendor/deep/b",
        "libs/a",
        "x",
    ]


def test_add_repo_and_repos_for_round_trip(tmp_path):
    conn = _conn(tmp_path)
    sub_id = store.add_repo(
        conn, work_item_id="w1", repo_path="/wt/repos/pkg", role="submodule",
        submodule_path="repos/pkg", merge_rank=1,
    )
    store.add_repo(conn, work_item_id="w1", repo_path="/wt", role="root", merge_rank=2)

    repos = store.repos_for(conn, "w1")

    assert [r["role"] for r in repos] == ["submodule", "root"]
    assert repos[0]["path"] == "/wt/repos/pkg"
    assert repos[0]["state"] == "pending"
    assert repos[0]["mr_ref"] is None

    store.update_repo_state(conn, sub_id, merge_state="merged", mr_ref={"number": 3, "url": "http://x/3"})
    repos = store.repos_for(conn, "w1")
    assert repos[0]["state"] == "merged"
    assert repos[0]["mr_ref"] == {"number": 3, "url": "http://x/3"}


def test_repos_for_is_empty_for_a_single_repo_item(tmp_path):
    conn = _conn(tmp_path)
    assert store.repos_for(conn, "w1") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_store.py -k "merge_rank_order or add_repo or repos_for_is_empty"`
Expected: FAIL — `store.add_repo` / `store.merge_rank_order` do not exist yet, and the current `repos_for(row, merged_nodes)` has the wrong signature.

- [ ] **Step 3: Schema — `src/kraft/db.py`**

Bump the version and add the table to `SCHEMA_SQL` (for a fresh database) and as migration `18` (for an existing one):

```python
SCHEMA_VERSION = 19
```

Add to `SCHEMA_SQL`, after the `retry_counters` table:

```sql
CREATE TABLE work_item_repos (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  -- absolute path: the worktree root for role='root', worktree/submodule_path
  -- for role='submodule'
  repo_path      TEXT NOT NULL,
  role           TEXT NOT NULL CHECK (role IN ('root', 'submodule')),
  -- repo-relative path under the worktree; NULL for role='root'
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
```

Add to `_MIGRATIONS`:

```python
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
```

The existing `assert sorted(_MIGRATIONS) == list(range(min(_MIGRATIONS), SCHEMA_VERSION))` at the bottom of `db.py` catches a gap or a duplicate at import time — no new test needed for that, it already runs on every import.

- [ ] **Step 4: Store — `src/kraft/store.py`**

Add near `ROOT_MERGE_POLICIES`, replacing the existing `repos_for`:

```python
def merge_rank_order(paths: list[str]) -> list[str]:
    """Deepest path first, root last (design 3a): a submodule must merge
    before the parent whose pointer names it."""
    return sorted(paths, key=lambda p: (-p.count("/"), p))


def add_repo(
    conn: sqlite3.Connection,
    *,
    work_item_id: str,
    repo_path: str,
    role: str,
    merge_rank: int,
    submodule_path: str | None = None,
    bead_id: str | None = None,
) -> int:
    """Register one repo (root or submodule) a work item will merge into.

    Callers compute `merge_rank` -- `ensure_worktree` for declared submodules,
    the §3a scan for ones it discovers -- because only they know the whole set
    at the moment they write a row.
    """
    now = _now()
    cur = conn.execute(
        "INSERT INTO work_item_repos (work_item_id, repo_path, role, submodule_path, "
        "merge_rank, bead_id, merge_state, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (work_item_id, repo_path, role, submodule_path, merge_rank, bead_id, now, now),
    )
    return cur.lastrowid


def repos_for(conn: sqlite3.Connection, work_item_id: str) -> list[dict]:
    """The item's repos, deepest submodule first, root last. Empty on a
    single-repo item -- no `work_item_repos` rows were ever written for it.
    """
    rows = conn.execute(
        "SELECT * FROM work_item_repos WHERE work_item_id = ? ORDER BY merge_rank",
        (work_item_id,),
    ).fetchall()
    return [
        {
            "repo": Path(r["repo_path"]).name,
            "path": r["repo_path"],
            "role": r["role"],
            "merge_rank": r["merge_rank"],
            "state": r["merge_state"],
            "mr_ref": json.loads(r["mr_ref"]) if r["mr_ref"] else None,
        }
        for r in rows
    ]


def update_repo_state(
    conn: sqlite3.Connection, repo_row_id: int, *, merge_state: str, mr_ref: dict | None = None
) -> None:
    """Record what a forge call just learned about one repo's merge request."""
    conn.execute(
        "UPDATE work_item_repos SET merge_state = ?, mr_ref = ?, updated_at = ? WHERE id = ?",
        (merge_state, json.dumps(mr_ref) if mr_ref else None, _now(), repo_row_id),
    )
```

Remove the old `repos_for(row, merged_nodes)` function entirely — it is fully replaced.

Note the accepted behavior change: the old `repos_for` derived a preview straight from the `submodules` JSON column declared at intake, so the UI's repos panel showed rows immediately on item creation. `work_item_repos` rows are only written once `ensure_worktree` (Task 4) runs, so the panel is empty for the short span between intake and that node completing. This is a deliberate trade-off, not an oversight — restoring an intake-time preview is a separate, small follow-up if anyone wants it back, and isn't this item's bug.

- [ ] **Step 5: Update the one call site — `src/kraft/api.py:989`**

```python
        "repos": st.db.read(lambda c: store.repos_for(c, wid)),
```

(was `store.repos_for(row, _completed_nodes(st, wid))` — `_completed_nodes` may now be unused at that call site; leave it if `get_work_item` uses it elsewhere, e.g. for `stop_reason`).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_store.py tests/test_db.py`
Expected: PASS, including the pre-existing `test_migrations_keys_are_contiguous` and `test_migrate_creates_schema_from_empty`.

- [ ] **Step 7: Commit**

```bash
git add src/kraft/db.py src/kraft/store.py src/kraft/api.py tests/test_store.py
git commit -m "feat: work_item_repos table, store CRUD, repos_for reads it"
```

---

### Task 2: Frontend — repos panel shows real merge states

**Files:**
- Modify: `frontend/src/views/WorkItemDetail.tsx`
- Test: `frontend/src/views/WorkItemDetail.test.tsx`

**Interfaces:**
- Consumes: `RepoRow.state` (`frontend/src/types.ts:426`, already `string`, no type change needed) — now one of `"pending" | "open" | "merged" | "failed"` from Task 1's `store.repos_for`, instead of only ever `"pending"` or `"merged"`.

**Context:** today's rendering (`WorkItemDetail.tsx` around line 439/449) maps every non-`"merged"` state to the `"pending"` glyph. A `"failed"` repo would render as merely pending — worse than before in one specific way, since the panel now exists to tell the truth. Fix the mapping before it ships a new state vocabulary the UI can't show.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/views/WorkItemDetail.test.tsx — next to the existing repos panel test
it("shows a failed repo as failed, not pending", () => {
  setup({
    root_merge_policy: "bump",
    repos: [
      { repo: "b", path: "repos/pkg", role: "submodule", merge_rank: 1, state: "failed" },
      { repo: "r", path: "/r", role: "root", merge_rank: 2, state: "pending" },
    ],
  });
  renderDetail();
  const row = document.querySelector('[data-repo="repos/pkg"]');
  expect(row?.textContent).toContain("failed");
  expect(row?.querySelector(".status-failed, [data-status='failed']")).toBeTruthy();
});
```

(Adjust the failed-glyph selector to whatever `StatusGlyph`/`RowState` actually render for `status="failed"` — check `frontend/src/components/ui.tsx` for the class/attribute it sets before finalizing the assertion.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix frontend test -- WorkItemDetail -t "shows a failed repo"`
Expected: FAIL — the row currently renders `status="pending"` for anything but `"merged"`.

- [ ] **Step 3: Fix the mapping**

In `frontend/src/views/WorkItemDetail.tsx`, add near the top of the component file (or just above the repos panel section):

```tsx
function repoGlyphStatus(state: string): SessionStatus {
  if (state === "merged") return "done";
  if (state === "failed") return "failed";
  if (state === "open") return "running";
  return "pending";
}
```

Replace both occurrences in the repos panel:

```tsx
<StatusGlyph status={repoGlyphStatus(r.state)} />
...
<RowState status={repoGlyphStatus(r.state)}>{r.state}</RowState>
```

(`SessionStatus` is already imported from `../types` in this file for the chain bar / sessions list — reuse that import, don't add a second one.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --prefix frontend test -- WorkItemDetail`
Expected: PASS, including the two pre-existing repos-panel tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/WorkItemDetail.tsx frontend/src/views/WorkItemDetail.test.tsx
git commit -m "fix: repos panel shows failed/open repo state, not just pending/merged"
```

---

### Task 3: `_assert_clean` sees a submodule regardless of its `ignore` config

**Files:**
- Modify: `src/kraft/adapters/forge.py:196`
- Test: `tests/test_forge.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_forge.py — near test_open_mr_refuses_a_dirty_worktree
def test_assert_clean_sees_a_submodule_with_ignore_all(tmp_path):
    """`submodule.<path>.ignore = all` is a legitimate thing for a human to
    set on a six-submodule workspace -- it must not blind Kraft's own guard
    to a submodule commit that never left the worktree (the real failure on
    work item 9d0ab38ff3c9439b90506df0f6966660)."""
    root = tmp_path / "root"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("root\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

    sub = tmp_path / "sub"
    sub.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=sub, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=sub, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=sub, check=True)
    (sub / "f.txt").write_text("1\n")
    subprocess.run(["git", "add", "-A"], cwd=sub, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=sub, check=True)

    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(sub), "pkg"],
        cwd=root, check=True,
    )
    subprocess.run(["git", "config", "submodule.pkg.ignore", "all"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add submodule"], cwd=root, check=True)

    # New commit inside the submodule, root pointer left untouched -- exactly
    # what "do not bump the workspace submodule pointer" produces.
    (root / "pkg" / "f.txt").write_text("2\n")
    subprocess.run(["git", "add", "-A"], cwd=root / "pkg", check=True)
    subprocess.run(["git", "commit", "-q", "-m", "metric change"], cwd=root / "pkg", check=True)

    with pytest.raises(forge.ForgeError, match="pkg"):
        asyncio.run(forge._assert_clean(root))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/test_forge.py -k test_assert_clean_sees_a_submodule_with_ignore_all`
Expected: FAIL — today's plain `git status --porcelain` respects `ignore = all` and reports nothing dirty, so no `ForgeError` is raised.

- [ ] **Step 3: Fix `_assert_clean`**

```python
async def _assert_clean(repo: Path) -> None:
    """Refuse to open a merge request over a worktree with uncommitted work.

    Untracked files are included on purpose: a source or test file the agent
    never `git add`ed is what went missing on work item 5163dd1b. Ignored files
    are excluded by git itself, so `.pytest_cache/` does not trip it, and
    `_WORK_PRODUCT` drops Kraft's own session notes in a repo that has not
    ignored them.

    `--ignore-submodules=none` deliberately overrides the repo's own
    `submodule.<path>.ignore` config. A human setting `ignore = all` on a
    workspace with several submodules to stop pointer churn in every `git
    status` is reasonable; it is not permission for Kraft to open a merge
    request over a submodule holding commits that request will not carry
    (Kraft-qlsf — this is what let work item 9d0ab38ff3c9439b90506df0f6966660
    push a submodule commit nowhere while every guard reported clean).
    """
    raw = await _run(
        repo, ["git", "status", "--porcelain", "--ignore-submodules=none", "--", *_WORK_PRODUCT]
    )
    dirty = [line[3:] for line in raw.splitlines() if line.strip()]
    if dirty:
        shown = ", ".join(dirty[:5])
        more = f" (+{len(dirty) - 5} more)" if len(dirty) > 5 else ""
        raise ForgeError(
            f"{len(dirty)} uncommitted path(s) in the worktree, which would not "
            f"reach the merge request: {shown}{more}"
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_forge.py`
Expected: PASS, including the pre-existing dirty-worktree tests (a plain source-file change is still caught the same way).

- [ ] **Step 5: Commit**

```bash
git add src/kraft/adapters/forge.py tests/test_forge.py
git commit -m "fix: _assert_clean sees a submodule even under ignore=all"
```

---

### Task 4: `ensure_worktree` sets up declared submodules

**Files:**
- Modify: `tests/support/harness.py` (new fixture helper)
- Modify: `src/kraft/builtins.py`
- Test: `tests/test_builtins.py`

**Interfaces:**
- Consumes: `store.merge_rank_order` (Task 1), `store.add_repo` (Task 1), `_pin_identity(repo: Path, worktree: Path, work_item_id: str) -> None` (Kraft-cppp, already landed)
- Produces: `_setup_submodules(db, repo: Path, worktree: Path, branch: str, work_item_id: str, paths: list[str]) -> None`, called from `ensure_worktree`
- Produces: `make_repo_with_submodule(tmp_path, *, submodule_path="repos/pkg") -> tuple[Path, Path]` in `tests/support/harness.py` — returns `(root_repo, submodule_repo)`, both real git repos on `main`, the submodule already added and committed into the root at `submodule_path`. Every later task's tests build on this instead of hand-rolling `git submodule add` again.

- [ ] **Step 1: Build the fixture helper**

```python
# tests/support/harness.py — near make_repo
def make_repo_with_submodule(tmp_path: Path, *, submodule_path: str = "repos/pkg") -> tuple[Path, Path]:
    """A root repo with one real submodule already added and committed.

    Every test in the submodule-merge-requests plan that needs "a superproject
    plus a submodule" builds on this rather than repeating `git submodule add`
    -- the shape that broke on work item 9d0ab38ff3c9439b90506df0f6966660.
    """
    sub = make_repo(tmp_path, name="pkg")
    root = make_repo(tmp_path, name="ws")
    _git(
        root, "-c", "protocol.file.allow=always", "submodule", "add", str(sub), submodule_path
    )
    _git(root, "commit", "-m", "add submodule")
    return root, sub
```

(`_git` already exists in this file and sets `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` for reproducible SHAs — reuse it, don't call `subprocess.run` directly here.)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_builtins.py
from support.harness import make_repo_with_submodule


def test_ensure_worktree_checks_out_a_declared_submodule_on_the_items_branch(tmp_path):
    root, _sub = make_repo_with_submodule(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c, id="w1", bead_id="B", title="t", repo=str(root),
                    chain_template="default", chain_definition="{}",
                    submodules=["repos/pkg"], root_merge_policy="bump_no_mr",
                )
            )
            worktree = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(root), work_item_id="w1",
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = 'w1'").fetchone()
            )
            branch = store.branch_for(row)
            sub_path = worktree / "repos" / "pkg"
            current = subprocess.run(
                ["git", "branch", "--show-current"], cwd=sub_path,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            repos = database.read(lambda c: store.repos_for(c, "w1"))
            return branch, current, repos
        finally:
            await database.close()

    branch, current, repos = asyncio.run(scenario())

    assert current == branch
    assert [r["role"] for r in repos] == ["submodule", "root"]
    assert repos[0]["path"].endswith("repos/pkg")


def test_ensure_worktree_never_runs_a_blanket_submodule_init(tmp_path, monkeypatch):
    """Global constraint: only declared paths, never every submodule in
    .gitmodules (design §3 step 2)."""
    root, _sub = make_repo_with_submodule(tmp_path)
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(args, **kw):
        if args[:3] == ["git", "submodule", "update"]:
            calls.append(args)
        return real_run(args, **kw)

    monkeypatch.setattr(kraft_builtins.subprocess, "run", spy)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c, id="w1", bead_id="B", title="t", repo=str(root),
                    chain_template="default", chain_definition="{}",
                    submodules=["repos/pkg"], root_merge_policy="bump_no_mr",
                )
            )
            await kraft_builtins.ensure_worktree(database, rd, repo=str(root), work_item_id="w1")
        finally:
            await database.close()

    asyncio.run(scenario())
    assert calls == [["git", "submodule", "update", "--init", "--", "repos/pkg"]]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_builtins.py -k declared_submodule or blanket_submodule_init`
Expected: FAIL — `ensure_worktree` today never reads `submodules` at all.

- [ ] **Step 4: Implement `_setup_submodules` and wire it into `ensure_worktree`**

In `src/kraft/builtins.py`, add near `_copy_attachments`:

```python
async def _setup_submodules(
    db, repo: Path, worktree: Path, branch: str, work_item_id: str, paths: list[str]
) -> None:
    """`git submodule update --init` only the declared paths (design 3 step 2
    -- never blanket), check the item's branch out inside each one, and write
    one `work_item_repos` row per repo -- deepest submodule first, root last
    (3a) -- so the forge nodes later know what to open a merge request
    against and in what order.

    A submodule is a regular working copy once initialized, not a bare repo,
    so getting the item's branch into it is a plain `checkout`/`checkout -b`
    -- design 06's "git worktree add inside each submodule" is shorthand for
    "this submodule ends up on the item's branch", not a second linked
    worktree, which a submodule path does not support the way the root does.
    """
    ordered = store.merge_rank_order(paths)
    init = await asyncio.to_thread(
        subprocess.run,
        ["git", "submodule", "update", "--init", "--", *ordered],
        cwd=str(worktree),
        capture_output=True,
        text=True,
    )
    if init.returncode != 0:
        detail = init.stderr.strip() or init.stdout.strip()
        raise RuntimeError(f"git submodule update --init failed for {work_item_id}: {detail}")
    for rank, rel in enumerate(ordered, start=1):
        sub = worktree / rel
        exists = git_read(
            sub, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", expected_failure=True
        )
        checkout = await asyncio.to_thread(
            subprocess.run,
            ["git", "checkout", branch] if exists else ["git", "checkout", "-b", branch],
            cwd=str(sub),
            capture_output=True,
            text=True,
        )
        if checkout.returncode != 0:
            detail = checkout.stderr.strip() or checkout.stdout.strip()
            raise RuntimeError(
                f"checkout of {branch!r} failed in submodule {rel} for {work_item_id}: {detail}"
            )
        _pin_identity(repo, sub, work_item_id)
        await db.write(
            lambda c, p=str(sub), r=rel, rk=rank: store.add_repo(
                c, work_item_id=work_item_id, repo_path=p, role="submodule",
                submodule_path=r, merge_rank=rk,
            )
        )
    await db.write(
        lambda c, p=str(worktree), rk=len(ordered) + 1: store.add_repo(
            c, work_item_id=work_item_id, repo_path=p, role="root", merge_rank=rk,
        )
    )
```

In `ensure_worktree`, right before `return worktree` (after the `uv sync` block):

```python
    decl = db.read(
        lambda c: c.execute(
            "SELECT submodules FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    submodules = json.loads(decl["submodules"]) if decl and decl["submodules"] else []
    if submodules:
        await _setup_submodules(db, Path(repo), worktree, branch, work_item_id, submodules)
    return worktree
```

(`branch` is already a local variable in `ensure_worktree`, computed earlier via `store.branch_for(row)`.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_builtins.py`
Expected: PASS, including every pre-existing `ensure_worktree` test (none of them declare `submodules`, so `_setup_submodules` never runs for them).

- [ ] **Step 6: Commit**

```bash
git add tests/support/harness.py src/kraft/builtins.py tests/test_builtins.py
git commit -m "feat: ensure_worktree checks out declared submodules and records work_item_repos rows"
```

---

### Task 5: §3a scan — catch a submodule nobody declared

**Files:**
- Modify: `src/kraft/builtins.py`
- Modify: `templates/default.yaml`, `templates/registry.yaml`
- Test: `tests/test_builtins.py`

**Interfaces:**
- Produces: `scan_submodules(db, run_dirs, *, session_id, work_item_id, node_id, hook_point, round, repo, worktree) -> str` (same builtin-task shape as `env_setup`/`noop`)
- New task hook `on.repos.scan`, `kind: builtin`, `handler: scan_submodules`, appended to the `implementation` node's `tasks` in `default.yaml`, right after `on.implementation.start`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_builtins.py
def test_scan_submodules_finds_a_submodule_the_agent_touched_but_nobody_declared(tmp_path):
    root, _sub = make_repo_with_submodule(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c, id="w1", bead_id="B", title="t", repo=str(root),
                    chain_template="default", chain_definition="{}",
                )  # no submodules declared -- exactly the real item's shape
            )
            worktree = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(root), work_item_id="w1"
            )
            # the agent's own half, done correctly: init + branch + commit
            # inside the submodule, root pointer never touched
            subprocess.run(
                ["git", "submodule", "update", "--init", "--", "repos/pkg"],
                cwd=worktree, check=True,
            )
            sub = worktree / "repos" / "pkg"
            subprocess.run(["git", "checkout", "-b", "agent-work"], cwd=sub, check=True)
            (sub / "new.txt").write_text("metric\n")
            subprocess.run(["git", "add", "-A"], cwd=sub, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "m"],
                cwd=sub, check=True,
            )

            await kraft_builtins.scan_submodules(
                database, rd, session_id="s1", work_item_id="w1", node_id="implementation",
                hook_point="on.repos.scan", round=0, repo=str(root), worktree=str(worktree),
            )
            return database.read(lambda c: store.repos_for(c, "w1"))
        finally:
            await database.close()

    repos = asyncio.run(scenario())
    assert len(repos) == 1
    assert repos[0]["role"] == "submodule"
    assert repos[0]["path"].endswith("repos/pkg")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/test_builtins.py -k scan_submodules_finds`
Expected: FAIL — `kraft_builtins.scan_submodules` does not exist yet.

- [ ] **Step 3: Implement `scan_submodules`**

In `src/kraft/builtins.py`:

```python
async def scan_submodules(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    round: int,
    repo: str,
    worktree: str,
) -> str:
    """Design 3a: catch a submodule the agent touched but the item never
    declared, give it a `work_item_repos` row and a pinned identity, before
    `open_mr` would otherwise have to refuse over it.

    Runs right after `on.implementation.start` in the same node (see
    `default.yaml`), so a plain green `verify` never masks a change `open_mr`
    would reject three nodes later -- this is what would have rescued work
    item 9d0ab38ff3c9439b90506df0f6966660, which declared nothing.
    """
    log_path, result_path = await start_session(
        db, run_dirs, session_id=session_id, work_item_id=work_item_id,
        node_id=node_id, hook_point=hook_point, round=round,
    )
    wt = Path(worktree)
    known = {
        r["submodule_path"]
        for r in db.read(
            lambda c: c.execute(
                "SELECT submodule_path FROM work_item_repos "
                "WHERE work_item_id = ? AND role = 'submodule'",
                (work_item_id,),
            ).fetchall()
        )
        if r["submodule_path"]
    }
    # '+' means the submodule's checked-out commit no longer matches what the
    # superproject's index records -- new commits sitting in the worktree,
    # exactly the shape that went unreported before this plan.
    raw = git_read(wt, "submodule", "status", expected_failure=True) or ""
    touched = set()
    for line in raw.splitlines():
        if not line or line[0] != "+":
            continue
        parts = line[1:].split()
        if len(parts) >= 2:
            touched.add(parts[1])

    undeclared = sorted(touched - known)
    if not undeclared:
        return await finish_session(
            db, log_path, result_path, session_id=session_id, status="done",
            log="no undeclared submodule changes\n",
        )

    existing = db.read(
        lambda c: c.execute(
            "SELECT COUNT(*) AS n FROM work_item_repos WHERE work_item_id = ?", (work_item_id,)
        ).fetchone()
    )
    next_rank = existing["n"] + 1
    log = ""
    for rel in undeclared:
        sub = wt / rel
        _pin_identity(Path(repo), sub, work_item_id)
        await db.write(
            lambda c, p=str(sub), r=rel, rk=next_rank: store.add_repo(
                c, work_item_id=work_item_id, repo_path=p, role="submodule",
                submodule_path=r, merge_rank=rk,
            )
        )
        log += f"found undeclared submodule change: {rel}\n"
        next_rank += 1
    # The root row (written by ensure_worktree, if this item declared any
    # submodule at all) now has to merge after these too.
    await db.write(
        lambda c, rk=next_rank: c.execute(
            "UPDATE work_item_repos SET merge_rank = ? WHERE work_item_id = ? AND role = 'root'",
            (rk, work_item_id),
        )
    )
    return await finish_session(
        db, log_path, result_path, session_id=session_id, status="done", log=log
    )
```

- [ ] **Step 4: Wire the new task hook**

`templates/registry.yaml`, near `on.env.prepare`:

```yaml
  on.repos.scan:              { kind: builtin, handler: scan_submodules }
```

`templates/default.yaml`, the `implementation` node:

```yaml
  - id: implementation
    tasks: [on.implementation.start, on.repos.scan]
    gate_after: null
```

Also add a matching branch in `executor._dispatch` next to the existing `env_setup`/`noop` builtin branches:

```python
    if kind == "builtin" and binding.get("handler") == "scan_submodules":
        return await _builtins.scan_submodules(
            db, run_dirs, repo=work_item_row["repo"], worktree=str(worktree), **common
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_builtins.py tests/test_forge.py`
Expected: PASS. Also confirm the template still validates:

Run: `uv run pytest -q tests/test_templates.py` (or wherever `registry.yaml`/`default.yaml` get schema-checked — grep for `_FORGE_BACKENDS`/`Registry` tests if the file name differs)
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/builtins.py src/kraft/executor.py templates/default.yaml templates/registry.yaml tests/test_builtins.py
git commit -m "feat: §3a scan catches an undeclared submodule change after implementation"
```

---

### Task 6: `forge.run_task` becomes multi-repo

**Files:**
- Modify: `src/kraft/adapters/forge.py`
- Test: `tests/test_forge.py`

**Interfaces:**
- Consumes: `work_item_repos` rows (Task 1)
- Produces: `_run_one(forge, db, *, repo, branch, title, work_item_id, handler, hook_point, poll_timeout, poll_interval, merge_timeout, merge_interval) -> tuple[str, str]` — the existing per-handler `match` block, extracted unchanged
- Produces: `_assert_submodules_covered(repo: Path, covered: set[Path]) -> None`
- `run_task`'s outer signature is unchanged; its behavior for an item with no `work_item_repos` rows is unchanged (same log strings, same single `finish_session` call) — every existing test in `tests/test_forge.py` is the regression suite for that claim and must keep passing untouched.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_forge.py
def test_run_task_opens_a_merge_request_per_repo_deepest_first(tmp_path, monkeypatch):
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    root, _sub = make_repo_with_submodule(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="t", repo=str(root),
                template=_back_half_template(), bd_cwd=str(tracker),
                submodules=["repos/pkg"], root_merge_policy="skip",
            )
            worktree = await _builtins.ensure_worktree(database, rd, repo=str(root), work_item_id=wid)
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            branch = store.branch_for(row)
            status = await forge.run_task(
                database, rd, session_id="s1", work_item_id=wid, node_id="open_mr",
                hook_point="on.mr.open", handler="open_mr", backend="fake",
                repo=worktree, branch=branch, title="t",
            )
            repos = database.read(lambda c: store.repos_for(c, wid))
            return status, repos
        finally:
            await database.close()

    status, repos = asyncio.run(scenario())
    assert status == "done"
    assert len(fake.opened) == 2  # submodule, then root
    assert [r["role"] for r in repos] == ["submodule", "root"]
    assert repos[0]["state"] == "open"


def test_run_task_is_unchanged_for_a_single_repo_item(tmp_path, monkeypatch):
    """The regression this whole task is not allowed to cause."""
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge, "resolve", lambda name: fake)
    repo = make_repo(tmp_path)

    status = asyncio.run(
        forge.run_task(
            _FakeDb(), RunDirs(tmp_path / "run").ensure(), session_id="s1", work_item_id="w1",
            node_id="open_mr", hook_point="on.mr.open", handler="open_mr", backend="fake",
            repo=repo, branch="kraft/w1", title="t",
        )
    )
    assert status == "done"
    assert len(fake.opened) == 1
```

(`_FakeDb` — check whether `tests/test_forge.py` already has an in-memory `db.Database`-like test double for cases that don't need `executor.intake`; if not, use the real `db.Database.open` against `tmp_path/"run.db"` the way `_run_back_half` does, wrapped in the same `scenario()`/`asyncio.run` pattern as the first test.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_forge.py -k "deepest_first or unchanged_for_a_single_repo"`
Expected: FAIL — `run_task` today takes one `repo: Path` and never looks at `work_item_repos`.

- [ ] **Step 3: Extract `_run_one`**

In `src/kraft/adapters/forge.py`, replace the body of `run_task`'s `try` block (the `match handler:` and everything inside it, `forge.py:843` through the `case _:` line) with a call to a new function, and move that exact code into it unchanged:

```python
async def _run_one(
    forge: Forge,
    db,
    *,
    repo: Path,
    branch: str,
    title: str,
    work_item_id: str,
    handler: str,
    hook_point: str,
    poll_timeout: float,
    poll_interval: float,
    merge_timeout: float,
    merge_interval: float,
) -> tuple[str, str]:
    """One forge handler against one repo. Extracted from `run_task` so the
    multi-repo loop there can call it once per `work_item_repos` row; a
    single-repo item's behaviour is unchanged -- one call, same log and status
    strings as before this split.
    """
    body = mr_body(work_item_id, branch, await _commits_on(repo, branch))
    match handler:
        case "open_mr":
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "open":
                await forge.push(repo=repo, branch=branch)
                await forge.update_mr(repo=repo, branch=branch, body=body)
                number, url = existing.number, existing.url
                log, status = f"reusing !{number}: {url}\n", "done"
            else:
                mr = await forge.open_mr(repo=repo, branch=branch, title=title, body=body)
                number, url = mr.number, mr.url
                log, status = f"opened {url}\n", "done"
            await db.write(
                lambda c, n=number, u=url: events.append(
                    c, work_item_id, "mr_opened", {"number": n, "url": u}
                )
            )
        case "ci_poll":
            await forge.push(repo=repo, branch=branch)
            ci, timed_out = await _poll_ci(
                forge, repo=repo, branch=branch, timeout=poll_timeout, interval=poll_interval
            )
            head = (
                f"pipeline timed out after {poll_timeout:g}s, still pending"
                if timed_out
                else f"pipeline {ci.state}"
            )
            log = f"{head}: {ci.url}\n" + "".join(f"  {j}\n" for j in ci.jobs)
            if ci.mergeable is False:
                log += f"merge request is not mergeable: {ci.merge_detail or 'unknown'}\n"
                status = "failed"
            else:
                status = "done" if ci.state == "success" else "failed"
        case "sync_mr":
            await forge.push(repo=repo, branch=branch)
            await forge.update_mr(repo=repo, branch=branch, body=body)
            log, status = "pushed and synced the merge request description\n", "done"
        case "merge":
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "merged":
                log = f"already merged (!{existing.number}); nothing to do\n"
                status = "done"
            elif existing is not None and existing.state == "open":
                await forge.push(repo=repo, branch=branch)
                await forge.merge(repo=repo, branch=branch, mr=MR(number=0, url=""))
                landed = await _poll_merged(
                    forge, repo=repo, branch=branch, timeout=merge_timeout, interval=merge_interval
                )
                state = landed.state if landed is not None else "gone"
                if state == "merged":
                    log, status = f"merged !{landed.number}\n", "done"
                elif state == "open":
                    log = (
                        f"!{landed.number} is still open {merge_timeout:g}s after the "
                        "merge command returned: nothing has landed on main. The forge "
                        "may have scheduled an auto-merge for when its checks pass — "
                        f"look at {landed.url}\n"
                    )
                    status = "failed"
                else:
                    log = f"the merge request is {state}, not merged; nothing landed\n"
                    status = "failed"
            else:
                raise ForgeError(
                    f"no open merge request for {branch!r}"
                    + (f": !{existing.number} is {existing.state}" if existing else "")
                )
        case _:
            log, status = f"unknown forge handler {handler!r}\n", "failed"
    return log, status


async def _assert_submodules_covered(repo: Path, covered: set[Path]) -> None:
    """Refuse to open the root's merge request while an initialized submodule
    holds commits no `work_item_repos` row will carry anywhere.

    The §3a scan (`builtins.scan_submodules`) runs earlier in the same node
    and is what normally covers a submodule the agent touched but nobody
    declared -- this only fires when that scan itself missed one (submodule
    init failed, `git submodule status` errored), which must stop the chain
    rather than silently drop the change, exactly as it did on work item
    9d0ab38ff3c9439b90506df0f6966660.
    """
    raw = await _run(repo, ["git", "submodule", "status"])
    for line in raw.splitlines():
        if not line or line[0] != "+":
            continue
        parts = line[1:].split()
        if len(parts) < 2:
            continue
        path = (repo / parts[1]).resolve()
        if path not in covered:
            raise ForgeError(
                f"submodule {parts[1]} has commits not covered by any declared or "
                "discovered repo — it will not reach a merge request"
            )
```

- [ ] **Step 4: Rewrite `run_task` to loop**

```python
async def run_task(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    handler: str,
    backend: str,
    repo_forge: str | None = None,
    repo: Path,
    branch: str,
    title: str,
    round: int = 0,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    merge_timeout: float = MERGE_VERIFY_TIMEOUT,
    merge_interval: float = MERGE_VERIFY_INTERVAL,
) -> str:
    """One forge node -- against every repo `work_item_repos` names for this
    item, deepest submodule first, root last (design 3a), or just `repo` when
    the table has no rows for it (every single-repo item, unchanged from
    before this function went multi-repo).
    """
    from kraft import builtins as _builtins, store

    log_path, result_path = await _builtins.start_session(
        db, run_dirs, session_id=session_id, work_item_id=work_item_id,
        node_id=node_id, hook_point=hook_point, round=round,
    )
    rows = db.read(
        lambda c: c.execute(
            "SELECT * FROM work_item_repos WHERE work_item_id = ? ORDER BY merge_rank",
            (work_item_id,),
        ).fetchall()
    )
    multi = bool(rows)
    targets = [(r["id"], Path(r["repo_path"]), r["role"]) for r in rows] or [(None, repo, "root")]

    log, status = "", "done"
    try:
        live_forge = resolve(backend_for(backend, repo_forge))
        for row_id, target_repo, role in targets:
            if handler == "open_mr" and role == "root" and multi:
                await _assert_submodules_covered(target_repo, {t for _, t, _ in targets})
            one_log, one_status = await _run_one(
                live_forge, db, repo=target_repo, branch=branch, title=title,
                work_item_id=work_item_id, handler=handler, hook_point=hook_point,
                poll_timeout=poll_timeout, poll_interval=poll_interval,
                merge_timeout=merge_timeout, merge_interval=merge_interval,
            )
            log += (f"[{target_repo.name}] " if multi else "") + one_log
            if row_id is not None:
                new_state = {
                    "open_mr": "open",
                    "merge": "merged" if one_status == "done" else "failed",
                }.get(handler)
                if new_state:
                    await db.write(
                        lambda c, i=row_id, s=new_state: store.update_repo_state(c, i, merge_state=s)
                    )
            if one_status != "done":
                status = one_status
                break
    except ForgeError as exc:
        log, status = f"{hook_point} failed: {exc}\n", "failed"

    return await _builtins.finish_session(
        db, log_path, result_path, session_id=session_id, status=status, log=log
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_forge.py`
Expected: PASS — every pre-existing test (all single-repo, `rows == []`) and the two new ones.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/adapters/forge.py tests/test_forge.py
git commit -m "feat: forge.run_task loops over work_item_repos, deepest submodule first"
```

---

### Task 7: root policy — no merge request over a bare pointer bump

**Files:**
- Modify: `src/kraft/adapters/forge.py`
- Test: `tests/test_forge.py`

**Interfaces:**
- Consumes: `_run_one`, `targets` construction from Task 6
- Produces: `_default_branch(repo: Path) -> str` (module-private)
- Behavior added to `run_task`: root is dropped from the ordinary per-repo loop whenever it has no commits of its own (`_commits_on(root, branch)` empty) — see "Design correction" above for why.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_forge.py
def test_root_with_no_changes_of_its_own_never_opens_a_merge_request(tmp_path, monkeypatch):
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    root, _sub = make_repo_with_submodule(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="t", repo=str(root), template=_back_half_template(),
                bd_cwd=str(tracker), submodules=["repos/pkg"], root_merge_policy="bump_no_mr",
            )
            worktree = await _builtins.ensure_worktree(database, rd, repo=str(root), work_item_id=wid)
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            branch = store.branch_for(row)
            # the agent's half: a commit inside the submodule only
            sub = worktree / "repos" / "pkg"
            (sub / "new.txt").write_text("x\n")
            subprocess.run(["git", "add", "-A"], cwd=sub, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "m"],
                cwd=sub, check=True,
            )
            await forge.run_task(
                database, rd, session_id="s1", work_item_id=wid, node_id="open_mr",
                hook_point="on.mr.open", handler="open_mr", backend="fake",
                repo=worktree, branch=branch, title="t",
            )
            return database.read(lambda c: store.repos_for(c, wid))
        finally:
            await database.close()

    repos = asyncio.run(scenario())
    assert len(fake.opened) == 1  # the submodule only
    root_row = next(r for r in repos if r["role"] == "root")
    assert root_row["state"] == "pending"  # never touched by open_mr
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/test_forge.py -k root_with_no_changes_of_its_own`
Expected: FAIL — Task 6's loop still runs `open_mr` against root unconditionally, opening a second merge request.

- [ ] **Step 3: Implement**

Add near `_assert_submodules_covered`:

```python
async def _default_branch(repo: Path) -> str:
    """origin's default branch, or `main` when the forge doesn't say."""
    try:
        raw = await _run(repo, ["git", "symbolic-ref", "refs/remotes/origin/HEAD"])
        return raw.strip().rsplit("/", 1)[-1] or "main"
    except ForgeError:
        return "main"
```

In `run_task`, right after `targets` is built and before the `try:` block:

```python
    root_has_changes = True
    root_policy = "bump"
    if multi:
        policy_row = db.read(
            lambda c: c.execute(
                "SELECT root_merge_policy FROM work_items WHERE id = ?", (work_item_id,)
            ).fetchone()
        )
        root_policy = (policy_row["root_merge_policy"] if policy_row else None) or "bump"
        root_repo = next(t for _, t, role in targets if role == "root")
        root_has_changes = bool(await _commits_on(root_repo, branch))
        if root_policy != "skip" and not root_has_changes:
            # Nothing of root's own to review -- it never goes through the
            # ordinary per-repo loop below. Its pointer bump, if any, is
            # pushed directly after every submodule merges (Step 4 below),
            # never through a merge request. See "Design correction" at the
            # top of this plan for why this can't wait for `open_mr` to run.
            targets = [t for t in targets if t[2] != "root"]
        elif root_policy == "skip":
            targets = [t for t in targets if t[2] != "root"]
```

Then, after the existing `try:`/`for row_id, target_repo, role in targets:` loop finishes (still inside the `try`, after the loop, before `except ForgeError`), add the direct-push step — only fires on the `merge` handler, only once every submodule row that ran actually merged:

```python
        if (
            handler == "merge"
            and multi
            and root_policy != "skip"
            and not root_has_changes
            and status == "done"
        ):
            # `multi` guarantees `rows` is non-empty here, so this is always
            # the root row's own path, not `repo` (the worktree root is the
            # same thing, but the row is the one source of truth).
            root_repo = Path(next(r["repo_path"] for r in rows if r["role"] == "root"))
            bumped = []
            for r in rows:
                if r["role"] != "submodule":
                    continue
                sub_path = Path(r["repo_path"])
                default = await _default_branch(sub_path)
                await _run(sub_path, ["git", "fetch", "origin", default])
                merged_sha = (
                    await _run(sub_path, ["git", "rev-parse", f"origin/{default}"])
                ).strip()
                await _run(sub_path, ["git", "checkout", merged_sha])
                rel = str(sub_path.relative_to(root_repo))
                await _run(root_repo, ["git", "add", "--", rel])
                bumped.append(rel)
            if bumped and (
                await _run(root_repo, ["git", "status", "--porcelain", "--cached"])
            ).strip():
                await _run(
                    root_repo,
                    ["git", "commit", "-m", f"chore: bump submodule pointers for {work_item_id}"],
                )
                root_default = await _default_branch(root_repo)
                await _run(root_repo, ["git", "push", "origin", f"HEAD:{root_default}"])
                log += f"bumped {', '.join(bumped)} directly on {root_default}, no root merge request\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_forge.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/adapters/forge.py tests/test_forge.py
git commit -m "feat: root skips its own MR when it has no changes; pointer bump pushed directly"
```

---

### Task 8: regression test for the real shape

**Files:**
- Test: `tests/test_forge.py`

**Interfaces:**
- Consumes: everything above. No production code changes in this task.

- [ ] **Step 1: Write the test**

```python
# tests/test_forge.py
def test_the_shape_that_broke_on_9d0ab38ff3c9439b90506df0f6966660(tmp_path, monkeypatch):
    """A work item whose entire deliverable is inside a submodule, root told
    not to bump its pointer: the submodule gets its own merge request and the
    root gets none. This is the standing regression test for the real
    occurrence -- it exercises the same structure without depending on that
    one workspace ever existing again (see the spec's Verification section)."""
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="repos/packages")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="six OTEL metric attributes", repo=str(root),
                template=_back_half_template(), bd_cwd=str(tracker),
                submodules=["repos/packages"], root_merge_policy="skip",
            )
            worktree = await _builtins.ensure_worktree(database, rd, repo=str(root), work_item_id=wid)
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            branch = store.branch_for(row)
            sub = worktree / "repos" / "packages"
            (sub / "metrics.py").write_text("ATTRS = 6\n")
            subprocess.run(["git", "add", "-A"], cwd=sub, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "metrics"],
                cwd=sub, check=True,
            )
            status = await executor.run(
                database, rd, work_item_id=wid,
                registry=_forge_registry("fake"), bd_cwd=str(tracker),
            )
            repos = database.read(lambda c: store.repos_for(c, wid))
            return status, repos
        finally:
            await database.close()

    status, repos = asyncio.run(scenario())
    assert status == "completed"
    assert fake.merged == [1]  # only the submodule's MR, never a root one
    submodule_row = next(r for r in repos if r["role"] == "submodule")
    assert submodule_row["state"] == "merged"
    root_row = next(r for r in repos if r["role"] == "root")
    assert root_row["state"] == "pending"  # skip: root untouched, as asked
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest -q tests/test_forge.py -k 9d0ab38f`
Expected: PASS, on the first run — this task adds no production code, so a failure here means an earlier task's behavior doesn't compose the way its own tests implied; fix the earlier task, not this test.

- [ ] **Step 3: Run the whole targeted suite**

Run: `uv run pytest -q tests/test_forge.py tests/test_store.py tests/test_builtins.py tests/test_db.py`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_forge.py
git commit -m "test: regression coverage for the submodule-MR shape that broke on Kraft-qlsf"
```

---

## Self-review notes

- **Spec coverage:** `_assert_clean` (Task 3), commit identity (already landed as Kraft-cppp, reused not re-implemented), `work_item_repos` (Task 1), worktree setup (Task 4), §3a scan (Task 5), forge handlers per repo + `open_mr` refusal (Task 6), sequential deepest-first (Task 6's loop, `break` on first non-`"done"`), branch naming (already true by construction — every task checks out the same `branch` string everywhere, never a derived name), root policy (Task 7, corrected mid-plan per the recorded design correction) — every "What to build" bullet in the spec has a task. Frontend state mapping (Task 2) wasn't named in the spec's "What to build" but is required for the spec's own claim that the panel "starts telling the truth for free" to actually be true.
- **Placeholder scan:** no TBD/TODO. Task 7 Step 3 originally left a dead throwaway line with a "delete this" note — fixed inline during self-review, not shipped as a trap for whoever implements it.
- **Type consistency:** `store.repos_for(conn, work_item_id)` and its dict shape (`repo`/`path`/`role`/`merge_rank`/`state`/`mr_ref`) are the same across Tasks 1, 2, 6, 7, 8. `_run_one`'s signature in Task 6 matches every call site added in Tasks 6 and 7. `_pin_identity`'s signature matches its Kraft-cppp origin everywhere it's called (Tasks 4, 5).
- **Scope:** eight tasks, one work item, matches the user's explicit choice not to split into increments.
