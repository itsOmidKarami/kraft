# Sub-project A: diff review at human gates

Date: 2026-09-04
Beads: Kraft-8mu.2 (parent Kraft-8mu)

## Problem

A `human_review_approval` gate asks a human to approve a merge request and shows
them nothing to approve. `Gate.tsx` renders a title, a prompt line, an optional
`artifact` node, and two buttons. The artifact, where one is passed, is a linked
markdown document — the spec or the plan. The code the agent actually wrote is
not on the screen.

The existing way out is `POST /work-items/{wid}/open-worktree`, and its own
docstring states the limit:

> Local-only by nature: the path means nothing to a browser on another machine,
> which is why the UI only offers this when the server can act on it.

Kraft is now meant to be reachable from off the machine. On that path the escape
hatch is gone and the gate is unreviewable, so serving the diff is not a
convenience — it is the only review surface that survives the product's own
deployment target.

Everything needed is already in place. `builtins.env_setup` creates the worktree
at `run_dirs.worktrees / work_item_id` on a branch `kraft/{work_item_id}`, and
`Gate.tsx` already has the `artifact` slot to put a viewer in.

## Non-goals

- **Inline comments on the diff.** Rejection already carries a note, and the
  reject textarea is the place for it. Per-line commenting means a comment
  store, anchors that survive a re-run, and a way to show them to the agent —
  a sub-project of its own, and one the forge already does.
- **Editing from the viewer.** Read-only, like `DocumentModal`. Kraft does not
  edit repo files.
- **Conflict resolution or a three-way merge view.**
- **A diff library.** Unified diff text plus a per-line CSS class is enough; a
  side-by-side renderer is a dependency bought for a rendering preference.
- **Per-node diffs.** See §6.

## 1. The base commit

A diff needs two endpoints. The working tree is one. The other must be pinned,
not recomputed: a merge-base against the repo's default branch moves whenever
that branch moves, so the same gate would show a different diff on Tuesday than
it did on Monday, and a diff that changes under an unchanged work item is worse
than no diff.

`work_items` gains one column, schema v8, following the `_MIGRATIONS` pattern in
`db.py` (`SCHEMA_VERSION` was 7 when this was written; the column landed as v8):

```sql
ALTER TABLE work_items ADD COLUMN base_ref TEXT
```

`builtins.env_setup` stamps it: `git rev-parse HEAD` in the source repo,
recorded before `git worktree add` runs. The function is already idempotent
against a crashed prior run — it returns early when the worktree exists — and
the stamp goes on the creating path only, so a re-entry never re-pins the base.

`base_ref` is nullable and stays null for any work item created before this
change — the migration adds the column, it cannot invent a base for work already
done — and for any future template with no `env_setup` node. Both shipped
templates (`default.yaml`, `quick-task.yaml`) do have one, so a freshly created
item always gets a base. Null is a normal state, not an error: the endpoint
reports "no diff available yet" and the UI does not offer the viewer.

## 2. The diff endpoint

```
GET /work-items/{wid}/diff  →  { work_item_id, base_ref, files: [...],
                                 diff: "...", untracked: [...], truncated: bool }
```

One git invocation for the content:

```
git diff <base_ref>            # cwd = the item's worktree
```

Diffing the *working tree* against the base, rather than `<base>...HEAD`,
matters: an agent that wrote files but has not committed them is the normal
mid-chain state, and a committed-only diff would show a reviewer an empty
change set while the work sat right there on disk. Working-tree-vs-base covers
committed and uncommitted changes in one command.

It does not cover untracked files. Rather than mutate the index with
`git add -N` to make them appear, they are listed separately from
`git status --porcelain`, and the UI shows them as a plain list of paths. A new
file the agent added is thereby visible as a fact even though its content is
not in the diff body — which is the honest rendering of what git will tell us
without touching the repo.

`files` comes from `git diff --numstat` against the same base, giving path,
insertions, deletions for the file list, without parsing the diff body.

### Size

The body is capped at 1 MB. On overflow the response carries `truncated: true`,
the full `files` list — which is small regardless of diff size — and as much
body as fits, cut at a file boundary. A reviewer facing a truncated diff has the
complete file-level picture and is told plainly that the body is short; the
`open-worktree` button remains for the local case.

The cap is a constant in `api.py`, not policy. It protects the browser, and
nothing about it is a user decision.

### Failure modes

| Condition | Response |
|---|---|
| unknown work item | 404, via the existing `_work_item_row` |
| `base_ref` is null | 200, `base_ref: null`, empty diff |
| worktree directory gone | 404, matching `open-worktree`'s wording |
| `git diff` exits non-zero | 500 with stderr — a broken worktree is a real fault and must not read as an empty diff |

That last row is the one that matters. An empty diff and a failed diff look
identical to a reviewer, and the failure mode of confusing them is approving
unreviewed code.

## 3. The viewer

A new `DiffModal.tsx`, sibling to `DocumentModal.tsx` and built on the same
`useModal` hook and the same reasoning recorded there:

> Not a route: the reader is looking at the item, and closing must put them back
> where they were, not in history.

It is a sibling rather than an extension. `DocumentModal` is markdown rendering
plus an editor-launch menu plus a document-kind icon set; a diff shares none of
that. Folding both into one component means a prop that switches between two
unrelated bodies.

Layout, top to bottom: base ref (short sha) and total `+/-`; the file list from
`files` with per-file counts; the unified diff body; the untracked list if
non-empty; a truncation notice if `truncated`.

Rendering is a `<pre>` per hunk with a class per line derived from its first
character — `+`, `-`, `@`, everything else. Three CSS rules against the existing
palette in `styles.css`. No syntax highlighting: it is a review aid, and
highlighting a diff correctly means a tokenizer per language.

## 4. Where it appears

Two places, both existing:

- **The gate.** `Gate.tsx` takes `artifact` today. `WorkItemDetail` passes a
  "Review changes" button into that slot for `human_review_approval`. The other
  three gates — `spec_approval`, `plan_approval`, `chain_finalized` — are
  decisions about a document, and keep passing their document; a diff at a spec
  gate would usually be empty and always be noise.
- **The detail view**, next to "Open worktree", available at any status once
  `base_ref` is set. Reviewing before the gate is a legitimate thing to want,
  and the button that works remotely should sit beside the one that does not.

## 5. Testing

Backend, against a real temp git repo — the suite already builds these for the
worktree tests:

- base stamped at `env_setup`, and *not* re-stamped when the function re-enters
  on an existing worktree
- committed change appears; uncommitted change appears; both appear together
- untracked file is listed and absent from the body
- null `base_ref` returns 200 and an empty diff
- missing worktree 404s; a `git diff` failure 500s and does not return an empty
  diff — the pair that guards the confusion in §2
- truncation sets the flag, keeps `files` complete, and cuts at a file boundary

Frontend: `DiffModal.test.tsx` for the three line classes, the truncation
notice, and the untracked list; a `Gate.test.tsx` case that the artifact slot
renders the review button for `human_review_approval` and not for
`spec_approval`.

## 6. Deferred

**Per-node diffs** ("what did *this* node change") need a start-of-node sha per
node, which is a second table or a column on `worker_sessions`, and a UI to
choose between scopes. The whole-item diff is what a merge decision is actually
about. Revisit if a reviewer at a gate is observed hunting for which node did
what.

## Acceptance

- A `human_review_approval` gate offers a diff that opens in the browser with no
  filesystem access, from a machine that is not the server.
- The diff shows uncommitted agent work.
- A work item with no `base_ref` degrades to "no diff available" rather than
  erroring.
- `just test`, `just test-ui`, `just lint` pass.
