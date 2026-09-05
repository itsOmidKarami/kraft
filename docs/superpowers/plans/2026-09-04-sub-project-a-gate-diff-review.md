# Gate Diff Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a human at a `human_review_approval` gate read the code the agent wrote, from a browser that has no filesystem access.

**Architecture:** `builtins.env_setup` pins the source repo's `HEAD` into a new `work_items.base_ref` column when it creates the worktree. A `GET /work-items/{wid}/diff` endpoint runs `git diff <base_ref>` inside that worktree and returns the body, a numstat file list, and an untracked-file list. A new `DiffModal` renders it, reached from the gate's existing `artifact` slot and from the detail view.

**Tech Stack:** Python 3.14, FastAPI, SQLite, pytest; React 18 + TypeScript + Vitest.

**Spec:** `docs/superpowers/specs/2026-09-04-sub-project-a-gate-diff-review-design.md`

**Beads:** `Kraft-8mu.2` (parent `Kraft-8mu`)

## Global Constraints

- Python 3.14+. Unparenthesized multi-exception `except A, B:` (PEP 758) is used in this codebase and is correct — do not "fix" it.
- Read-only against the target repo. No task may run `git add`, `git stash`, or anything else that writes to the worktree or its index — including `git add -N` to surface untracked files.
- A failed `git diff` must never be returned as an empty diff. These are different HTTP statuses and the distinction is a tested requirement.
- Diff body cap: **1 MB**, a module constant in `api.py`, not policy.
- `base_ref` is nullable. Null is a normal state — work items that predate the migration, and any future template without an `env_setup` node — never an error. Both shipped templates DO have an `env_setup` node, so a freshly created item always gets a base.
- The viewer is read-only. No editing, no inline comments, no syntax highlighting, no new npm dependency.
- Run `just lint` before each commit.

---

### Task 1: `base_ref` column

**Files:**
- Modify: `src/kraft/db.py:16` (`SCHEMA_VERSION`), `src/kraft/db.py:19-37` (base `work_items` DDL), `src/kraft/db.py:97-156` (`_MIGRATIONS`)
- Modify: `src/kraft/store.py`
- Test: `tests/test_db.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `work_items.base_ref TEXT` (nullable). `store.set_base_ref(conn: sqlite3.Connection, work_item_id: str, sha: str) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_migration_adds_base_ref(tmp_path):
    path = tmp_path / "m.db"
    conn = db._connect(path)
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert "base_ref" in cols
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    assert version == db.SCHEMA_VERSION
```

Asserting against `db.SCHEMA_VERSION` rather than a literal `8`: sub-project G
also adds a migration, and whichever lands second takes the next number. A test
pinned to a literal version breaks the other sub-project for no reason.

Append to `tests/test_store.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test -k "base_ref"`
Expected: FAIL — `base_ref` not in columns; `store.set_base_ref` does not exist.

- [ ] **Step 3: Write the implementation**

In `src/kraft/db.py`, set `SCHEMA_VERSION = 8`. Add `base_ref TEXT` to the `work_items` block in the base schema, after `root_merge_policy TEXT,`. Add to `_MIGRATIONS`:

```python
    7: ["ALTER TABLE work_items ADD COLUMN base_ref TEXT"],
```

The key is the version being migrated **from**: `db.migrate` runs `for v in range(version, SCHEMA_VERSION)` and applies `_MIGRATIONS[v]`, bumping `user_version` to `v + 1` after each. So going 7 → 8 uses key `7`.

In `src/kraft/store.py`, next to `set_steer`:

```python
def set_base_ref(conn: sqlite3.Connection, work_item_id: str, sha: str) -> None:
    """Pin the commit a work item's diff is measured against.

    Written once, when the worktree is created. A merge-base recomputed later
    moves when the default branch moves, and a diff that changes under an
    unchanged work item is worse than no diff.
    """
    conn.execute(
        "UPDATE work_items SET base_ref = ?, updated_at = ? WHERE id = ?",
        (sha, _now(), work_item_id),
    )
```

No event is appended: this is bookkeeping, not a state change a human reads in the timeline.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test -k "base_ref or migrat"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/db.py src/kraft/store.py tests/test_db.py tests/test_store.py
git commit -m "feat: work_items.base_ref column and setter"
```

---

### Task 2: Stamp the base at worktree creation

**Files:**
- Modify: `src/kraft/builtins.py:6-27` (`env_setup`)
- Test: `tests/test_builtins.py`

**Interfaces:**
- Consumes: `store.set_base_ref` from Task 1.
- Produces: after `env_setup` runs, `work_items.base_ref` holds the source repo's `HEAD` sha at creation time.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_builtins.py`, following the `scenario()` async pattern already used in that file:

```python
def test_env_setup_stamps_base_ref(tmp_path):
    repo = make_repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B",
                    title="t",
                    repo=str(repo),
                    chain_template="quick-task",
                    chain_definition="{}",
                )
            )
            await kraft_builtins.env_setup(
                database, rd, session_id="s1", work_item_id="w1",
                node_id="env_setup", repo=str(repo),
            )
            row = database.read(
                lambda c: c.execute("SELECT base_ref FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["base_ref"] == head
        finally:
            await database.close()

    asyncio.run(scenario())


def test_env_setup_does_not_restamp_on_reentry(tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c, id="w1", bead_id="B", title="t", repo=str(repo),
                    chain_template="quick-task", chain_definition="{}",
                )
            )
            await kraft_builtins.env_setup(
                database, rd, session_id="s1", work_item_id="w1",
                node_id="env_setup", repo=str(repo),
            )
            await database.write(lambda c: store.set_base_ref(c, "w1", "PINNED"))
            # second call returns early: the worktree already exists
            status = await kraft_builtins.env_setup(
                database, rd, session_id="s2", work_item_id="w1",
                node_id="env_setup", repo=str(repo),
            )
            assert status == "done"
            row = database.read(
                lambda c: c.execute("SELECT base_ref FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["base_ref"] == "PINNED"
        finally:
            await database.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test -k env_setup`
Expected: the stamp test FAILs with `base_ref` being `None`; the re-entry test passes trivially (it guards a regression, which is its job).

- [ ] **Step 3: Write the implementation**

In `src/kraft/builtins.py`, add the import and stamp before the `git worktree add` call, after the idempotence early-return:

```python
from kraft.config import git_read
```

and inside `env_setup`, immediately after the `if worktree.is_dir(): return "done"` guard:

```python
    # Pin the base *before* the worktree exists, so the early return above
    # guarantees a crashed-and-retried run never re-pins to a moved HEAD.
    head = git_read(Path(repo), "rev-parse", "HEAD")
    if head:
        await db.write(lambda c: store.set_base_ref(c, work_item_id, head))
```

`Path` must be imported in `builtins.py` if it is not already.

This uses `git_read`, which is `config._git` renamed in Task 3. Do Task 3's rename first, or inline `subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, ...)` here and switch to `git_read` in Task 3. Prefer doing Task 3's rename first.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test -k "env_setup or builtins"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/builtins.py tests/test_builtins.py
git commit -m "feat: pin base_ref when the worktree is created"
```

---

### Task 3: Promote the git helper

**Files:**
- Modify: `src/kraft/config.py:76-85` and its 4 call sites in the same file
- Test: covered by the existing `test_settings_api.py` probe cases

**Interfaces:**
- Consumes: nothing.
- Produces: `config.git_read(cwd: Path, *args: str) -> str | None` — one read-only git command, stripped stdout on success, `None` on any failure. Never raises.

- [ ] **Step 1: Rename**

In `src/kraft/config.py`, rename `_git` to `git_read` and update all four call sites in that file. The docstring stays accurate as written: "One read-only git command, or None if git says no. Never raises."

The return contract is what Task 4 depends on and must not change: **`None` means the command failed; `""` means it succeeded with no output.** That distinction is what separates a broken worktree from an empty diff.

- [ ] **Step 2: Run tests**

Run: `just test -k "probe or settings_api"`
Expected: PASS — a pure rename, existing coverage carries it.

- [ ] **Step 3: Commit**

```bash
git add src/kraft/config.py
git commit -m "refactor: make the read-only git helper public as git_read"
```

---

### Task 4: The diff endpoint

**Files:**
- Modify: `src/kraft/api.py` — add after `get_work_item_documents`
- Test: Create `tests/test_diff_api.py`

**Interfaces:**
- Consumes: `config.git_read` (Task 3), `work_items.base_ref` (Tasks 1-2), the existing `_work_item_row(st, wid)` helper in `api.py`.
- Produces: `GET /work-items/{wid}/diff` returning
  `{work_item_id: str, base_ref: str | None, files: [{path: str, insertions: int, deletions: int}], diff: str, untracked: [str], truncated: bool}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_diff_api.py`:

```python
"""GET /work-items/{wid}/diff — the review surface at a human gate.

The pair that matters here is empty-vs-broken: a reviewer who cannot tell an
empty diff from a failed one approves unreviewed code.
"""

import subprocess

from support.harness import make_repo


def _write(path, text):
    path.write_text(text)


def test_diff_shows_committed_and_uncommitted_changes(client, seeded_item, worktree):
    _write(worktree / "calc.py", "committed\n")
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "c"], cwd=worktree, check=True)
    _write(worktree / "calc.py", "committed\nuncommitted\n")

    body = client.get(f"/work-items/{seeded_item}/diff").json()
    assert "committed" in body["diff"]
    assert "uncommitted" in body["diff"]
    assert body["files"][0]["path"] == "calc.py"
    assert body["truncated"] is False


def test_diff_lists_untracked_without_adding_them(client, seeded_item, worktree):
    _write(worktree / "new_file.py", "x = 1\n")
    body = client.get(f"/work-items/{seeded_item}/diff").json()
    assert body["untracked"] == ["new_file.py"]
    assert "new_file.py" not in body["diff"]
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=worktree, capture_output=True, text=True
    )
    assert staged.stdout.strip() == ""  # read-only: nothing was staged


def test_diff_with_null_base_ref_is_empty_not_an_error(client, item_without_base_ref):
    r = client.get(f"/work-items/{item_without_base_ref}/diff")
    assert r.status_code == 200
    assert r.json()["base_ref"] is None
    assert r.json()["diff"] == ""


def test_diff_404s_on_unknown_work_item(client):
    assert client.get("/work-items/nope/diff").status_code == 404


def test_diff_404s_when_the_worktree_is_gone(client, seeded_item, worktree):
    import shutil

    shutil.rmtree(worktree)
    assert client.get(f"/work-items/{seeded_item}/diff").status_code == 404


def test_diff_500s_when_git_fails_rather_than_returning_empty(client, seeded_item, worktree):
    # A worktree whose git metadata is broken: present on disk, unusable to git.
    (worktree / ".git").unlink()
    (worktree / ".git").mkdir()
    r = client.get(f"/work-items/{seeded_item}/diff")
    assert r.status_code == 500
    assert r.json().get("detail")


def test_diff_truncates_at_a_file_boundary(client, seeded_item, worktree, monkeypatch):
    import kraft.api as api_mod

    monkeypatch.setattr(api_mod, "DIFF_MAX_BYTES", 200)
    for i in range(20):
        _write(worktree / f"f{i}.py", "x = 1\n" * 100)
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)

    body = client.get(f"/work-items/{seeded_item}/diff").json()
    assert body["truncated"] is True
    assert len(body["files"]) == 20  # the file list is never truncated
    assert not body["diff"].rstrip().endswith("x = 1")  # cut between files, not mid-hunk
```

Add module-level fixtures `client`, `seeded_item`, `worktree`, and `item_without_base_ref` to this file, built from the patterns already in `tests/test_api.py` (app + `TestClient`) and `tests/test_builtins.py` (`make_repo`, `RunDirs`, `env_setup` to produce a real worktree with a stamped base).

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_diff_api.py`
Expected: FAIL — 404 on every case, the route does not exist.

- [ ] **Step 3: Write the implementation**

In `src/kraft/api.py`, add a module constant near the other constants:

```python
#: Diff bodies larger than this are cut at a file boundary. Protects the
#: browser; not a user decision, so not policy.
DIFF_MAX_BYTES = 1_000_000
```

And the route, after `get_work_item_documents`:

```python
def _truncate_at_file_boundary(diff: str, limit: int) -> tuple[str, bool]:
    """Cut a unified diff to `limit` bytes on a `diff --git` boundary."""
    if len(diff.encode()) <= limit:
        return diff, False
    kept: list[str] = []
    size = 0
    for chunk in diff.split("\ndiff --git ")[:1] + [
        "\ndiff --git " + c for c in diff.split("\ndiff --git ")[1:]
    ]:
        if size + len(chunk.encode()) > limit and kept:
            break
        kept.append(chunk)
        size += len(chunk.encode())
    return "".join(kept), True


@app.get("/work-items/{wid}/diff")
async def get_work_item_diff(wid: str, request: Request):
    """The changes an agent made, for a reviewer with no filesystem access.

    Diffs the working tree against `base_ref`, not `base_ref...HEAD`: an agent
    that wrote files without committing them is the normal mid-chain state, and
    a committed-only diff would show an empty change set while the work sat on
    disk.
    """
    st = request.app.state
    row = _work_item_row(st, wid)  # 404s on an unknown work item
    base = row["base_ref"]
    worktree = st.run_dirs.worktrees / wid
    if not worktree.is_dir():
        raise HTTPException(404, "this work item has no worktree yet")
    if not base:
        return {
            "work_item_id": wid, "base_ref": None, "files": [],
            "diff": "", "untracked": [], "truncated": False,
        }

    body = config_mod.git_read(worktree, "diff", base)
    numstat = config_mod.git_read(worktree, "diff", "--numstat", base)
    status = config_mod.git_read(worktree, "status", "--porcelain")
    if body is None or numstat is None or status is None:
        # None means git itself failed. Returning an empty diff here would be
        # indistinguishable from "no changes" to the human approving the gate.
        raise HTTPException(500, f"git could not read the worktree at {worktree}")

    files = []
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            ins, dels, path = parts
            files.append({
                "path": path,
                "insertions": int(ins) if ins.isdigit() else 0,
                "deletions": int(dels) if dels.isdigit() else 0,
            })

    untracked = [ln[3:] for ln in status.splitlines() if ln.startswith("?? ")]
    diff, truncated = _truncate_at_file_boundary(body, DIFF_MAX_BYTES)
    return {
        "work_item_id": wid, "base_ref": base, "files": files,
        "diff": diff, "untracked": untracked, "truncated": truncated,
    }
```

`config_mod` is already the import alias for `kraft.config` in `api.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test tests/test_diff_api.py -v`
Expected: PASS, all eight.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/api.py tests/test_diff_api.py
git commit -m "feat: GET /work-items/{id}/diff for gate review"
```

---

### Task 5: The diff viewer

**Files:**
- Create: `frontend/src/components/DiffModal.tsx`, `frontend/src/components/DiffModal.test.tsx`
- Modify: `frontend/src/types.ts`, `frontend/src/api.ts`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: `GET /work-items/{wid}/diff` from Task 4; the existing `useModal` hook (`frontend/src/useModal.ts`).
- Produces: `<DiffModal workItemId={string} onClose={() => void} />`; `api.getWorkItemDiff(id: string)`; the `WorkItemDiff` type.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/DiffModal.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DiffModal } from "./DiffModal";
import * as api from "../api";

const body = {
  work_item_id: "w1",
  base_ref: "abc1234567",
  files: [{ path: "calc.py", insertions: 2, deletions: 1 }],
  diff: "diff --git a/calc.py b/calc.py\n@@ -1 +1,2 @@\n-old\n+new\n context\n",
  untracked: ["extra.py"],
  truncated: false,
};

describe("DiffModal", () => {
  it("classes added, removed and hunk lines", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue(body);
    render(<DiffModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText("+new")).toHaveClass("diff-add");
    expect(screen.getByText("-old")).toHaveClass("diff-del");
    expect(screen.getByText("@@ -1 +1,2 @@")).toHaveClass("diff-hunk");
    expect(screen.getByText(" context")).toHaveClass("diff-ctx");
  });

  it("lists untracked files", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue(body);
    render(<DiffModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText("extra.py")).toBeInTheDocument();
  });

  it("says when the body was truncated", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({ ...body, truncated: true });
    render(<DiffModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText(/truncated/i)).toBeInTheDocument();
  });

  it("says when there is nothing to show", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({
      ...body, base_ref: null, files: [], diff: "", untracked: [],
    });
    render(<DiffModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText(/no diff available/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run DiffModal`
Expected: FAIL — cannot resolve `./DiffModal`.

- [ ] **Step 3: Write the implementation**

In `frontend/src/types.ts`:

```ts
export interface WorkItemDiff {
  work_item_id: string;
  base_ref: string | null;
  files: { path: string; insertions: number; deletions: number }[];
  diff: string;
  untracked: string[];
  truncated: boolean;
}
```

In `frontend/src/api.ts`, alongside `getWorkItemDocuments`:

```ts
export const getWorkItemDiff = (id: string) =>
  req<WorkItemDiff>(`/work-items/${id}/diff`);
```

Create `frontend/src/components/DiffModal.tsx`:

```tsx
import { useEffect, useState } from "react";
import { X } from "@phosphor-icons/react";
import * as api from "../api";
import type { WorkItemDiff } from "../types";
import { useModal } from "../useModal";

/**
 * The changes an agent made, read-only, over the work item. Sibling to
 * DocumentModal rather than a mode of it: a diff shares none of that
 * component's markdown rendering or editor-launch menu.
 *
 * Not a route, for DocumentModal's reason: closing must put the reader back
 * where they were, not in history.
 */

const lineClass = (line: string) =>
  line.startsWith("+") ? "diff-add"
  : line.startsWith("-") ? "diff-del"
  : line.startsWith("@@") ? "diff-hunk"
  : "diff-ctx";

export function DiffModal({
  workItemId,
  onClose,
}: {
  workItemId: string;
  onClose: () => void;
}) {
  const ref = useModal<HTMLDivElement>(onClose);
  const [diff, setDiff] = useState<WorkItemDiff | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .getWorkItemDiff(workItemId)
      .then(setDiff)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [workItemId]);

  const totals = diff?.files.reduce(
    (a, f) => ({ ins: a.ins + f.insertions, del: a.del + f.deletions }),
    { ins: 0, del: 0 },
  );

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="changes">
      <div className="dialog diff-modal" ref={ref}>
        <header className="diff-modal-head">
          <span className="mono">{diff?.base_ref?.slice(0, 10) ?? "—"}</span>
          {totals && (
            <span className="diff-totals">
              <span className="diff-add">+{totals.ins}</span>{" "}
              <span className="diff-del">−{totals.del}</span>
            </span>
          )}
          <button className="btn btn-ghost" onClick={onClose} aria-label="close">
            <X size={14} />
          </button>
        </header>

        {err && <p className="form-error">{err}</p>}

        {diff && diff.diff === "" && diff.untracked.length === 0 && (
          <p className="empty">No diff available for this work item yet.</p>
        )}

        {diff && diff.files.length > 0 && (
          <ul className="diff-files">
            {diff.files.map((f) => (
              <li key={f.path}>
                <span className="mono">{f.path}</span>
                <span className="diff-add">+{f.insertions}</span>
                <span className="diff-del">−{f.deletions}</span>
              </li>
            ))}
          </ul>
        )}

        {diff && diff.diff !== "" && (
          <pre className="diff-body">
            {diff.diff.split("\n").map((line, i) => (
              <div key={i} className={lineClass(line)}>
                {line}
              </div>
            ))}
          </pre>
        )}

        {diff?.truncated && (
          <p className="field-hint">
            Diff truncated — the file list above is complete. Open the worktree for the
            full change.
          </p>
        )}

        {diff && diff.untracked.length > 0 && (
          <div className="diff-untracked">
            <span className="field-hint">New files (content not shown)</span>
            <ul>
              {diff.untracked.map((p) => (
                <li key={p} className="mono">
                  {p}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
```

This mirrors `DocumentModal.tsx:132-133` exactly: `role="dialog"` and `aria-modal` sit on `.dialog-backdrop`, the `useModal` ref goes on the inner `.dialog` element, and the component-specific class rides alongside `.dialog`. `useModal<T extends HTMLElement>(onClose)` returns the ref — the type parameter is required.

Append to `frontend/src/styles.css`, using the palette variables already defined there:

```css
.diff-modal { width: 820px; max-width: 100%; padding: 0; gap: 0;
              max-height: calc(100vh - 88px); overflow: hidden; }
.diff-body { overflow: auto; padding: 16px 22px 22px;
             font-size: 12px; line-height: 1.45; }
.diff-body > div { white-space: pre; }
.diff-add  { color: var(--color-accent); }
.diff-del  { color: var(--color-accent-2); }
.diff-hunk { color: var(--color-neutral-500); }
.diff-ctx  { color: var(--color-neutral-300); }
```

**On the colours.** There is no `--ok` or `--danger` in this codebase, and that
is deliberate rather than an omission: the palette is near-monochrome with one
accent, and even `failed` is rendered as a *brighter neutral*
(`styles.css:64,77`), never red. Adding green-and-red for a diff would import a
visual language the rest of the app does not speak.

So additions take `--color-accent` and deletions `--color-accent-2` — two hues
that already exist, already sit beside each other in `nocturne.css`, and are
distinguishable. The `+` and `-` prefix characters remain in the rendered text
and carry the meaning on their own, which is also what makes this readable
without colour vision.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run DiffModal`
Expected: PASS, all four.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/DiffModal.tsx frontend/src/components/DiffModal.test.tsx \
        frontend/src/types.ts frontend/src/api.ts frontend/src/styles.css
git commit -m "feat: DiffModal renders a work item's changes"
```

---

### Task 6: Surface it at the gate and on the detail view

**Files:**
- Modify: `frontend/src/views/WorkItemDetail.tsx:168-173` (the `<Gate>` call)
- Test: `frontend/src/views/WorkItemDetail.test.tsx`

**Interfaces:**
- Consumes: `DiffModal` from Task 5; `Gate`'s existing `artifact?: ReactNode` prop (`frontend/src/components/Gate.tsx:39`).
- Produces: nothing consumed later.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/views/WorkItemDetail.test.tsx`:

```tsx
it("offers a diff at the human review gate", async () => {
  renderDetail({ status: "needs_human", gate: "human_review_approval" });
  expect(await screen.findByRole("button", { name: /review changes/i })).toBeInTheDocument();
});

it("does not offer a diff at a spec gate", async () => {
  renderDetail({ status: "needs_human", gate: "spec_approval" });
  expect(screen.queryByRole("button", { name: /review changes/i })).toBeNull();
});
```

`renderDetail` stands for the existing render helper in that file — reuse it, do not add one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run WorkItemDetail`
Expected: FAIL — no such button.

- [ ] **Step 3: Write the implementation**

In `frontend/src/views/WorkItemDetail.tsx`, add state near the other view state:

```tsx
const [showDiff, setShowDiff] = useState(false);
```

Pass the artifact into the existing `<Gate>` call:

```tsx
        <Gate
          item={item}
          gate={gate}
          sub={`${nodeSessions.map((s) => s.hook_point).join(", ")} completed clean`}
          artifact={
            gate === "human_review_approval" ? (
              <button className="btn btn-secondary" onClick={() => setShowDiff(true)}>
                Review changes
              </button>
            ) : undefined
          }
        />
```

The three document gates — `spec_approval`, `plan_approval`, `chain_finalized` — are decisions about a document and pass no diff; at a spec gate it would usually be empty and always be noise.

Render the modal once, at the end of the component:

```tsx
      {showDiff && <DiffModal workItemId={item.id} onClose={() => setShowDiff(false)} />}
```

Add the same "Review changes" button to the `control-row` block below, **outside** its `desktop-only` wrapper — the whole point is that this one works from another machine, unlike the "Open worktree" button beside it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test-ui`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/WorkItemDetail.tsx frontend/src/views/WorkItemDetail.test.tsx
git commit -m "feat: review changes from the human review gate"
```

---

### Task 7: Verify end to end and close

**Files:** none.

- [ ] **Step 1: Drive it against a real dev instance**

```bash
just dev-reset && just dev
```

In a second shell: `just dev-seed`. Open `http://127.0.0.1:5173`, find the seeded item sitting at a pending gate, and confirm the diff opens and renders. The seeder drives the real HTTP API, so what renders is what the executor produced.

- [ ] **Step 2: Full gates**

Run: `just test && just test-ui && just lint`
Expected: all pass.

- [ ] **Step 3: Close the bead**

```bash
bd close Kraft-8mu.2 --reason="diff review at gates; base_ref pinned at env_setup"
```

`Kraft-8mu.4` (away-from-desk) unblocks here.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 base commit, schema v8, stamped at `env_setup`, not re-stamped, nullable | 1, 2 |
| §2 endpoint, working-tree-vs-base, untracked without mutating, numstat | 4 |
| §2 size cap at a file boundary, `truncated` flag, complete file list | 4 |
| §2 failure table — 404 unknown, 404 no worktree, 500 on git failure | 4 |
| §3 `DiffModal` as a sibling, `useModal`, layout, per-line classes, no highlighting | 5 |
| §4 gate artifact slot for `human_review_approval` only; detail view button | 6 |
| §5 testing, both backend and frontend | 1, 2, 4, 5, 6 |
| §6 deferred per-node diffs | no task — deliberately out of scope |

**Placeholders:** none. Task 5 Step 3 and Task 6 Step 1 name `useModal`'s call shape and `renderDetail` as things to match in the existing files rather than invent, and say so explicitly. Task 4 Step 1 says which existing test files the fixtures are built from.

**Type consistency:** `base_ref` is `TEXT`/`str | None`/`string | null` throughout. `store.set_base_ref(conn, work_item_id, sha)` is defined in Task 1 and called with those names in Task 2. `config.git_read` is named in Task 3 and used with that name in Tasks 2 and 4 — Task 2 flags the ordering dependency. `DIFF_MAX_BYTES` is defined in Task 4 and monkeypatched under that name in its own test. The `WorkItemDiff` field names match the endpoint's returned keys one for one.

**Ordering note:** Task 3 (the `git_read` rename) is a prerequisite of Task 2's preferred implementation. If executed strictly in order, Task 2 must inline `subprocess.run` and Task 3 must then switch it over. Executing 1 → 3 → 2 → 4 → 5 → 6 → 7 avoids the rework.
