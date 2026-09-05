# Competitive gap closure: roadmap

Date: 2026-09-04
Beads: Kraft-8mu (epic), Kraft-8mu.1 … Kraft-8mu.7

## Problem

Kraft was compared against Kiro (AWS), DeepSeek Harness, Auto-Company, and the
Vibe Kanban / Conductor class of board-in-front-of-your-CLI orchestrators. Nine
capabilities those tools have and Kraft does not were judged worth closing.

The target changed the shape of the list. Kraft is to be a real product other
people install, running locally but reachable from off the machine by its single
user. That makes two things adoption blockers rather than niceties: a forge that
is not hardwired to GitLab, and some way to be told that a run stopped when you
are not looking at the screen.

A second pass compared Kraft against the `superpowers` skills themselves —
`subagent-driven-development` in particular, which runs the same
implement / review / fix-loop cycle Kraft's chains do, and has run it against
real work long enough to have paid for its mistakes. Six more items came out of
that, two of which answer questions Kraft's own design documents deferred for
want of exactly that track record.

This document decomposes the fifteen into seven sub-projects and fixes their
order. It is not itself a design. Each sub-project gets its own spec.

## What Kraft already has that the field does not

Recorded so no sub-project accidentally trades it away:

- **Chain templates as versioned, diffable YAML.** Kiro's specs are per-feature
  and agent-generated. Kraft's process is a file you can revert.
- **Bounded autonomy as policy.** `policy.py` caps attempts and wall-clock, and
  `capped_out` is a distinct terminal status from `failed`. Vibe Kanban and
  Conductor simply hang; Auto-Company has circuit breakers but no per-loop caps.
- **Gates as first-class objects** at node boundaries, with reject-and-re-plan.
- **Cost and token analytics** per node, repo, and week (`analytics.py`).
- **Cross-repo artifact search**, FTS + vector (`index/`).
- **The context-injection boundary** — process context reaches agents only via
  the per-invocation system prompt, never via `CLAUDE.md` or any repo file.

Two claims that do **not** hold up and should not be repeated in any pitch:

- Multi-repo and submodule support is **designed, not built**. There is no
  `work_item_repos` table in `db.py`; `06_cross_repo_federation.md` is a design
  document with no implementation behind it.
- Every MR, CI, and remote-review hook in `templates/registry.yaml` is bound to
  `builtins.noop`. Kraft does not currently talk to a forge at all.

That second fact is what makes the ordering below work.

## The fifteen, grouped into seven

Grouped by what they touch, because three of the fifteen rewrite the same function
and three more rewrite one loop.

| # | Sub-project | Contains | Bead |
|---|---|---|---|
| D | Forge boundary | GitHub support / forge abstraction | `Kraft-8mu.1` |
| A | Gate review | Diff at human gates | `Kraft-8mu.2` |
| C | Agent invocation contract | Per-node model, permissions/sandbox, steering | `Kraft-8mu.3` |
| B | Away from desk | Notifications out, phone-shaped UI | `Kraft-8mu.4` |
| E | Budget and autonomy | Spend caps, auto-intake | `Kraft-8mu.5` |
| F | Findings contract | Severity gate, finding carry-forward, reviewer contract, review package | `Kraft-8mu.6` |
| G | Worker report contract | `done_with_concerns`, `needs_context`, prior-attempt handoff | `Kraft-8mu.7` |

**Order: D → A → F → C → G → B → E.**

`B` is blocked by `A`; `E` is blocked by `C`; the pre-existing `Kraft-n70`
(Codex support) is blocked by `C`. `D`, `A`, `F` and `G` are independent of
everything and of each other — `F` and `G` each carry one child task that is
not (`Kraft-8mu.6.1` behind `A`, `Kraft-8mu.7.1` behind `C`).

### D — Forge boundary (first, because it is cheap only right now) — **LANDED**

> Shipped in `e9fd1e6..404aa70`, bead `Kraft-8mu.1` closed. `forge` + `project`
> replace `gitlab_project` across the probe, the tolerant reader, the `/repos`
> API and the Settings readout. No forge client, per the non-goals. The section
> below is the reasoning as written before the work; the file:line citations in
> it describe the code as it was.

`03_plugin_adapters.md:14` already specifies `HttpClientAdapter` as generalized
rather than a one-off GitLab client, so the adapter layer is not the problem.
The leak is above it, in three places that name GitLab concretely:

- `config.py:130` — `probe_repo` string-matches `"gitlab"` in the origin URL and
  returns a `gitlab_project` field.
- `api.py:951` and `api.py:1005` — `RepoBody` and `RepoPatch` carry
  `gitlab_project`; `add_repo` persists it into `repos.yaml`.
- The Settings repo form, which renders that field by name.

Doing this before the MR/CI plugins exist costs one schema change to
`repos.yaml` and one probe rewrite. Doing it after costs a migration of every
user's connected repos plus a rewrite of whichever forge plugin shipped first.
Nothing else on this list has that property, which is the entire argument for
putting the smallest sub-project at the front.

**Spec must settle:** whether the forge is a per-repo field or a plugin
selected in `registry.yaml`; what a forge-agnostic repo identity looks like
(`{forge, project}` vs. a URL); whether `repos.yaml` gets a migration or a
tolerant reader; whether GitHub is implemented now or only made possible.

**Non-goals:** implementing the CI/MR plugin itself; Bitbucket; Gitea.

### A — Gate review (the one that fixes the central feature)

`Gate.tsx` already takes an `artifact` prop — the slot for "the one linked
artifact the decision is about". Today a `human_review_approval` gate offers
Approve and Reject with nothing to look at, so the decision is made blind or
made somewhere else.

The escape hatch is `POST /work-items/{wid}/open-worktree`, whose own docstring
concedes the problem: "the path means nothing to a browser on another machine".
Once Kraft is reachable remotely (the stated target), that hatch closes
entirely. A served diff is the only review path that survives.

The mechanics are in place: `builtins.env_setup` creates the worktree at
`run_dirs.worktrees / work_item_id` on a branch named `kraft/{work_item_id}`, so
a diff is one `git diff` away.

**Settled by the spec:** the base commit is pinned at `env_setup` time in a new
`work_items.base_ref` column, because a merge-base recomputed later moves when
the default branch moves. The diff is the **working tree against that base** —
not `<base_ref>...HEAD`, which this roadmap first recommended and the spec
rejected: an agent that wrote files without committing them is the normal
mid-chain state, and a committed-only diff shows an empty change set while the
work sits on disk. Whole-item scope, 1 MB cap cut at a file boundary, and a
`DiffModal` sibling to `DocumentModal` rather than a mode of it.

**Non-goals:** inline comments on the diff; editing from the viewer; three-way
merge conflict resolution.

### C — Agent invocation contract (largest, and a rule change)

Per-node model choice, agent permissions/sandboxing, and repo-standards steering
are one spec because all three change the same thing: what
`adapters/agent.py` puts into a launch. Specified separately, the same launch
path gets rewritten three times and the three features fight over precedence.

- **Model** is nearly free — `registry.yaml` hook entries gain a `model:` key,
  passed through as `--model`. `worker_sessions.model` is already a column, so
  analytics picks it up with no further work. This is the lever that makes the
  existing cost tracking actionable: a cheap model for `on.ci.poll`, an
  expensive one for `on.implementation.start`.
- **Permissions** is the adoption blocker. Kraft hands an agent a worktree and
  trusts it. Kiro ships permissions and `kiroignore`; Auto-Company enforces hard
  constraints against force-push and credential leaks. For unattended runs on
  other people's repositories, "we trust the agent" is not a shippable answer.
- **Steering** conflicts with an existing decided rule. The context-injection
  boundary bans repo files as a context channel on purpose. Adding steering does
  not overturn that — it supplies the missing other half, an authored,
  Kraft-owned place for per-repo standards that reaches the agent through the
  per-invocation system prompt. The spec must say this explicitly, or the next
  reader will think the boundary was forgotten rather than honored.

**Spec must settle:** precedence when a template, a registry entry, and a repo
all specify a model; what the permission unit is (tool allowlist, path
allowlist, or both) and whether it is enforced by Kraft or delegated to the
agent CLI's own flags; where steering text lives (`templates/steering/*.md` vs.
a `repos.yaml` field) and how it is scoped per hook point; what happens to a
launch when the requested model is unavailable.

**Non-goals:** containerizing the agent — `2026-09-04-packaging-and-dev-execution-design.md`
already rejected Docker on the grounds that Kraft shells out to CLIs holding
host credentials, and that reasoning still stands; a permission-prompt UI that
asks the human mid-run (that is what gates are for).

### B — Away from desk (notifications + phone UI)

One story, not two features: a gate fires, the phone buzzes, the gate is
approved from the phone. Split apart, each half ships something unusable alone —
a notification you cannot act on, or a mobile screen you never know to open.

Plumbing exists. `api.py:147` already fans every commit out to the WS
broadcaster and the indexer; a notifier is a third subscriber on that same
callback. Events are generic `{type, payload, work_item_id, seq}` rows, so the
trigger set is a filter over `type`, not new instrumentation.

The UI half is real but small: `styles.css` has three media queries in total.
Only two screens need to work on a phone — the board, and the gate
approve/reject — plus whatever `A` builds, which is why `B` waits for `A` rather
than making the diff viewer responsive twice.

**Spec must settle:** which event types notify (recommendation: `gate_requested`
and cap breach only — anything more and the notifications get muted, which is
the same as not having them); the delivery channel, given that the server may be
running on a machine the human is not at, so an OS notification is insufficient
by itself and a webhook or push service is required; whether notification config
lives in `access.yaml` or a new `notify.yaml`; secret storage for a webhook URL
or push token, which is the first secret Kraft would hold besides the password
hash.

**Non-goals:** email; a native mobile app; inbound control (replying to a
notification to approve) — the notification carries a link.

### E — Budget and autonomy (last, largest blast radius)

**Spend caps** reuse the existing machinery but not the existing shape.
`worker_sessions.cost_usd` is populated per session, so an item's spend is a
`SUM` away — but the spec puts the limit in a `budget:` block beside `loops:`
rather than as a third field on `policy.Cap`, which this roadmap first suggested.
`Cap` is per-loop and stored per `(work_item_id, key)` in `retry_counters`; a
spend limit spans every loop, and forcing it into that row means writing the same
limit into every counter and then reconciling them.

One hard constraint the spec must state rather than discover: `usage.py:91`
reads cost from the agent's log envelope **after the session exits**. A spend cap
can therefore refuse to launch the next task; it cannot interrupt a running
agent. A cap of $20 means "stop once we notice we passed $20", and the spec must
say so in those words or it will promise a ceiling it does not have.

**Auto-intake** goes last on purpose. Starting work unattended from `bd ready`
is the only item on this list that increases blast radius, and it is only
defensible once `C` has given the agent a sandbox and this sub-project has given
it a budget ceiling. It also sits closest to Kraft's thesis boundary: bounded
autonomy with a human at the gates. The spec should treat "how much can start
without a human" as its central question, not an afterthought.

**Spec must settle:** cap scope (per work item, per day, per repo, or all
three); what a breach does — recommendation is `needs_human` with a distinct
reason, reusing the `capped_out` escalation path rather than inventing a
terminal state; how auto-intake picks among ready beads; the concurrency
ceiling; whether an auto-started item is allowed to pass its own gates (it must
not).

**Non-goals:** per-token billing integration with a provider account; forecasting
spend before a run; auto-intake picking up work from anywhere but `bd ready`.

### F — Findings contract (borrowed, and it closes two open questions)

The fix loop is built and works (`executor.py:221-323`) but branches on task
*status* only. `_FIX_PROMPT` interpolates failed hook-point **names** —
*"Failing hook points: on.test.run"* — the comment at line 323 reads
`# fix task status is not branched on`, and `grep -rn "Finding" src/` returns
nothing. The `Finding{}` schema in `03_plugin_adapters.md` §4 and the
"structured failure payload" in `02` §7.2 are design, not code.

So every finding is fatal, the loop cannot see itself repeating, and the fix
agent starts each cycle holding a hook-point name.

`subagent-driven-development` answers all three, and its answers are worth
taking because they were paid for. Its re-review template verdicts **each
finding** ADDRESSED or NOT ADDRESSED; its skill body routes Minor findings out
of the loop into a roll-up the final review triages, with the rule that makes
that safe — *"a roll-up nobody reads is a silent discard"*.

Two of Kraft's own deferrals fall out of this:

- `02` §13 defers a severity threshold. SDD's answer: Critical and Important
  enter the loop, Minor is deferred to a list rendered at the `human_review`
  gate.
- `02` §14 defers "no-progress early escalation" in as many words — *"defining
  'no progress' (same failures? same files touched? diff churn without green?)
  needs a track record"*. With per-finding identity the definition is available:
  **the same loop-eligible fingerprints survived a full fix cycle.** Not churn,
  not files touched — the reviewer said the same thing twice about code that
  changed in between.

**Spec settles:** the `findings[]` result-file contract; a fingerprint that
excludes `line`, because the fix task moves every line below its edit; carry-
forward over the `events` log rather than a new table; and the reviewer prompt
contract, including SDD's prohibition on pre-judging — *"if the prompt you are
writing contains 'do not flag' — stop"*.

**Non-goals:** building a review plugin (`on.review.local.run` is still
`noop`); a findings table; SDD's ledger, which `events` already is.

### G — Worker report contract (borrowed)

`adapters/subprocess.py:33` accepts exactly two agent-chosen statuses:

```python
return status if status in ("done", "failed") else "failed"
```

An agent that finished but doubts the work must claim `done`. An agent that was
never told something must claim `failed` — which inside a `fix_loop` burns a
cycle for work it never attempted. SDD's implementer template makes this the
worker's first-class output with four statuses and the instruction that names
the failure: *"Never silently produce work you're unsure about."* Kraft has one
of the four already — `plan_diverged` is `BLOCKED`.

`needs_context` is nearly free because Kraft already has the answering
machinery: `POST /steer` stores the note, `resume` takes it with `take_steer`,
and `_STEER_PROMPT` prepends it to the relaunch. A stopped agent asking a
question and a human answering through the steer box is the same transaction
Kraft already runs.

**Spec settles:** the two statuses and their migration; that
`done_with_concerns` changes no control flow; that `needs_context` does not
consume a fix cycle, on the same reasoning `02` §7.2 already applies to
`plan_diverged`; and handing the previous fix attempt's result file over **by
path, not by content** — SDD's *"everything you paste stays resident in your
context"* costs Kraft tokens on every cycle of every work item.

**Non-goals:** resuming a live agent process; a mid-run question channel; model
escalation on later cycles (`Kraft-8mu.7.1`, behind `C`).

## Sequencing summary

```
D ──────────────────────────────────►  (independent, do first: cheap only now)
A ──────────────┬───────────────────►  (independent)
                ├──► B                 (needs A's diff viewer)
                └──► Kraft-8mu.6.1     (review package, needs A's base_ref)
F ──────────────────────────────────►  (independent; answers §13 and §14)
C ──────────────┬───────────────────►  (largest single spec)
                ├──► E                 (needs C's sandbox)
                ├──► Kraft-8mu.7.1     (model escalation, needs C's model)
                └──► Kraft-n70         (Codex support, pre-existing)
G ──────────────────────────────────►  (independent)
```

`F` and `G` both touch `executor.py`'s fix loop and both want
`store.sessions_for_round`. Their plans duplicate that five-line query
deliberately rather than sequencing the two — whichever lands first adds it.

## What is deliberately not on this list

- **Persona agents** (Auto-Company's 14 archetypes). Theater, not signal.
- **Becoming an IDE** (Kiro). Wrong shape for a local orchestrator.
- **A plugin marketplace or a Cordis-grade plugin runtime** (DeepSeek Harness).
  Three adapter kinds and seven first-party plugins is the right amount.
- **Checkpoints and rewind** (Kiro). The unit of work is a node holding a git
  worktree; git already rewinds.
- **A cloud or Kubernetes runtime** (OpenHands). Off-thesis. Kraft is local.
- **Multi-user or team tenancy.** Single user, own machine, reachable remotely.
  If this changes, access control moves ahead of all seven sub-projects.

And from `subagent-driven-development` specifically, deliberately not ported:

- **The ledger** (`progress.md`). It exists because a controller's context dies
  at compaction. Kraft's `events` table is durable, ordered and queryable —
  copying a markdown ledger into a system with an event bus is a regression.
- **Rulings.** SDD's controller decides alone and confesses afterward *because
  it cannot stop*. Kraft stops at gates; that is the product. A ruling concept
  would be an autonomy Kraft has deliberately declined.
- **The five-round cap** as a constant. Kraft's caps are per-loop, in
  `policy.yaml`, snapshotted per counter row.
- **Adjudicate-only-at-the-cap.** That rule stops a controller short-circuiting
  its own loop. Kraft's loop is run by a policy engine that cannot rationalize.
