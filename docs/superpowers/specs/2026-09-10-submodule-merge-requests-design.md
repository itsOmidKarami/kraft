# Submodule merge requests: implementing design 06 §2–§3a

## Problem

Kraft cannot open a merge request in a submodule. On a superproject-plus-submodules
checkout — a "virtual monorepo", the dominant cross-repo shape and already the shape
design 06 was written for — a work item whose entire deliverable lives inside a
submodule finishes green having pushed nothing of that deliverable anywhere, and
opens a merge request in the wrong repository.

This is not a theoretical gap. It happened on work item
`9d0ab38ff3c9439b90506df0f6966660` (`wfm-workspace`, six OTEL metric attributes,
every file to change inside `repos/packages`). The item's own description said:

> Every file to change lives in the `repos/packages` submodule, which is its own git
> repository. Branch, commit, test and open the merge request inside `repos/packages`;
> do not bump the workspace submodule pointer as part of this change.

The agent did its half correctly: branched inside the submodule, committed the
metrics change there, ran the submodule's suites green. Kraft then opened MR !13 on
`wfm-workspace` — carrying the plan, the session notes, the spec, and some
workspace-root test plumbing, and **not one line of the metrics change**. The
submodule branch was never pushed. `git ls-remote origin "kraft/*"` inside
`repos/packages` returns nothing.

The item reached `mr_checks` and reported healthy throughout. Nothing in the chain
noticed that the work was unreachable.

## Why it happened

Three independent mechanisms, each individually defensible, combining into a silent
loss.

### 1. The forge adapter is single-repo by construction

`src/kraft/adapters/forge.py` is built around one repository path. `run_task` takes
`repo: Path` (:798) and every handler threads that one value through: `push(repo=…)`,
`open_mr(repo=…)`, `find_mr(repo=…)`, `ci_status(repo=…)`, `merge(repo=…)`,
`_assert_clean(repo)`, `_assert_pushed(repo, branch)`, `_commits_on(repo, branch)`.
There is no traversal into `repos/*` anywhere in the file, and no parameter through
which a second repository could be named. That path is the worktree root, so the root
is the only repository `open_mr` can act on. It did exactly what it was built to do.

### 2. The one guard that would have caught it was blindfolded by config

`_assert_clean` (`forge.py:196`) exists precisely for "work that would not reach the
merge request", and its docstring says so — it was added because a source file went
missing on work item `5163dd1b`. A submodule holding new local commits normally shows
at the superproject as `M repos/packages`, which would have tripped it.

It did not, because a workspace checkout of this kind sets, per submodule:

```
[submodule "repos/packages"]
	ignore = all
```

That is a reasonable thing for a human to set — nobody wants pointer churn in every
`git status` of a six-submodule workspace. But `_assert_clean` runs a plain

```
git status --porcelain -- . ':(exclude).engineering/sessions'
```

and therefore inherits the blindness. The root tree reported clean.
`commit_stragglers` (`forge.py:216`) runs the identical status check and so agreed
there was nothing left behind. Both guards passed on a branch missing its entire
payload.

### 3. The cross-repo feature is designed, stored, and rendered — but never executed

`docs/consolidated/06_cross_repo_federation.md` §2–§3a already specifies this
completely: `work_item_repos` as the per-repo table (§2.2), `merge_rank` computed
topologically with the deepest submodule merging first (§3a), `root_merge_policy` with
`bump` / `bump_no_mr` / `skip` (§2.3), selective `git submodule update --init -- <paths>`
never blanket (§3 step 3), a `git worktree add` inside each resolved submodule on the
item's branch (§3 step 4), a per-submodule diff scan to catch an undeclared submodule
the implementation touched (§3a), and per-submodule MR + CI + review with a fix loop.

What exists in code is the front and back of that design with nothing in between:

- **Intake accepts it.** `WorkItemCreate.submodules` and `root_merge_policy`
  (`api.py:487`, `:489`), cited to "design 1g Advanced · cross-repo".
- **The store persists it.** `store.create_work_item` writes both columns
  (`store.py:108`); `db.py:40` documents the column.
- **The executor threads it.** `executor.py:181`, `:235` — signature to store, no
  further reader.
- **The UI renders it.** `store.repos_for` (`store.py:133`) sorts declared paths by
  depth and hands `api.py:989` a repos panel — but derives each repo's `state` from
  nothing more than whether the item's `merge` node completed. There is no real
  per-repo MR state to report, because no per-repo MR is ever opened.
- **`work_item_repos` does not exist.** `grep -rn work_item_repos src/` → no hits.
  The table the design makes load-bearing was never created.

So `submodules` is a value Kraft stores, shows a panel for, and never acts on. The
work item in question had `submodules: None` — but setting it would have changed
nothing, because there is no consumer. That is the honest statement of the gap: not
"the feature has a bug", but "the feature's execution path was never built, while its
intake and its UI ship".

## Impact

Silent, and worse than a failure. A chain that cannot open a submodule MR should stop
and say so. Instead:

- `verify` passes — the submodule's own suites are genuinely green.
- `_assert_clean` passes — root status is clean, per `ignore = all`.
- `open_mr` succeeds — it opens a root MR whose diff looks plausible at a glance,
  because plan/session/spec documents and any root-level plumbing do live at the root.
- `mr_checks` polls that root MR's CI, which can legitimately go green.
- The deliverable sits on an unpushed local branch inside a submodule of a **throwaway
  worktree**, which `git worktree prune` is entitled to destroy.

A reviewer opening the MR sees documents describing a change the MR does not contain.
The failure mode is a work item that reports success while the work is one prune away
from gone.

## Reproduction

1. A repo with at least one submodule, `submodule.<path>.ignore = all` in its config
   (or simply a submodule whose pointer the item is told not to bump).
2. File a work item whose changes are entirely inside that submodule.
3. Let the chain run to `open_mr`.
4. Observe: root MR opened, root diff carries no submodule change,
   `git -C <submodule> ls-remote origin "kraft/*"` empty, no node ever failed.

Evidence for the real occurrence is in `~/.kraft/run/logs` for item
`9d0ab38ff3c9439b90506df0f6966660`: session `7ad0556c` (`open_mr` success, root MR
!13), and the submodule commit that never left the worktree. That occurrence is
machine-specific and not part of this repo's test suite; verification below instead
builds a disposable fixture repo with a real submodule, so the regression test runs
in CI rather than depending on that one workspace.

## What to build

Design 06 §2–§3a is the specification; this is its implementation, as one work item.

- **`_assert_clean` (`forge.py:196`)**: pass `--ignore-submodules=none` on the status
  call, so a submodule with new commits or a dirty tree is reported as uncommitted
  work that would not reach the MR, regardless of repo config. Note in the docstring
  that the flag is deliberately overriding the repo's own `ignore` setting, and why.
- **Commit identity**: a submodule's gitdir under
  `.git/worktrees/<id>/modules/<path>` does not inherit the superproject's
  `user.name`/`user.email`. On the real item this produced commits authored
  `Kraft Agent <kraft@local>` and four `pre-receive hook declined` push rejections
  before an agent worked around it itself. `ensure_worktree` (`builtins.py:112`)
  must pin identity into every worktree it creates, submodules included, and fail
  worktree creation when it cannot be resolved. (Tracked and implemented separately
  as Kraft-cppp/`Kraft-mxdx`, landed ahead of this item — this item builds on it
  rather than re-implementing it, and extends the same pin to submodules discovered
  by the §3a scan below, not only ones declared at intake.)
- **`work_item_repos` table**, per §2.2: `work_item_id`, `repo_path`, `role`,
  `submodule_path`, `merge_rank`, `bead_id`, `mr_ref`, `merge_state`.
  `store.repos_for` then reports real state from these rows instead of deriving a
  placeholder from `merge` completion — the panel already exists and starts telling
  the truth for free.
- **Worktree setup** in `ensure_worktree` (`builtins.py:112`): resolve the submodule
  set (declared at intake only — never "all submodules", per §3 step 2),
  `git submodule update --init -- <paths>` selectively, then `git worktree add` inside
  each on the item's branch, writing one `work_item_repos` row per repo with
  `merge_rank` = depth (root last).
- **Undeclared-submodule detection** (§3a): a diff scan at the end of the
  implementation node — `git submodule summary` plus a per-submodule diff — adds a
  `work_item_repos` row and recomputes `merge_rank` for any submodule the agent
  touched but the item never declared. This is what would have rescued the real item,
  which declared nothing. The scan pins commit identity into that submodule's gitdir
  at detection time too (same resolve-or-fail guard `ensure_worktree` runs for
  declared submodules) — a submodule found this way must not be exempt from the
  identity guarantee just because nobody named it up front.
- **Forge handlers per repo**: `open_mr`, `find_mr`, `push`, `ci_status`, `merge`
  iterate `work_item_repos` by `merge_rank` (deepest first) instead of taking a single
  `repo: Path`. Each repo's MR ref and state live on its row. Before opening the
  root MR, refuse when any initialized submodule holds commits not covered by a
  `work_item_repos` row — the §3a scan above runs first specifically so this can
  never fire on a submodule the scan already found and is about to open an MR for;
  it fires only on the case the scan itself misses (submodule init failed, `git
  submodule summary` errored), which should stop the chain rather than silently drop
  the change. The message names the submodule path and its branch.
- **`mr_checks` goes sequential, not parallel**: for each repo in `merge_rank` order,
  open (or find) its MR, poll to `checks_green`, then merge, before starting the next
  repo. The node completes only once every repo has merged in rank order; a
  `capped_out` on any one repo fails the node there, leaving repos already merged as
  merged and repos not yet reached untouched. This keeps the failure story simple —
  one stuck repo, one clear stopping point — at the cost of wall-clock time versus
  polling all repos concurrently.
- **Branch naming**: the same branch name is used in the root repo and every
  submodule. This already happens by accident today (the agent branches itself
  inside the submodule using the item's branch); this design makes it the explicit
  contract `work_item_repos` and the forge handlers rely on.
- **Root policy** (§2.3, §3a): because merge is sequential and deepest-first, by the
  time the root repo's turn comes every submodule under it has already merged to its
  target branch. Under `bump`, the root's pointer commit therefore references each
  submodule's **post-merge target-branch HEAD** — a commit that has actually landed —
  never a feature-branch SHA, which a squash merge on the submodule's own MR could
  invalidate. A root repo with no changes of its own carries nothing but that pointer
  commit. `bump_no_mr` produces the same pointer commit without opening a root MR;
  `skip` touches the root not at all — this is the case the real item asked for
  ("do not bump the workspace submodule pointer as part of this change").

## Verification

- Unit: `_assert_clean` on a fixture superproject with `ignore = all` and a committed
  submodule change → raises, naming the submodule path. This is the regression test
  for the silent case.
- Unit: `ensure_worktree` on a fixture with one declared submodule → submodule
  worktree exists, on the item's branch, with a resolvable commit identity; a repo
  with no resolvable `user.email` → worktree creation fails with a message naming the
  missing key.
- Unit: the §3a diff-scan step, on a fixture where the agent touched an undeclared
  submodule → a `work_item_repos` row is added, `merge_rank` recomputed, and the
  submodule's gitdir carries a pinned identity.
- Unit: `store.repos_for` ordering — deepest submodule first, root last — against
  `work_item_repos` rows.
- End-to-end under `just dev` with a seeded superproject-plus-submodule fixture built
  by the test itself (not the real `wfm-workspace` checkout, which is
  machine-specific): an item whose only change is inside the submodule opens an MR in
  the submodule, and under `root_merge_policy: bump_no_mr` opens none at the root.
  This fixture is the standing regression test for the shape that broke on
  `9d0ab38ff3c9439b90506df0f6966660` — it exercises the same structure without
  depending on that one workspace ever existing again.

## Open questions resolved during design

1. **Multi-MR status reporting** — sequential, not parallel. See "Forge handlers per
   repo" / "`mr_checks` goes sequential" above.
2. **Branch naming across repos** — confirmed as the explicit contract: same name in
   root and every submodule.
3. **Root pointer SHA under `bump`** — resolved by the sequential merge order itself:
   always the submodule's post-merge target-branch HEAD, never a pre-merge SHA.
