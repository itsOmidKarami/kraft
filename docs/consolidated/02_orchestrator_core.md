# Orchestrator Core

> Component 1 of the decomposition (`01_conceptual_model.md` §12): the **skeleton**
> everything else plugs into — chain executor, plugin registry + validator, policy
> engine, event bus, session tracking, and the local API the UI talks to. No real
> plugins here (see `03_plugin_adapters.md`).

## 0. Purpose

Refines the conceptual model, does not replace it. Fills in the "chain executor" box
of the layered architecture (`01` §2) and defines every mechanism the plugin
adapters, indexer, and UI build against.

---

## 1. Internal Architecture

```
┌─────────────────────┐
│    Policy engine     │  per-hook caps, per-work-item overrides,
│                      │  backward-motion coordinator (§7)
└──────────┬──────────┘
           │ gates
           ▼
┌─────────────────────┐
│  Chain executor     │  materialize chain from template, walk nodes,
│                     │  gather() intra-node tasks (§11)
└──────────┬──────────┘
           │ dispatches
           ▼
┌─────────────────────┐
│  Plugin invocation  │  subprocess adapter (v1); HTTP-client adapter (§6)
└──────────┬──────────┘
           │ tracked by
           ▼
┌─────────────────────┐
│   Session tracker   │  PID + log file, reattach on restart (§8)
└──────────┬──────────┘
           │ emits
           ▼
┌─────────────────────┐
│  Event log + state  │  append-only + snapshot, same transaction (§4, §5)
└──────────┬──────────┘
     │            ▲
     │            └── retry counters read back into policy engine
     ▼
┌─────────────────────┐
│      Local API      │  HTTP + WS, auth-gated beyond localhost (§9, §10)
└─────────────────────┘
```

A **Chain Validator** (§12) sits as a peer of the plugin registry, checking template
files against registered hook points at startup and at intake.

---

## 2. Runtime & Stack

- **Language:** Python 3.12+, `asyncio` throughout. The workload is I/O-bound
  (subprocess management, polling, WS push), not compute-bound.
- **HTTP + WS:** FastAPI on uvicorn, single process, single port. Also serves the
  UI's static build output directly (`05_ui.md` §1).
- **State:** SQLite, WAL mode, single writer path (serialized through one queue to
  avoid write contention across concurrent work-item tasks).
- **Process introspection:** `psutil` — PID liveness + start-time checks for reattach.
- **Config:** hand-edited YAML, loaded at startup.
- **Auth:** `argon2-cffi` for password hashing; `keyring` for real secrets (GitLab
  token, etc.), backed by macOS Keychain.
- **Target OS:** macOS first. Nothing here is macOS-specific except the Keychain
  backend, and `keyring` has other-OS backends.

---

## 3. Process & Deployment Model (v1)

- Single persistent process, started manually when you sit down to work. No launchd
  registration yet — not blocked by anything here, just out of v1 scope.
- Binds to `127.0.0.1` by default. A config flag opens it to the LAN (`0.0.0.0`) for
  phone status checks; when set, auth (§9) is required, not optional.
- Single machine, single user, single DB file. No multi-user, no clustering.

---

## 4. State Model

Two SQLite files: the orchestrator's own state DB (authoritative, never rebuilt from
source) and the Indexer's separate index DB (disposable — `04_indexer_search.md` §1).
This section covers the state DB.

### 4.1 `work_items` — materialized current state

| Column | Notes |
|---|---|
| `id` | bead ref (anchor bead for a multi-repo item) |
| `repo` | the **root repo** — superproject for a submodule-nested item, sole repo otherwise |
| `current_node_id` | the node the work item is in (chains are sequential — always exactly one) |
| `chain_definition` | json — the resolved chain for this work item, seeded from a template at intake, possibly rewritten by chain review |
| `chain_template` | resolved template id (`default`, `quick-task`, …) recorded at intake; kept as its own column because `chain_definition` carries no wrapper id. Needed for the UI board's `chain_template` filter. Also mirrored into bead metadata (`03_plugin_adapters.md` §5) — different store. |
| `status` | enum: `active` \| `paused` \| `needs_human` \| `completed`. `needs_human` covers both scheduled-gate waits and cap breaches. |
| `config_overrides` | json — per-work-item retry-cap overrides |
| `root_merge_policy` | enum, default `bump`; values `bump` \| `skip` \| `bump_no_mr` — governs whether/how the superproject's submodule pointers are bumped once submodule MRs merge (`06_cross_repo_federation.md` §3a) |
| `pending_steer_context` | text, nullable — holds submitted steer text between `POST /steer` and the next `POST /resume` |
| `updated_at` | |

**Removed vs. the original design:** `phase` (→ `current_node_id` + `chain_definition`)
and `current_worker_session_id` (→ direct node-scoped query, §4.3 / §10.1).

### 4.2 `events` — append-only, authoritative log

`id`, `work_item_id`, `seq`, `type`, `payload_json`, `created_at`.

Type enum:

- `chain_loaded` — chain resolved and materialized at intake
- `node_started`, `node_completed` — node-granularity transitions
- `involved_repos_resolved` — emitted by env-prep with the resolved involved-repo set
  (parallel to `chain_loaded`; `06_cross_repo_federation.md` §3.1)
- `retry_attempt`
- `fix_cycle_started` — emitted by the backward-motion coordinator at the top of each
  fix cycle (§7.2). Payload: `node_id`, `cycle` (1-based), `triggering_failures`
  (summary of the `failures[]` / `findings[]` / pipeline result)
- `gate_requested`, `gate_approved`, `gate_rejected` — generic; the `{gate}` name is
  in the payload (`spec_approval`, `plan_approval`, `chain_finalized`,
  `human_review_approval`). `gate_rejected` payload carries a required `note`.
- `worker_session_launched`, `worker_session_completed`, `worker_session_paused`
- `steer_context_set` — emitted on `POST /steer`, carries the submitted text
- `pause_requested`, `resume_requested`
- `plugin_error`

`03_plugin_adapters.md` §6 also refers to GitLab poll-result events
(`ci_poll_result`, `mr_review_result`) emitted directly onto this log for
HttpClientAdapter tasks that have no `worker_sessions` row. Whether those are formal
members of this enum or a separate poll-result event stream was left unreconciled in
the source documents — flagged in §13, not resolved here.

Every `events` insert and its corresponding `work_items` (or `worker_sessions` /
`work_item_repos`) update happen in **one transaction** — current state is always a
cheap direct read, never a replay.

### 4.3 `worker_sessions` — one row per launched plugin subprocess

`id`, `work_item_id`, `node_id`, `hook_point`, `plugin_id`, `pid`,
`process_start_time`, `log_file_path`, `result_file_path`, `status`, `exit_code`,
`usage_reported_json`, `session_summary_ref`, `started_at`, `ended_at`.

- `node_id` — so multiple concurrent task invocations within one node are
  attributable, and a node's completion is "all `worker_sessions` rows for this
  `work_item_id` + `node_id` are terminal" — no extra table.
- `status` — `running` \| `completed` \| `unknown` \| `failed` \| `capped_out` \|
  `paused`.
  - `capped_out` — the loop hit its cap but siblings ran to completion; distinct from
    `failed` so node-completion logic can tell "all clean" from "all terminal, one
    capped out". Reachable both via a per-task hook-type cap and via a
    `<node>_fix_loop` counter breach (§7.2).
  - `paused` — a human chose to stop it; no error occurred.
- `session_summary_ref` — pointer to the agent's self-generated summary artifact
  (`01` §6). Parsed out of the result file by the subprocess adapter (§6); this is
  the only writer of that field.
- Named apart from `auth_sessions` deliberately — unrelated concepts.

### 4.4 `retry_counters` — keyed on `(work_item_id, <counter_key>)`

`attempts`, `wall_clock_started_at`, resolved `cap_attempts`, resolved
`cap_wall_clock_minutes`.

Counter keys:

- Per hook type: `test_loop`, `review_loop`, `ci_loop`, …
- Node-level fix loops (backward-motion): `verify_fix_loop`, `mr_checks_fix_loop`
- Gate reject loops (backward-motion): `spec_approval_reject_loop`,
  `plan_approval_reject_loop`, `chain_finalized_reject_loop`

Resolution: YAML hook-type default, overridden by `work_items.config_overrides`.
Written the first time that loop fires for the work item, not re-resolved per attempt.

### 4.5 `work_item_repos` — one row per repository involved in a work item

```
work_item_id     TEXT   FK work_items.id
repo_path        TEXT   absolute path (matches beads' source_repo convention)
role             TEXT   'root' | 'submodule'
submodule_path   TEXT NULL   path within the root worktree; null for role='root'
merge_rank       INTEGER     merge order — deepest submodule highest, root = 0 (last)
bead_id          TEXT NULL   anchor bead for root; linked sub-bead per co-developed
                             submodule; null if that repo has no .beads/
mr_ref           TEXT NULL   populated by the open_mr node
merge_state      TEXT   'pending' | 'mr_open' | 'checks_green' | 'merged' | 'skipped'
PRIMARY KEY (work_item_id, repo_path)
```

- Every work item has **at least** the `role='root'` row (`merge_rank=0`,
  `repo_path = work_items.repo`), written at intake. A single-repo work item has
  exactly that one row and every node behaves as the pre-federation design did.
- Submodule rows are written by the env-prep node, not at intake
  (`06_cross_repo_federation.md` §3.1).
- Modeled as its own table (not a JSON column) for the same reason as
  `worker_sessions` — row-at-a-time mutation under concurrent UI read, since the
  `merge` node walks and mutates these rows one at a time mid-node.

### 4.6 `auth_sessions`

`id`, `token_hash`, `created_at`, `expires_at`, `revoked_at`.

### 4.7 Config in YAML, written by hand *or* by the UI

Plugin registry config (which plugin binds which hook, timeouts, per-repo
enable/disable), chain template files, per-repo `default_chain_template` and
loop policy all live in versioned YAML.

**Revised (UI handoff spec §8).** The original rule was "not something the
runtime mutates" — config was hand-edited only. The Settings screens (`05` §4.7,
design 5a–5e) reverse that: the UI writes those same YAML files through
`GET/PUT /repos`, `/templates/{id}`, `/registry`, `/policy` and `/access`.

What does *not* change is where the truth lives. The UI is an editor for files
git tracks, not a front end for a config table — so a change is still reviewable,
diffable and revertible, and an operator editing the file by hand remains a
first-class path. A write validates before it lands (the chain validator re-runs
on any template or registry save) and takes effect on the same terms a hand edit
does: caps apply to loops that start after the save, a registry change affects
intake only, a bind-address change waits for a restart.

---

## 5. Event Bus

Event-sourced *lite*: `events` is authoritative, but current state is a materialized
snapshot updated transactionally alongside each event, so nothing needs replaying at
read time. Full replay is a disaster-recovery path only (rebuilding `work_items`
after DB corruption).

**Live delivery:** every event insert is also pushed to subscribed WS clients. On
reconnect a client asks for "everything after seq N" for a work item — a dropped
connection loses nothing, it catches up.

**Session-level detail doesn't live in the event log.** `worker_session_completed`
carries a pointer (`worker_sessions.id`, `session_summary_ref`); anyone wanting the
full transcript follows that pointer. The event log stays thin.

---

## 6. Plugin Registry & Adapters

The plugin contract (id/version, hook bindings, input/output schema, config) is
adapter-agnostic (`01` §4). Two adapter families exist; a third is deferred.

### 6.1 SubprocessAdapter (v1)

Covers Execution Worker and, via its CLI, Work-graph, env-prepare, and the
remote-review CLI.

- **Launch:** command template + args + env + cwd + timeout, resolved from YAML
  config plus per-invocation context.
- **Detached:** launched with a new session so the child survives if the orchestrator
  dies mid-run.
- **Output:** stdout/stderr redirected straight to a log file on disk — never piped
  into orchestrator memory. This is what makes reattach (§8) possible.
- **Structured result:** the child is handed a result-file path via an env var (e.g.
  `ORCH_RESULT_PATH`) and writes its declared output schema there before exiting. The
  raw log is for debugging/archival; the result file is what the orchestrator parses.
- **Status callback:** for long-running agent sessions, the orchestrator stands up a
  per-invocation MCP server so the worker reports progress/usage without knowing
  anything about the orchestrator beyond that one session — same mechanism as context
  injection (`01` §10), reused for status/cost.

The Execution Worker's output schema includes `session_summary_ref`; the adapter
parses it from the result file straight into `worker_sessions.session_summary_ref`.

### 6.2 HttpClientAdapter

Sibling to SubprocessAdapter, not a variant — no process, PID, log file, or reattach
story. Base URL + keyring-sourced token + retry/backoff + integration with the policy
engine's loop-bounded caps. Only consumer today: CI/MR (GitLab). Generalized now
because the shape (auth + retry + poll-against-a-cap) is what any future HTTP plugin
needs. Its unit of work is a request/response pair logged to the event bus; no
`worker_sessions` row analog.

### 6.3 MCP-client adapter — not added

Work-graph (beads) uses plain SubprocessAdapter directly. Beads' CLI is JSON-native;
its MCP server is an alternative transport, not an added capability. An MCP-client
adapter would trade the disk-redirected-log + PID-reattach model for a live
connection outside that story, for no gain. Revisit only if a needed beads capability
has no CLI equivalent.

---

## 7. Policy Engine

### 7.1 Retry caps

- Caps (attempt count + wall-clock ceiling) default per hook type, overridable per
  work item via `config_overrides` — override wins if set, else the hook-type
  default. Result written into `retry_counters` the first time a loop-bounded hook
  fires, not re-resolved per attempt.
- Checked before every invocation of a loop-bounded hook: if either cap is breached,
  the loop halts, an event is emitted, and the work item drops to `needs_human`.
- **Cap breach under node concurrency:** sibling tasks in the same node are **not
  killed** — a cap breach halts only that task. The offending task's row →
  `capped_out`. The work item drops to `needs_human` only once **every** task in the
  node is terminal — not the instant the first one breaches — so the human sees the
  fullest picture the node produced.
- Cost/token usage is logged (not yet enforced) from
  `worker_sessions.usage_reported_json`, sourced from the Execution Worker's
  self-report. The core does not compute usage itself.

### 7.2 Backward-Motion Coordinator

A component of the policy engine, not a peer. Owns two things the base policy engine
did not model — both bounded exactly like every other cap (§7.1 resolution rule):

- **Node-level fix-loop counters** — `verify_fix_loop`, `mr_checks_fix_loop`.
- **Gate reject-loop counters** — `spec_approval_reject_loop`,
  `plan_approval_reject_loop`, `chain_finalized_reject_loop`.

**Carve-out:** a fix task or gate re-invocation launched by the coordinator does
**not** increment `(work_item_id, execution_worker)` or any producer-hook budget — it
is bookkept solely under the loop it serves. It still occupies one `execution_worker`
concurrency slot (§11), just not a retry budget. Same rationale as reattach (§8) and
resume (§10.2) not counting.

**Inner loop vs. outer loop.** The fix loop is the *outer* loop. Any inner poll loop
a measuring task runs is unchanged and separate: `on.ci.poll` still polls GitLab
until the pipeline reaches a terminal state or the `ci_loop` cap trips — that cap
guards a *hung* pipeline, not a *failed* one. `on.test.run` and `on.review.local.run`
run once and return, no inner loop. The fix loop below reacts only to terminal
non-clean results.

**Entry point A — measurement-failure fix loop.** Every measuring task in `verify` or
`mr_checks` has reached a terminal result and at least one is non-clean (non-empty
`findings[]` of any severity triggers the loop in v1 — a severity threshold is
deferred, §13):

| Task | Non-clean result |
|---|---|
| `on.test.run` | `status: "red"` + `failures[]` |
| `on.review.local.run` | non-empty `findings[]` |
| `on.ci.poll` | pipeline terminal = failed |
| `on.review.mr.run` | non-empty `findings[]` |

Per cycle:

1. Increment `(work_item_id, <node>_fix_loop)`.
2. **Breached** → every measuring task in the node → `capped_out`, node goes
   terminal, `work_items.status = needs_human`. Stop.
3. **Not breached** → launch **one** fix task: `on.implementation.start` via its
   existing `HeadlessAgentInvocation` adapter, `cwd` = the worktree of the repo whose
   check failed. New `worker_sessions` row: `node_id` = the current node,
   `hook_point` = `on.implementation.start`. Injected prompt is fix-scoped ("the
   following checks failed against the current diff; fix them, make no unrelated
   changes") plus the structured failure payload. One fix task per cycle, not one per
   failing check.
4. Emit `fix_cycle_started`.
5. On fix-task terminal state:
   - `done` → re-run the node's measuring tasks (§7.2 "which re-run"), then back to
     step 1 on the combined result.
   - `plan_diverged` → route to the guidance gate (`01` §8) as an ordinary
     divergence; the `<node>_fix_loop` counter is preserved and resumes at step 1 on
     `on.guidance.provided`.
   - `error` → attempt already counted at step 1; re-evaluate the cap.

Which measuring tasks re-run each cycle:

- **`verify`** — both `on.test.run` and `on.review.local.run` re-run every cycle (the
  fix changed the diff). Node completes only when both are clean **in the same cycle**.
- **`mr_checks`** — `on.ci.poll` re-runs every cycle (fix commits are pushed to the MR
  branch, so CI re-runs anyway). `on.review.mr.run` (the remote CLI, kept out of the
  local loop for cost) runs **only once `on.ci.poll` is green**, not every cycle. If
  the remote review then comes back non-clean, that is a fresh non-clean result — one
  more cycle, still under the one `mr_checks_fix_loop` counter.

Cross-repo: under a multi-repo work item `mr_checks` runs per involved repo; a red
submodule MR → fix task with `cwd` = that submodule's worktree → commits pushed to
that submodule's MR branch. Still **one** `mr_checks_fix_loop` counter for the node
(a 3-repo item burns it ~3× faster — a tuning note). The `merge` node's mid-walk
CI-red case is **not** a fix loop — it stays `needs_human` + human re-runs `merge`
(`06_cross_repo_federation.md` §3.5).

**Entry point B — gate rejection.** `POST /work-items/{id}/gates/{gate}/reject` takes
a **required** `{ note: string }` body. For the three producer gates:

1. Emit `gate_rejected` with `note` in the payload (the event log is authoritative
   for the note — no new event type, no new `work_items` column).
2. Increment `(work_item_id, <gate>_reject_loop)`.
3. **Breached** → `work_items.status = needs_human`. Stop.
4. **Not breached** → re-invoke the node's own producing hook:

   | Gate | Re-invoked hook |
   |---|---|
   | `spec_approval` | `on.spec.requested` |
   | `plan_approval` | `on.plan.requested` |
   | `chain_finalized` | `on.chain.review_ready` |

   Fresh `worker_sessions` row, same `node_id`. The rejection note is appended to
   that invocation's injected system prompt (`01` §10 — another per-invocation
   context source, no new mechanism).
5. The producing hook completes → the node's `gate_after` fires again →
   `gate_requested` → back to the human.

`chain_finalized` re-invocation: `on.chain.review_ready` produces a fresh
`revised_chain_nodes`; on re-approval it is spliced into `work_items.chain_definition`
exactly as the first time.

**`human_review_approval` reject** — emits `gate_rejected` with the required `note`,
sets `needs_human`. No counter, no auto-re-invocation. A rejected final review means
real forward chain re-entry (redo implementation, replay `verify` → `open_mr` →
`mr_checks`) — the human drives it via ad hoc pause/steer or a manual re-trigger.

### 7.3 Reconciliation with the chain non-goals

`current_node_id` never decrements. The fix task launches *inside* the current node,
alongside the measuring tasks. Gate reject re-invokes the *current* node's producing
hook. Still strictly sequential, still no DAG. The only new fact: a node may execute
more than one cycle of its own tasks, capped by the core.

---

## 8. Session Tracking & Reattach

On launch, a `worker_sessions` row is written *before* the subprocess starts,
`status=running`, with PID and process start time. On orchestrator startup, every row
still `running` is checked (`psutil`): does a process at that PID exist, and does its
start time match (guarding against PID reuse after reboot)? Match → reattach, keep
tailing the log, poll for exit. No match → mark `unknown`, drop to `needs_human` with
whatever log survived.

Only works for subprocess-based plugins with disk-redirected output. `paused` rows
are excluded from the check (not expected to have a live process).

**Reattach never touches `retry_counters`.** A crash-and-restart is an orchestrator
failure, not a plugin failure — the reattached process is the *same* attempt.
`attempts` increments only when a launched process actually exits (or is positively
confirmed gone) and the outcome is evaluated.

---

## 9. Auth

- Password hashed with argon2id, stored in config — never plaintext.
- `POST /login` verifies the password, issues a random opaque token, stores only its
  hash in `auth_sessions`, sets it as an `HttpOnly`, `SameSite=Lax` cookie.
- Every other request checks the cookie against `auth_sessions` (not expired, not
  revoked).
- Sessions expire (default TBD) and are individually revocable — "log out everywhere"
  is a delete across `auth_sessions`.
- Failed logins are rate-limited (in-memory counter + backoff) — enough to blunt
  casual guessing from something else on the LAN, not to defend a public service.
- **Only active when bound beyond localhost.** On strict `127.0.0.1` the auth
  middleware is skipped entirely — no check on the request path at all, since
  strictly-local access has no attacker model. `/login` and `/logout` still exist
  either way, so the path is testable before flipping the LAN flag.
- **No DB-level encryption keyed off the password** — it would block unattended
  recovery after a crash (§8's reattach reads state with no human present). Real
  secrets (GitLab token, API keys) go in the macOS Keychain via `keyring`; FileVault
  covers the rest of the disk.

---

## 10. Local API Surface

### 10.1 Endpoints

- `POST /login`, `POST /logout`
- `POST /work-items` — the **only** path by which a work item comes into existence
  (no passive bead-watching). Body:
  ```
  { repo, title,
    chain_template?: <template id>,          // omitted → repo's default_chain_template
    submodules?: [<relative path>],           // omitted → resolve at env-prep, none by default
    root_merge_policy?: "bump" | "skip" | "bump_no_mr" }   // omitted → "bump"
  ```
  The orchestrator dispatches `on.intake` to the work-graph plugin (`bd create`),
  then resolves and materializes the chain, running it through the Chain Validator's
  resolution tier (§12) before the work item leaves intake. Single-repo intake uses
  only the first two fields.
- `GET /work-items` (list) — one row per work item, including `chain_definition` in
  full, `chain_template`, `involved_repos` (compact `work_item_repos` rows), and
  `root_merge_policy`. A deliberate departure from a thin-list endpoint: the board
  renders every item's full chain graph and filters by `chain_template` /
  `involved_repo` up front, and every field is an already-materialized column — no
  join cost at this scale.
- `GET /work-items/{id}` (detail) — full row (`chain_definition`, `chain_template`,
  `current_node_id`, `status`, `config_overrides`, `pending_steer_context`,
  `root_merge_policy`) plus:
  - the `worker_sessions` array for that item, **node-scoped** — `WHERE work_item_id
    = ? AND node_id = current_node_id`, **every** task in the current node regardless
    of state (running, completed, failed, capped_out, paused, unknown), *not* filtered
    to `running`. The UI's current-node panel needs terminal-state rows present to
    render chips and to run its client-side "awaiting gate approval" inference.
  - the full `work_item_repos` array (`repo_path`, `role`, `submodule_path`,
    `merge_rank`, `bead_id`, `mr_ref`, `merge_state` per repo). A single-repo item has
    exactly one entry (`role='root'`).
- `GET /work-items/{id}/events?after_seq=N`
- `POST /work-items/{id}/gates/{gate}/approve`
- `POST /work-items/{id}/gates/{gate}/reject` — body `{ note: string }`, **required**
  (§7.2 entry point B). `{gate}` ∈ `spec_approval` \| `plan_approval` \|
  `chain_finalized` \| `human_review_approval`.
- `POST /work-items/{id}/pause`, `/resume`, `/steer` — §10.2 pause/steer mechanism
  below.
- `GET /worker-sessions/{id}/log` — raw contents of `worker_sessions.log_file_path`,
  a pull not a stream. The **only** place raw agent output is exposed, and on request,
  not live (consistent with §6's disk-only stdout). No pagination in v1.
- `GET /beads/search?q=` — thin passthrough to `bd search --json -C <hub>`. Returns
  bead id, title, repo, status, snippet. Structured, not indexed. Owned by component
  5 (`06_cross_repo_federation.md` §6.1); listed here because it sits on this API
  surface.
- `WS /ws/events` — subscribe to one or all work items, live push.
- `GET /health` — also surfaces Chain Validator problems (invalid templates, §12).

Document-fetch endpoints (`GET /documents/{id}`, `GET /work-items/{id}/documents`)
belong to the Indexer — `04_indexer_search.md` §9.

### 10.2 Pause / Steer Mechanism

Execution Worker (and every HeadlessAgentInvocation-backed plugin) is a one-shot
`claude -p ...` subprocess with no stdin channel. There is no live connection to
redirect mid-session — an OS-level suspend would freeze the process but a frozen
agent still can't accept new instructions once resumed. So pause and steer are **one
mechanism: kill the current attempt, optionally carry new context into the next one.**

**`POST /pause`**
- SIGTERM every `running` `worker_sessions` row for the work item's *current node* —
  all of them if it's a concurrent node (`verify`, `mr_checks`).
- Each affected row → `status: paused`. Log file untouched.
- `work_items.status` → `paused`. Emits `pause_requested` + one
  `worker_session_paused` per affected row.
- Non-agent poll/CLI tasks (`on.ci.poll`, `on.review.mr.run`, `on.mr.open`,
  `on.merge`) have no process and no row — pause means "withhold the next scheduled
  poll tick"; the poll-scheduling logic checks `work_items.status` before firing.
  (`on.review.mr.run`'s adapter kind is stated inconsistently across the source
  docs — see `03_plugin_adapters.md` §4; either way it is not steerable and pauses
  the same way.)

**`POST /steer { text }`**
- Valid only while `status = paused`. Writes `text` to
  `work_items.pending_steer_context`, emits `steer_context_set`.
- No effect on non-agent tasks. Not enforced server-side this pass (the endpoint
  doesn't reject a steer against a non-agent hook) — client-side steerable-hook table
  in `05_ui.md` §4.2; server-side validation is a later add.

**`POST /resume`**
- Relaunches every task killed for the current node as fresh `worker_sessions` rows
  (new PID, new `worker_session_launched` each).
- If `pending_steer_context` is set, it's appended to that invocation's injected
  system prompt, then cleared.
- **Does not increment `retry_counters.attempts`** — same rationale as reattach (§8):
  a human-initiated interruption is not a plugin failure.
- `work_items.status` → `active`.

---

## 11. Concurrency

One asyncio task per in-flight work item; SQLite WAL handles the concurrent
reads/writes at this scale. Multiple work items running their unattended stretch at
once is a first-class requirement.

- **Soft concurrency ceiling is per hook type**, not one global number: a
  `max_concurrent` value (config, default TBD) for `execution_worker`, `test_loop`,
  `review_loop`, etc. Execution Worker sessions are the expensive ones and get the
  tightest ceiling, but test runners and review bots spawn subprocesses too. Not a
  cost control — a guard against saturating the machine or tripping API rate limits.
- **Intra-node fan-out:** on entering a multi-task node, the work item's asyncio task
  `gather()`s the launches for every task in that node; each launch resolves its own
  hook-type ceiling and retry cap independently. The node does not advance until
  every task reaches a terminal state.
- A node's concurrent tasks are a *same-work-item* concern that interacts with but is
  distinct from the *cross-work-item* ceiling: if `test_loop`'s ceiling is already
  saturated by other work items, a task inside a node queues behind it, delaying that
  node's completion independent of its own retry loop. Worth knowing when tuning
  ceiling defaults.

---

## 12. Chain Validator

A peer of the Plugin Registry, not part of it. Templates cross-reference the registry
by hook-point name and need validation the registry never specified for its own YAML.

- **Startup tier.** On process start, every template file is checked against the
  global hook-point set. A template referencing an unknown hook is marked invalid and
  excluded from the resolvable set — does not block startup or affect other templates.
  Invalid templates show up in `GET /health`.
- **Resolution tier.** At intake, the chosen template is re-checked against the
  *target repo's* plugin config — a hook can be globally registered but disabled per
  repo. A template valid at startup can still fail here; this is a per-work-item
  failure that blocks that one intake with a clear error rather than materializing a
  chain with a dead node.

---

## 13. Open Questions / Placeholders

- Retry cap defaults per hook type + wall-clock minutes; `<node>_fix_loop` and
  `<gate>_reject_loop` counter defaults. Placeholders (`01` §11).
- Concurrency ceiling default values.
- Auth session expiry duration.
- Exact YAML config schema (fields, structure) — not yet drafted.
- `ci_loop` cap tuning when one work item drives CI across 3+ repos under one counter
  (`06_cross_repo_federation.md` §3.6).
- Server-side validation that `POST /steer` targets a steer-capable hook — client-side
  table only this pass.
- `GET /worker-sessions/{id}/log` pagination — deferred until log sizes become a
  problem.
- Whether the steerable-hook mapping should become real plugin-registry metadata
  (`interactive: bool` per hook) rather than a UI-side table — noted, not designed.
- **"No-progress" early escalation for the fix loop** — a `verify` / `mr_checks` fix
  loop making no measurable progress across cycles could escalate to the guidance
  gate before the hard cap. Deferred: defining "no progress" (same failures? same
  files touched? diff churn without green?) needs a track record, same reasoning as
  the auto-re-loop-on-drift deferral (`01` §8).
- Whether GitLab poll-result events (`ci_poll_result`, `mr_review_result`,
  `03_plugin_adapters.md` §6) are formal members of the §4.2 `events` enum or a
  distinct poll-result stream — carried unreconciled from the source documents.
- Whether/when to add an OS-level service wrapper (launchd).

---

## 14. Build Order

1. **State model + migrations** — the SQLite schema in §4. Nothing else works without
   it.
2. **Event bus** — transactional write path + WS broadcast + catch-up.
3. **Subprocess plugin adapter + session tracker + reattach** — the mechanics that
   depend on state existing.
4. **Chain executor + Chain Validator** — materialize from template, walk nodes,
   intra-node `gather()`.
5. **Policy engine** — caps, gating, backward-motion coordinator, resolved against
   `retry_counters`.
6. **Auth layer.**
7. **Local API surface** — wires all of the above together for the UI.

State and events first, then the mechanics that depend on state, then the interface
layer last — mirroring the conceptual model's own decomposition logic.

---

## Changelog

Consolidated from `orchestrator_core_design_v1.md`, `orchestrator_core_addendum_v1.md`,
and §§1–3 of `orchestrator_indexer_api_addendum_v1.md`, plus the state-model and
policy-engine deltas from `chain_flow_model_addendum_v1.md`,
`chain_backward_motion_addendum_v1.md`, and `cross_repo_federation_design_v1.md`.

- **§1** — the undefined "Phase state machine" box is now the **chain executor**
  (`chain_flow_model_addendum_v1.md` §0).
- **§4.1 `work_items`** — `phase` → `current_node_id` + `chain_definition`
  (`chain_flow_model_addendum_v1.md` §8); `chain_template` column added (ibid);
  `current_worker_session_id` **removed** (`orchestrator_core_addendum_v1.md` §4);
  `status` enum pinned to `active|paused|needs_human|completed`
  (`orchestrator_indexer_api_addendum_v1.md` §1.2); `pending_steer_context` added
  (ibid); `repo` meaning tightened to "root repo" and `root_merge_policy` added
  (`cross_repo_federation_design_v1.md` §2).
- **§4.2 `events`** — `phase_transition` → `node_started`/`node_completed`;
  `chain_loaded`, `fix_cycle_started`, `steer_context_set`, `worker_session_paused`,
  `involved_repos_resolved` added. The bespoke `chain_review_requested` /
  `chain_finalized` event types briefly proposed in `chain_flow_model_addendum_v1.md`
  §9 were **retracted** there and in `orchestrator_core_addendum_v1.md` §3 — the
  chain-review gate reuses the generic `gate_*` events. `gate_rejected` payload gains
  a required `note` (`chain_backward_motion_addendum_v1.md` §4).
- **§4.3 `worker_sessions`** — `node_id` column added
  (`chain_flow_model_addendum_v1.md` §8); `status` gains `capped_out`
  (`orchestrator_core_addendum_v1.md` §6) and `paused`
  (`orchestrator_indexer_api_addendum_v1.md` §1.2).
- **§4.4 `retry_counters`** — fix-loop and reject-loop counter keys added
  (`chain_backward_motion_addendum_v1.md` §2).
- **§4.5 `work_item_repos`** — new table (`cross_repo_federation_design_v1.md` §2).
- **§7.1** — cap-breach behavior revised: siblings not killed, `needs_human` only when
  all node tasks terminal (`orchestrator_core_addendum_v1.md` §6). This is a real
  revision of the original "if either cap is breached, the loop halts."
- **§7.2** — backward-motion coordinator is entirely new
  (`chain_backward_motion_addendum_v1.md` §§2–6). It resolves the "how does
  fix-then-retest loop, given chains don't branch" question left open in
  `plugin_adapters_design_v1.md` §10 gap #3, and the "reject behavior undefined for
  all gates" gap in `orchestrator_core_addendum_v1.md` §3.
- **§10.1** — `POST /work-items` is new (`orchestrator_core_addendum_v1.md` §2),
  later gaining `submodules?` / `root_merge_policy?`
  (`cross_repo_federation_design_v1.md` §6.1). Ad hoc chain-composition / raw-YAML
  endpoint was dropped (`orchestrator_core_addendum_v1.md` §2). `GET` response shapes
  pinned (`orchestrator_indexer_api_addendum_v1.md` §2). `/worker-sessions/{id}/log`
  new (ibid §3). `gates/{gate}/reject` body now required.
- **§10.2 pause/steer** — mechanism was undefined (endpoints existed, behavior
  didn't); defined in `orchestrator_indexer_api_addendum_v1.md` §1.
- **§11** — intra-node fan-out execution model added
  (`orchestrator_core_addendum_v1.md` §5, `chain_flow_model_addendum_v1.md` §10).
- **§12 Chain Validator** — new (`orchestrator_core_addendum_v1.md` §1).
