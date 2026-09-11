# Conceptual Model

> **Status:** historical design record. It captures why the system was designed
> this way, and is not maintained against the code. Current intended behaviour
> lives in `docs/intent/` (today: `gates` only).
> Companion documents: `02_orchestrator_core.md`, `03_plugin_adapters.md`,
> `04_indexer_search.md`, `05_ui.md`, `06_cross_repo_federation.md`. Start with
> `00_overview.md` for reading order and glossary.

## 0. Purpose

The conceptual model for a personal, plugin-based, semi-autonomous software
engineering workflow system. It defines the layered architecture, the flow model,
the plugin contract, the human gates, and the bounded-autonomy rules that every
component document refines.

The next step after this model is decomposing it into buildable pieces (§12); each
piece has its own component document.

---

## 1. Design Principles

1. **Solo-first.** One user, ~10 repositories, work sometimes spans repos, including
   via git submodules.
2. **Everything is a plugin.** The orchestrator core hardcodes no vendor — not beads,
   not superpowers, not Claude Code, not Codex. It defines hook points; plugins fulfill
   them. Today's defaults are chosen because the current process already works well —
   a head start, not a lock-in.
3. **Git-native source of truth, local derived index for discovery.** Work-graph state
   and artifacts (specs, plans, reviews) live in each repo, versioned with the code. A
   separate local index — rebuildable, disposable — is what makes them searchable as
   one thing across repos.
4. **The app's process never leaks into the repo.** Steering/process context is
   injected per-invocation (system prompt, MCP config) into agent sessions the
   orchestrator itself starts. It is never written into `AGENTS.md`, `CLAUDE.md`, or
   any other ambient, repo-owned file.

   This governs what Kraft's *executor* injects into sessions it starts: that
   context is per-invocation and is never persisted to a repo-owned file. Files a
   human explicitly opts into by running `kraft init --repo` are that human's
   choice, not Kraft leaking its process into a repo — the distinction is ambient
   versus opted-into. See
   `docs/superpowers/specs/2026-09-05-agent-integration-design.md` §8.1.
5. **Bounded autonomy.** Every fix/retry loop has an attempt cap and a wall-clock cap,
   enforced by the core, not by individual plugins. Hitting a cap halts the loop and
   escalates to the human with the full trace, rather than looping forever or failing
   silently.
6. **Few, meaningful pauses.** Default human gates: spec approval, plan approval, chain
   finalization, human review approval (the final gate before merge). Everything
   between "plan approved" and "ready for final review" runs unattended.
7. **Polling for what we don't control, live for what we do.** The orchestrator wakes
   on an interval to check state it doesn't own (CI, MR review, bead status) — no
   public endpoint required. Worker sessions it holds open itself push status/event
   updates live over the event bus as they happen. This is about status/event
   delivery, not raw output — output itself is disk-redirected and pulled on request,
   never piped into the orchestrator or streamed live (`02_orchestrator_core.md` §6,
   §10.1; `05_ui.md` §6).
8. **Local web app, no editor fork.** The UI is a locally-served web app opened in a
   browser. It doesn't replace the code editor.
9. **Unattended stretches are interruptible.** Phase-boundary gates (§8) are scheduled
   pauses. A pause/steer control is available at any point during an unattended
   stretch too — if something looks off, stop and redirect now rather than waiting for
   the next scheduled gate.

---

## 2. Layered Architecture

```
┌────────────────────────────────────────────────────────────┐
│                         UI LAYER                            │
│  local web app — board, search, spec/plan viewer,           │
│  approve/reject gates, live worker status                   │
└───────────────────────────┬────────────────────────────────┘
                            │ local API (HTTP/WS)
┌───────────────────────────▼────────────────────────────────┐
│                    BACKEND / ORCHESTRATOR                   │
│  ┌────────────┐   ┌──────────────┐   ┌───────────────────┐  │
│  │   Chain    │   │   Plugin     │   │   Policy Engine   │  │
│  │  executor  │   │  registry    │   │ (retry/wall-clock │  │
│  │ + event bus│   │ + dispatch   │   │  caps, gates,     │  │
│  │            │   │ + validator  │   │  backward-motion) │  │
│  └────────────┘   └──────────────┘   └───────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │       Indexer (watches all repos → local search DB)   │  │
│  └──────────────────────────────────────────────────────┘  │
└───────────────────────────┬────────────────────────────────┘
                            │ plugin calls (CLI / MCP / API)
┌───────────────────────────▼────────────────────────────────┐
│                          PLUGINS                            │
│  work-graph · planning · execution worker · review(s) ·     │
│  CI/MR · env-prepare · chain review — bindings in §5        │
└───────────────────────────┬────────────────────────────────┘
                            │ reads / writes
┌───────────────────────────▼────────────────────────────────┐
│                    SOURCE LAYER (per repo)                  │
│  .beads/ (work graph) · .engineering/{specs,plans,          │
│  reviews,sessions}/ · code · worktrees · git history        │
└────────────────────────────────────────────────────────────┘
```

Source-of-truth content flows upward through plugins into the backend's index.
Process/steering context flows downward from the orchestrator through plugins into
agent sessions only — never into the repos themselves.

---

## 3. Flow Model — Chains, Nodes, Hook Points

A work item's sequence of stages is a per-work-item **chain**, resolved from a
template at intake, not a single hardcoded phase graph.

### 3.1 Chain / Node / Task

```
Chain := ordered list of Node
Node  := { id, tasks: [hook_point, ...], gate_after: <gate_name> | null }
```

- A work item is always in exactly **one** node at a time — sequencing is linear.
- A node "completes" when every task in it has reached a terminal state (tracked
  per hook-point invocation in `worker_sessions` — `02_orchestrator_core.md` §4).
- A node may optionally end in a gate (§8), reusing the generic gate mechanism.
- This is deliberately **a chain, not a DAG.** No branching, no merging, no
  join-semantics. The only concurrency is multiple tasks inside one node (§3.3).

### 3.2 Templates

Templates are YAML, same tier as plugin-registry config — hand-authored, versioned,
not DB-mutated. A repo declares a `default_chain_template`; intake can override by id
(`POST /work-items`, `02_orchestrator_core.md` §10.1).

**`default.yaml`** — spec and plan included:

```yaml
id: default
nodes:
  - id: spec
    tasks: [on.spec.requested]
    gate_after: spec_approval
  - id: plan
    tasks: [on.plan.requested]
    gate_after: plan_approval
  - id: chain_review
    tasks: [on.chain.review_ready]
    gate_after: chain_finalized
  - id: env_setup
    tasks: [on.env.prepare]
  - id: implementation
    tasks: [on.implementation.start]
  - id: verify
    tasks: [on.test.run, on.review.local.run]   # concurrent, read-only
  - id: open_mr
    tasks: [on.mr.open]
  - id: mr_checks
    tasks: [on.ci.poll, on.review.mr.run]       # concurrent, read-only
  - id: human_review
    tasks: [on.human_review.requested]
    gate_after: human_review_approval
  - id: merge
    tasks: [on.merge]
```

**`quick-task.yaml`** — spec/plan already done or not needed: identical from
`env_setup` onward, with **no `spec`, `plan`, or `chain_review` node**.

"Simple task, skip to implementation" and "spec/plan already exist" are both just
template choices. No special-case logic in the executor: downstream behavior follows
from which nodes the loaded chain contains — e.g. chain review fires only when the
chain carries both a `plan` node and a `chain_review` node (§3.5).

For a multi-repo work item the `open_mr`, `mr_checks`, and `merge` nodes become
**repo-set-aware** — node-internal iteration over the involved-repo set, not chain
branching (`06_cross_repo_federation.md` §3).

### 3.3 Concurrency Rule (Read-Only Fan-Out)

A node's tasks may run concurrently **only if** every task is read-only against the
same frozen upstream artifact:

- `verify` — `on.test.run` (measurement only, no edits) + `on.review.local.run`,
  both read-only against the implementation diff.
- `mr_checks` — `on.ci.poll` + `on.review.mr.run`, both read-only against the open MR.

Under a multi-repo work item, both `verify` and `mr_checks` run their read-only
task(s) **once per involved repo**, concurrent across repos — each instance reads
only its own frozen diff/MR, so the rule holds unchanged, just with more concurrent
instances (`06_cross_repo_federation.md` §3.4).

Concurrent tasks that *write* to the same work item are out of scope. If
implementation work needs splitting across scoped writers, that split happens inside
the Execution Worker plugin's own team/multi-agent mode (§5), never at the chain
level. The chain mechanism carries no worktree-per-task machinery, conflict
resolution, or join semantics beyond "wait for all tasks in this node."

### 3.4 Chain Lifecycle

1. **Intake.** A template is resolved (repo default or explicit id) and materialized
   as the work item's own chain — the coarse, upfront choice.
2. Nodes execute in order. Hook points, gates, and retry policy behave as specified.
3. **Chain review** (§3.5) fires right after plan approval — *only* if the chain
   included a plan node — the one "refined later" adjustment point.
4. Everything from there runs against whatever chain review locked in.

### 3.5 Chain Review

`on.chain.review_ready` → `on.chain.finalized`, `gate_after: chain_finalized`.

Runs once, immediately after plan approval. It requires a `chain_review` node in the
loaded chain (that node is where `on.chain.review_ready` lives) **and** a `plan` node
before it — with no plan node there is nothing freshly produced to react to, so even
a chain that carries a `chain_review` node skips the step. Looks at the now-real spec
and plan and decides whether the *rest* of the chain still holds — add a
security-review task to `verify`, lighten `verify` for a trivial change, etc. Touches
only not-yet-executed nodes.

Kept as its own stage rather than folded into the plan hook's output: the
`writing-plans` skill is scoped to the implementation plan (files, code, tests) and
has no opinion on review/MR/CI structure. This adds a fourth scheduled gate against
principle #6 — a deliberate trade: deciding what work runs is as consequential as
approving the spec or plan.

If the loaded chain has no plan node, chain review does not fire. Ad hoc pause/steer
(§8) remains the fallback if the loaded chain turns out wrong anyway.

### 3.6 Non-Goals (v1)

- No arbitrary branching or merging — chains are strictly sequential node lists.
- No concurrent writes within a node, no worktree isolation, no chain-level merge-back.
- **No re-entry**, meaning `current_node_id` never moves backward to an earlier node.
  A node re-running its own tasks under a bounded counter (§9, backward-motion) is
  explicitly permitted and is *not* re-entry.
- ~~No on-the-spot chain composition~~ — **lifted** (UI handoff spec §8). Templates
  are still files selected by id at intake, and a live work item still runs the
  `chain_definition` it materialized at intake. What is now allowed is editing those
  files from Settings → Chain templates (design 5b): a structured editor over the
  same YAML, validated per repo on save. Still excluded: composing a chain at
  intake time, and raw YAML executed straight from a request.
- No planner agent generating chains. If template selection turns out too coarse,
  that is the natural opening for a planner later — proposing a chain in this same
  YAML shape for approval, not a different mechanism.

---

## 4. Plugin Contract

Every plugin declares:

- **id / version**
- **hook points** it binds to
- **input / output schema** per hook (e.g. a Review plugin takes a diff + context,
  returns a list of `Finding { severity, message, file, line, source_plugin }`)
- **config**: enabled per repo, timeout/budget overrides

Multiple plugins can bind to one hook (e.g. three review plugins) — outputs merge
through the shared schema, not concatenated as free text.

Plugins reach their tool however is native to it — CLI subprocess, MCP call, HTTP
API. The core cares only about the declared contract, not the mechanism.

**Agent-invoking plugins** (Planning, Execution Worker, Chain Review) must
additionally respect the context-injection boundary (§10): process context passed at
invocation time only, never written to a repo file.

---

## 5. Default Plugin Bindings

| Hook category | Default plugin | Notes |
|---|---|---|
| Work-graph | **beads** | Git-native, Dolt-backed, MCP server + CLI; also owns the hydration hub (`06_cross_repo_federation.md` §5) |
| Planning — spec | **superpowers**: brainstorming | Writes spec artifact |
| Planning — plan | **superpowers**: writing-plans | Writes plan artifact |
| Chain review | bespoke skill (new authored content) | Weighs finalized spec/plan against the loaded chain |
| Worktree / env | git worktree + selective submodule init | Custom, thin, deterministic |
| Execution worker | **Claude Code** (headless); Codex deferred | Runs superpowers subagent-driven-development; may delegate to native multi-agent/team mode for large decomposable tasks |
| Test runner | repo's own test command | Detected/configured per repo |
| Review — local | **ponytail-review** (over-engineering only) | Findings coerced to shared `Finding{}` schema |
| Review — MR | remote-review CLI bot | Findings mapped to `Finding{}` |
| CI/MR | GitLab API | Polled, not webhook |
| Artifact store | git-native markdown in-repo | `.engineering/{specs,plans,reviews,sessions}/` |
| Index / search | local SQLite FTS5 + `sqlite-vec` vector index | Derived, rebuildable; artifacts + session summaries only, no beads |
| Notification | local status view + OS notification | Slack/email pluggable later |
| Session capture & summarization | agent self-summarizes at session end (orchestrator-launched sessions) + fallback batch-summarizer for sessions swept from native per-tool history stores | Summary is indexed; raw transcript archived cold, unindexed, kept alive so the summary's pointer never dies |

---

## 6. Data Model — What Lives Where

- **Work-graph (beads):** identity, status, priority, dependencies, current node,
  repo/branch/worktree ref, chain-template id, and *pointers* (IDs, paths, commit
  SHAs) to artifacts and sessions. Never full content.
- **Artifact store (git files):** the actual spec/plan/review content, versioned with
  the code, under `.engineering/{specs,plans,reviews}/`.
- **Session summaries:** a short, structured artifact per session — decisions,
  rationale, open questions, related work items — generated by the agent itself as
  the last step of any orchestrator-launched session, or by a fallback summarizer for
  swept sessions. Stored at `.engineering/sessions/`, linked many-to-many to work
  items/topics, and this is what is indexed and searched.
- **Session archive:** the raw transcript, copied out of the native per-tool store
  (e.g. Claude Code's `~/.claude/projects/`) into durable cold storage — not indexed
  by default, kept alive so a summary's pointer back to the full conversation never
  dies.
- **Index (local, derived):** full-text plus semantic index over artifacts and
  session summaries, across every connected repo. Disposable — always rebuildable by
  re-scanning the source layer. **Beads are not indexed** — cross-repo bead queries
  run against the hydration hub (`06_cross_repo_federation.md` §5).

---

## 7. Cross-Repo & Submodules

**Built design: `06_cross_repo_federation.md`.** Summary:

- `bd federation` (peer-to-peer Dolt replication across orgs) is the wrong mechanism
  and is **out of scope**. A solo user on one machine is none of what it is for.
- Cross-repo linking uses beads' **multi-repo hydration** (`bd repo add` /
  `bd repo sync` into an aggregating hub) plus ordinary `parent-child` / `relates-to`
  / `blocks` links on the hydrated rows. There is no first-class "one work item, many
  repos" object — the model is an anchor bead plus linked beads, one per repo,
  queried together through the hub.
- The dominant case is **submodule-nested**: a superproject plus one or more git
  submodules as one worktree tree. Sibling top-level repos in one work item is not
  modeled. A single-repo work item is the degenerate case (one repo, `role='root'`).
- Submodule init is **selective**: `git submodule update --init -- <resolved paths>`
  for only the submodules a work item declares or is later found to touch — never
  `--init` across all submodules.

---

## 8. Human-in-the-Loop Gates

- **Scheduled gates** (default): spec approval, plan approval, chain finalization,
  human review approval — configurable per work item. A well-defined ticket might
  skip spec approval entirely, or run a `quick-task` template with no spec/plan/chain
  gates at all.
- **Ad hoc pause/steer:** available at any point during an unattended stretch, not
  just at scheduled gates — stop what's running, inspect it, redirect, resume.
  Mechanized in `02_orchestrator_core.md` §10.2 as: kill the current attempt,
  optionally carry new context into the next one.
- **Guidance gate:** triggered by plan drift — when implementation diverges from the
  approved plan (`on.implementation.start` returns `status: "plan_diverged"`), the
  work item stops and asks for guidance (`on.guidance.provided` resumes it) rather
  than auto-looping into re-planning. Under the chain model this is result-status
  driven, not a distinct hook — the old phase table's `on.implementation.plan_diverged`
  / `on.guidance.requested` names are retired.
  Automatic re-loop is a reasonable refinement later, once there is a track record of
  what drift looks like in practice.
- **Gate rejection behavior** (`02_orchestrator_core.md` §7.2, backward-motion):
  - `spec_approval` / `plan_approval` / `chain_finalized` — reject requires a
    `{ note }` body; the coordinator re-invokes that node's own producing hook with
    the note injected, bounded by a per-gate reject-loop counter. On cap breach →
    `needs_human`.
  - `human_review_approval` — reject requires a `{ note }`, sets `needs_human`, no
    counter, no auto-re-invocation. A rejected final review means redoing
    implementation and replaying `verify` → `open_mr` → `mr_checks` forward — real
    chain re-entry, and precisely the moment a human should stay in control.
- Everything between "plan approved" and "ready for final review" runs unattended
  (subject to pause/steer), bounded by the retry policy in §9.

### 8.1 A trimmed gate must be self-backed

Kraft resolves references at one time and dereferences them at another —
steering names, skill names, artifact paths, intake attachments. When the
referent is gone by the time it is used, the rule is:

> **A missing reference may degrade what a human sees. It may never remove the
> human.**

Steering that cannot be read fails the node. A skill an agent cannot load stops
it with `needs_context`. A missing artifact still puts its gate to a person,
just without a document to read. All three degrade; none of them decide
anything on a human's behalf.

Where a decision made at validation time **cannot be un-made**, degrading is not
enough: the referent is copied into Kraft's own storage at the moment the
decision is taken. Trimming a gate is today's only such decision — an intake
attachment removes `spec_approval` or `plan_approval` from `chain_definition`
permanently — so intake copies the document into `run_dirs.attachments/` and
records that copy, rather than a path into a working tree Kraft does not own
(Kraft-eqgn). A reference that can rot must never be the thing a skipped human
decision rests on.

---

## 9. Bounded Autonomy / Retry Policy

- Every loop-bounded hook is wrapped by the core's policy engine — not by the plugin
  providing that stage.
- Cap = attempt count **and** wall-clock ceiling, whichever triggers first. Exact
  numbers are placeholders until tuned against real runs.
- On cap breach: the loop halts, the human is notified with everything attempted, and
  the work item sits in a clear `needs_human` state — never a silent failure, never
  an infinite loop.
- **Node-level fix loop** (backward-motion): a `verify` or `mr_checks` node whose
  measuring tasks come back non-clean may run **more than one cycle** of its own
  tasks — a fix task (`on.implementation.start`, fix-scoped) followed by a re-check —
  bounded by a new `<node>_fix_loop` counter owned by the policy engine's
  backward-motion coordinator. `current_node_id` does not move; this is an instance
  of the attempt+wall-clock principle above, not a revision of it. Gate rejection
  (§8) uses the same coordinator with `<gate>_reject_loop` counters.
- **Node-level repair pass** (`on_failure`, Kraft-rv6i): a node whose tasks fail
  may declare a second list of tasks that runs *once*, after the failure and
  before the item drops to `needs_human` — for a blocker that is not the code,
  such as a merge request missing a label its pipeline requires. The node then
  measures itself again, and only that second measurement decides: a repair task
  exiting 0 is not evidence that the thing it repaired is fixed. One pass, not a
  loop, and no counter of its own — a repair that did not take is a blocker Kraft
  does not understand, and stopping for a human beats pulling the same lever
  twice. Mutually exclusive with `fix_loop`, which is already its node's
  remediation. A `needs_context` question skips the repair entirely: it is
  addressed to a human and no task can answer it.
- Cost/token usage is logged per work item from the start, even before hard budgets
  are enforced — parallel/team execution can get expensive fast, and the data should
  exist before the cap does. A hard cost cap is a later addition once there is a
  track record to tune it against.

---

## 10. Context-Injection Boundary

- Only work product — code, spec/plan/review content, beads entries, commits — is
  ever written to a repo.
- Process/steering/orchestration context is injected per-invocation into agent
  sessions the orchestrator itself starts, via system-prompt injection and
  per-session MCP config (e.g. Claude Code headless mode's `--append-system-prompt`
  and `--mcp-config`) — never into `AGENTS.md`, `CLAUDE.md`, or any other ambient
  file.
- Applies to every agent-invoking plugin category: Planning, Execution Worker, Chain
  Review. Sources of per-invocation context that all flow through this one mechanism:
  the approved plan/spec, steer text, gate-rejection notes, and fix-loop failure
  payloads.

---

## 11. Open Questions / To Verify Before Building

- Codex CLI's equivalent to Claude Code's `--append-system-prompt` / `--mcp-config`
  (`-c developer_instructions=...`, `-c mcp_servers.*=...` — unverified). Blocks Codex
  work specifically, not the Claude-Code-only v1.
- Retry cap defaults — attempt counts and wall-clock ceilings for every loop-bounded
  hook and for the new `<node>_fix_loop` / `<gate>_reject_loop` counters. Placeholders.
- Concurrency ceiling default values (per hook type) — detail in `02` §13.
- Auth session expiry duration — detail in `02` §13.
- Exact YAML config schema (fields, structure) — not yet drafted; detail in `02` §13.
- Notification transport beyond local/OS (Slack, email) — later, if wanted.
- Findings severity threshold for the fix loop — v1 loops on any non-empty
  `findings[]`; whether low-severity nits should auto-accept needs real runs.
- Whether/when to add an OS-level service wrapper (launchd) — deferred, not blocked.

**Resolved since the original model:** beads federation semantics (→
`06_cross_repo_federation.md` §1: `bd federation` out of scope, hydration hub
adopted); vector index choice (→ `sqlite-vec`, `04_indexer_search.md` §7); UI
framework (→ React, no meta-framework, `05_ui.md` §1); gate-reject semantics (→ §8
above); the fix-then-retest loop mechanism (→ §9, backward-motion coordinator).

---

## 12. Decomposition (Component Build Order)

1. **Orchestrator core** (`02`) — chain executor, plugin registry, chain validator,
   policy engine (retry caps + gates + backward-motion), event bus, local API. No
   real plugins yet, just the skeleton.
2. **First-party plugin adapters** (`03`) — work-graph (beads), planning
   (superpowers), execution worker (Claude Code headless), env-prepare, chain review,
   review (ponytail-review + remote-review CLI), CI/MR (GitLab).
3. **Indexer + search backend** (`04`) — SQLite FTS + vector ingestion from
   `.engineering/` artifacts and session summaries, across all repos.
4. **UI** (`05`) — local web app: board, search, spec/plan viewer, approval gates,
   live status.
5. **Cross-repo / federation** (`06`) — hydration hub, involved-repo set, ordered
   multi-MR nodes, root-bump policy.

This order front-loads the genuinely new piece (the orchestrator core) and defers the
piece that depended on research finished last (federation).

---

## Changelog

Consolidated from `ai_workflow_conceptual_model_v1.md` plus the
conceptual-level content of `chain_flow_model_addendum_v1.md`,
`chain_backward_motion_addendum_v1.md`, and the §6/§7 rewrite from
`cross_repo_federation_design_v1.md`. Decisions preserved; superseded drafts dropped.

- **§3 rewritten** from the fixed phase table (old §3) to the chain-based flow model,
  per `chain_flow_model_addendum_v1.md`. The old per-phase table is gone; its
  sequence survives as `default.yaml`. `on.chain.review_ready` / `chain_finalized`
  added as a stage and a fourth gate.
- **§3.6 non-goal "no re-entry"** clarified per `chain_backward_motion_addendum_v1.md`
  §5: it means `current_node_id` never decrements; a node re-running its own tasks
  under a bounded counter is permitted.
- **§6 / §7 cross-repo** rewritten per `cross_repo_federation_design_v1.md`:
  `bd federation` out of scope, hydration hub adopted, beads dropped from the index,
  selective submodule init. Old §7's "exact semantics need a closer read of beads'
  federation docs" open item is resolved.
- **§8 gates** — added chain finalization as a scheduled gate; added the gate-reject
  behavior table (`chain_backward_motion_addendum_v1.md` §4), which the original left
  undefined.
- **§9 bounded autonomy** — added the node-level fix loop as a named core-enforced
  construct (`chain_backward_motion_addendum_v1.md` §7); the original only covered
  same-hook retry loops.
- **§11 open questions** — resolved items moved to a "Resolved since" note; genuine
  placeholders retained. A few orchestrator-scoped placeholders (concurrency ceiling
  values, auth session expiry, YAML config schema, launchd) are listed here for a
  single read-through view and repeated with detail in `02` §13.
- Ad hoc chain composition (briefly in `chain_flow_model_addendum_v1.md` §1/§5.1) was
  dropped by `orchestrator_core_addendum_v1.md` §2 and appears here only as a §3.6
  non-goal.
