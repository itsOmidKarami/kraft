# Closing the dogfooding gaps

Kraft cannot yet run its own development cycle. A chain ends at a local diff:
the back half of `default.yaml` — `open_mr`, `mr_checks`, `merge` — is
`builtin:noop`, so a human opens the merge request and merges by hand. Around
that sit three smaller obstacles: the shipped policy has no spend cap, there is
no way to abandon a work item or reclaim its worktree, and the concurrency limit
only governs autonomous intake.

This design closes all four, then proves them with a live run against this
repository.

Beads: Kraft-33j (forge adapter), Kraft-9oq (unlimited spend), Kraft-x85
(abandon), Kraft-n2d (concurrency), Kraft-krd (stale install has no version
signal).

## 1. Forge protocol and backends

New module `src/kraft/adapters/forge.py`.

```python
class Forge(Protocol):
    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR: ...
    async def ci_status(self, *, repo: Path, mr: MR) -> CIStatus: ...
    async def merge(self, *, repo: Path, mr: MR) -> None: ...
```

`MR` and `CIStatus` are frozen dataclasses. `MR` carries the number and the web
URL. `CIStatus` carries `state` (`pending` | `success` | `failed`), the pipeline
URL, and a per-job summary the gate renders.

Three implementations:

- `GlabCli` — shells `glab`. The backend this repository runs against; `origin`
  is `git@gitlab.com:itsOmidKarami/kraft.git`.
- `GhCli` — shells `gh`. For the public repository after the v0.1.0 split
  (Kraft-5fx).
- `FakeForge` — in-memory, for tests. Returns scripted CI states with no
  network and no tokens.

**No auto-detection.** The backend is named in `templates/registry.yaml`. A
backend whose CLI is absent, or present but unauthenticated, fails loudly at
dispatch. Probing for an available CLI would mean the same work item takes a
different path on a laptop than in a container, and a bug that reproduces on one
and not the other.

**No MR-id plumbing between nodes.** The worktree sits on branch
`kraft/<work_item_id>`, and both CLIs resolve the merge request from the current
branch. `MR` is derived at each call, not persisted and threaded through the
chain.

A direct-API backend (`GitLabApi`, `GitHubApi`) is explicitly deferred. It
requires Kraft to handle a token — where it is read from, that it never reaches
a log, an event payload, or a worker session's environment — which is real
security-adjacent work with no consumer today. The environments that would need
it are Kraft-rki (container isolation) and Kraft-gzp (non-developer install),
both still open. It gets built when one of them lands and can state its
requirements, not guessed at now. Adding it is a new class satisfying `Forge`,
not a refactor.

## 2. Dispatch and chain bindings

`executor._dispatch` grows a `kind: forge` branch alongside `subprocess`, with
`handler: open_mr | ci_poll | merge`.

Forge tasks record their sessions through `builtins._record_done`, which today
hardcodes a `"done"` status. It grows a `status` parameter. This is load-bearing
rather than cosmetic: a red pipeline must record `failed` so the item lands in
`needs_human`, instead of passing as done and letting the chain proceed into the
merge node.

`templates/registry.yaml` replaces four of its seven placeholder bindings —
`on.mr.open`, `on.ci.poll`, `on.merge`, and `on.human_review.requested` (see §3).
`on.review.mr.run` stays `builtin:noop`; that is Kraft-pl7 and out of scope here.

**Red CI stops at the human.** It does not enter `verify_fix_loop` in this pass.
A failing pipeline can mean a flaky runner, an infrastructure fault, or a real
regression, and spending fix attempts before a human has looked spends tokens on
the two cases where fixing is the wrong response.

## 3. The `human_review` gate artifact

`on.human_review.requested` becomes an agent task with `artifact: review_brief`,
backed by a fourth packaged skill in `src/kraft/skills/` beside `spec`, `plan`,
and `chain-review`.

It must be an agent task rather than a forge or subprocess one: artifact
registration exists only on the agent path (`adapters/agent.py:227`), and the
gate needs a document for `kraft artifact` to print.

The skill reads the CI status, the merge request URL, and the local review
findings, and writes the brief a human reads before approving the merge.

## 4. Spend caps, abandon, concurrency, version

**Spend caps (Kraft-9oq).** `templates/policy.yaml` ships
`budget.work_item_usd: 10` and `budget.daily_usd: 50` in place of `null`. The
enforcement already exists (`policy.py`, `executor._budget_breach`); only the
defaults change. The seeded copy at `$KRAFT_HOME/templates/policy.yaml` must be
updated too — it is written once on first run and never overwritten, so a change
to the packaged template does not reach an existing installation. That gap is
Kraft-ouo and is not solved here, only worked around.

**Abandon (Kraft-x85).** `POST /work-items/{wid}/abandon`. Refuses while the
item is `active` — pause first, so the endpoint never races a running agent —
sets a terminal `abandoned` status, and removes the worktree and the
`kraft/<id>` branch. Exposed as `kraft abandon [ID]`, which requires
confirmation because it destroys uncommitted work in the worktree. `kraft list`
hides abandoned items unless `--all`.

**Concurrency (Kraft-n2d).** The slot count at `intake.py:71` moves into one
shared check that counts every active work item, not only autonomously picked
ones. `templates/intake.yaml` default rises from 1 to 3. A manual start that
would exceed the limit is refused with the count, rather than silently
over-subscribing.

**Version (Kraft-krd).** `kraft --version`, reading installed package metadata.
The tree's `cli.main` already rejects unknown subcommands; the observed
fall-through to `serve` came from an installed build predating argparse, which
no version signal made visible. The flag is the fix; `pyproject.toml` moving off
`0.0.0` is Kraft-5fx.7.

## 5. Testing

`FakeForge` makes the whole back half testable without network, tokens, or a
live pipeline: the poll loop, the `failed` status path on red CI, and the gate
brief.

- `tests/test_forge.py` — the three backends against a stubbed CLI, and
  `FakeForge` directly.
- `tests/test_e2e.py` — a full chain, spec through merge, against `FakeForge`.
- Existing `tests/test_budget.py` covers enforcement; it gains a case pinning
  the shipped defaults, so a future edit to `policy.yaml` that removes the caps
  fails a test rather than silently shipping unlimited spend.
- Abandon and concurrency get cases in `tests/test_api.py`.

## 6. The live run

A small real bead, run through a reinstalled `kraft` with the real `claude`
CLI, against `origin` on GitLab, with every gate driven from the command line.

This step opens a real merge request on gitlab.com and can merge to a real
branch. Both are outward-facing and are confirmed with the human at the time —
approval of this design is not approval of either.

## Out of scope

- `GitLabApi` / `GitHubApi` direct-API backends (deferred, see §1).
- `on.review.mr.run` — Kraft-pl7.
- Upgrading an already-seeded templates directory — Kraft-ouo.
- Looping red CI back into a fix cycle (§2).
