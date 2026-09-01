# Overview — AI-Assisted Software Engineering Workflow

Consolidated design documentation for a personal, plugin-based, semi-autonomous
software engineering workflow system.

**Status:** v1 design, agreed, **not yet built**. Every retry/timeout number, several
tool identities (remote-review CLI, exact `bd` CLI surface, local embedding model),
and all Codex support are explicit placeholders — see each document's §"Open
Questions".

This set was consolidated from 10 layered documents in `../drive/` (one base
conceptual model + five component designs + four addendums). The originals are
retained unchanged; `DECISION_MAP.md` traces every decision from there to here.

---

## Reading Order

Read top to bottom for the full design. Each document refines the one(s) above it and
does not restate them.

| # | Document | What it covers |
|---|---|---|
| — | `00_overview.md` | This file — map, glossary, status |
| 1 | `01_conceptual_model.md` | Design principles, layered architecture, the chain-based flow model, plugin contract, default bindings, human gates, bounded-autonomy rules. **The model everything else builds against.** |
| 2 | `02_orchestrator_core.md` | Component 1 — chain executor, plugin registry + chain validator, policy engine (retry caps + gates + backward-motion coordinator), event bus, SQLite state model, session tracking/reattach, auth, the full local API surface. |
| 3 | `03_plugin_adapters.md` | Component 2 — the three adapter kinds and the seven first-party plugins: work-graph (beads), planning, execution worker, env-prepare, chain review, review, CI/MR (GitLab). |
| 4 | `04_indexer_search.md` | Component 3 — SQLite FTS5 + `sqlite-vec` ingestion of `.engineering/` artifacts and session summaries across all repos; the search API. Beads are **not** indexed. |
| 5 | `05_ui.md` | Component 4 — the local React SPA: board, work-item detail, search overlay, document viewer, gates, pause/steer controls. |
| 6 | `06_cross_repo_federation.md` | Component 5 — the beads-federation research, the involved-repo data model, the repo-set-aware `open_mr`/`mr_checks`/`merge` nodes, root-bump policy, and the hydration hub. |
| — | `DECISION_MAP.md` | Traceability matrix: every significant decision in the 10 originals → where it landed here (or why it was dropped). The review aid for confirming this consolidation is faithful. |

Each component document ends with a **`## Changelog`** section listing exactly what
was folded in from which addendum, and which superseded drafts were dropped.

---

## One-Paragraph Summaries

**Conceptual model.** A solo user, ~10 repos (some with submodules), semi-autonomous
workflow. Everything is a plugin behind hook points; today's defaults (beads,
superpowers, Claude Code, ponytail, GitLab) are a head start, not a lock-in. Work-graph
state and artifacts are git-native per repo; a disposable local index makes them
searchable as one thing. The app's process context never leaks into repo files — it is
injected per agent invocation. Every fix/retry loop is capped by the core; hitting a cap
escalates to the human with the full trace. Few scheduled gates (spec, plan, chain,
final review); everything between plan-approved and ready-for-review runs unattended but
interruptible.

**Orchestrator core.** A single Python/asyncio FastAPI process, SQLite (WAL). Each work
item gets a **chain** — an ordered list of nodes, each running one or more hook-point
tasks — materialized from a hand-authored YAML template at intake. The policy engine
caps every loop-bounded hook and hosts a **backward-motion coordinator** that lets a
`verify` / `mr_checks` node run bounded fix cycles and lets producer gates re-invoke on
rejection, all without the chain ever moving backward. Subprocess plugins are launched
detached with disk-redirected logs so they can be reattached after an orchestrator
crash. The API is localhost-only by default; auth engages only when bound to the LAN.

**Plugin adapters.** Three adapter kinds: `SubprocessAdapter` (generic),
`HeadlessAgentInvocation` (on top — adds system-prompt injection + a per-invocation MCP
status-callback server), and `HttpClientAdapter` (sibling, for GitLab). Planning, chain
review, and ponytail-review are thin config-only variants of `HeadlessAgentInvocation`;
beads, env-prepare, and the remote-review CLI sit directly on `SubprocessAdapter`;
GitLab is the sole `HttpClientAdapter` consumer. Claude Code is the only execution-worker
target this pass; Codex is deferred behind a known per-invocation-context blocker.

**Indexer + search.** Runs inside the orchestrator process, second SQLite file,
disposable. Ingests `.engineering/` markdown artifacts and `.engineering/sessions/`
summaries across every connected repo — event-driven off the internal bus, plus a
startup scan and an `on.env.prepare` piggyback, no poll loop. `kind` is an open tag;
`source_kind` is a two-value closed enum. FTS5 + `sqlite-vec` hybrid search. Beads are
not indexed — the hydration hub answers cross-repo bead queries.

**UI.** One React SPA served by the orchestrator, one global WebSocket, normalized
client store reduced from the event stream. Flat card board with per-card chain graphs
(chain templates differ, so kanban columns don't line up). Work-item detail shows a
repos panel (multi-repo only), a chain stepper, the current node's tasks with live
status chips, pause/steer/resume controls, an event timeline, and linked documents.
Global search overlay over artifacts/summaries plus a separate live bead-search box.

**Cross-repo / federation.** `bd federation` (org-to-org Dolt replication) is the wrong
mechanism and is out of scope. Cross-repo work is submodule-nested: an anchor bead in
the root repo, linked sub-beads per co-developed submodule, and a **hydration hub** (a
beads workspace aggregating all ~10 repos, read-only, lagging, never authoritative for
gating). Env-prep resolves a selective submodule set (never `--init` all). The
`open_mr` / `mr_checks` / `merge` nodes iterate the involved-repo set by merge rank —
deepest submodule first, root last — which is node-internal iteration, not chain
branching. Cross-repo merge is not transactional; partial progress across a halt is a
deliberate, surfaced outcome.

---

## Glossary

| Term | Meaning |
|---|---|
| **Work item** | One unit of tracked work. Identity is a beads issue (the *anchor bead* for a multi-repo item). Materialized state lives in `work_items`. |
| **Chain** | The ordered list of nodes a work item moves through. Resolved from a template at intake, stored as `work_items.chain_definition`. Strictly sequential — no branching, no DAG, no backward movement of `current_node_id`. |
| **Node** | One stage in a chain: `{ id, tasks: [hook_point, ...], gate_after: <gate> | null }`. A work item is always in exactly one node. |
| **Task** | One hook-point invocation within a node. Multiple tasks in a node may run concurrently only if all are read-only against the same frozen artifact. |
| **Hook point** | A named extension point (`on.spec.requested`, `on.implementation.start`, `on.ci.poll`, …) that one or more plugins bind to. |
| **Template** | A hand-authored, versioned YAML file defining a chain (`default.yaml`, `quick-task.yaml`). Selected by id at intake; never composed at runtime. |
| **Gate** | A scheduled human pause at a node boundary: `spec_approval`, `plan_approval`, `chain_finalized`, `human_review_approval`. Uses the generic `gate_requested`/`gate_approved`/`gate_rejected` events. |
| **Chain review** | The stage (`on.chain.review_ready` → `chain_finalized` gate) that runs once after plan approval to adjust not-yet-executed nodes to the now-real spec/plan. Fires only if the chain had a plan node. |
| **Backward-motion coordinator** | A policy-engine component that runs bounded fix cycles inside a `verify`/`mr_checks` node and re-invokes producer gates on rejection — without `current_node_id` ever decrementing. |
| **Fix loop / fix task** | A `<node>_fix_loop`-counted cycle: a fix-scoped `on.implementation.start` invocation followed by re-running the node's measuring tasks. |
| **Worker session** | One launched plugin subprocess. Row in `worker_sessions` with PID, log path, `node_id`, status. HTTP-client (GitLab) tasks have no worker session. |
| **Artifact** | A git-tracked markdown file under `.engineering/{specs,plans,reviews}/` — the actual spec/plan/review content. |
| **Session summary** | A short structured artifact under `.engineering/sessions/` written by the agent at session end; the thing that is indexed and searched (the raw transcript is archived cold, unindexed). |
| **Hydration hub** | A beads workspace (`~/.orchestrator/beads-hub/`) aggregating every connected repo for cross-repo *reads* only. Lags one export+sync cycle; never read for a gating decision. |
| **Involved-repo set** | The `work_item_repos` rows for a work item — one per repository in play. A single-repo item has exactly one (`role='root'`). |
| **Root repo** | For a submodule-nested work item, the superproject; otherwise the sole repo. The meaning of `work_items.repo`. |
| **`root_merge_policy`** | `bump` / `skip` / `bump_no_mr` — whether and how the superproject's submodule pointers are updated once submodule MRs merge. |
| **`needs_human`** | Work-item status covering both a scheduled-gate wait and a cap breach — a clear "stopped, waiting on you" state, never a silent failure. |
| **`capped_out`** | Worker-session terminal status: this task hit its retry/wall-clock cap while siblings ran to completion. Distinct from `failed`. |
| **Context-injection boundary** | The rule that process/steering context reaches agents only via per-invocation system-prompt / MCP config, never via `CLAUDE.md`, `AGENTS.md`, or any repo file. |

---

## Component Build Order

1. Orchestrator core (`02`) → 2. Plugin adapters (`03`) → 3. Indexer + search (`04`)
→ 4. UI (`05`) → 5. Cross-repo / federation (`06`).

Front-loads the genuinely new piece (the orchestrator core) and defers the piece whose
research finished last (federation). Each component document carries its own internal
build order in its final numbered section.
