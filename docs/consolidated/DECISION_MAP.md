# Decision Map

Traceability from the 10 original documents (since removed) to the 7 consolidated
documents here. Use this to confirm the consolidation preserved every decision and
spec — not byte-for-byte, but every design decision, schema, endpoint, and rule.

**Legend for "How":**

- **verbatim** — carried over essentially unchanged
- **merged** — combined with material from other docs, same meaning
- **amended** — a later addendum changed it; the *final* state is in the clean doc, the
  change is noted in that doc's Changelog
- **dropped** — intentionally not carried; reason given

**Sections intentionally dropped from every source:** the "Self-Review / Gaps Checked"
subsections (`chain_flow_model_addendum` §13, `chain_backward_motion_addendum` §9,
`orchestrator_core_addendum` §7, `orchestrator_indexer_api_addendum` §1.4). These were
working notes verifying an addendum didn't break reattach / auth / concurrency
ceilings — every one concluded "no regression." Their conclusions are baked into the
consolidated text; the checklists themselves are not spec content.

---

## 1. `ai_workflow_conceptual_model_v1.md` → `01_conceptual_model.md`

| Original | Decision / content | Landed in | How |
|---|---|---|---|
| §0 | Supersedes §10/§11 of an earlier requirements doc; "model to build against" | `01` §0 | merged (context kept brief) |
| §1 | 9 design principles (solo-first, everything-a-plugin, git-native + derived index, no process leak into repo, bounded autonomy, few meaningful pauses, poll-vs-live, local web app, interruptible) | `01` §1 | amended — principle #6's gate list gains "chain finalization" (otherwise verbatim) |
| §2 | Layered architecture diagram (UI / backend / plugins / source) | `01` §2 | amended — "Phase state machine" box → "Chain executor"; validator + backward-motion named; `sessions/` folder added to source layer |
| §3 | Fixed phase table (16 rows, hook points, default plugin, gate?, loop-bounded?) | `01` §3 | **amended — replaced** by the chain-based flow model (`chain_flow_model_addendum`). The row sequence survives as `default.yaml` in `01` §3.2 |
| §4 | Plugin contract (id/version, hook points, I/O schema, config; multi-bind via shared schema; adapter-agnostic; agent-invoking plugins respect §10) | `01` §4 | amended — Chain Review added to the agent-invoking list (otherwise verbatim) |
| §5 | Default plugin bindings table | `01` §5 | amended — Chain Review row added; Review split into local (ponytail) / MR (remote CLI); index row notes "no beads"; worktree row notes "selective init" |
| §6 | Data model — what lives where (work-graph / artifact store / session summaries / session archive / index) | `01` §6 | amended — `sessions/` folder named; chain-template pointer on the bead; "beads are not indexed" |
| §7 | Cross-repo & submodules (anchor bead + linked beads; "exact semantics need a closer read of beads' federation docs" — open) | `01` §7 | **amended** — rewritten per `cross_repo_federation_design_v1`: `bd federation` out of scope, hydration hub, selective `--init`. Open item resolved |
| §8 | Gates — scheduled (spec/plan/human-review), ad hoc pause/steer, guidance gate on plan drift | `01` §8 | amended — chain finalization added as a 4th scheduled gate; gate-reject behavior table added (was undefined) |
| §9 | Bounded autonomy — cap = attempts + wall-clock, cap breach → needs-human + full trace, cost logged not enforced | `01` §9 | amended — node-level fix loop added as a named core-enforced construct |
| §10 | Context-injection boundary — work product to repo, process context per-invocation only | `01` §10 | amended — fix-loop failure payload + gate-reject note named as additional per-invocation context sources (otherwise verbatim) |
| §11 | Open questions (beads federation, Codex flags, vector index, retry defaults, UI framework, notification transport) | `01` §11 | amended — resolved items (federation, vector index → `sqlite-vec`, UI → React, gate-reject, fix loop) moved to a "Resolved since" note; genuine placeholders kept; a few orchestrator-scoped placeholders repeated here for a single-view read (they detail-live in `02` §13) |
| §12 | Suggested decomposition — 5 components, build order | `01` §12 | amended — component-2 build step now names env-prepare + chain review; wording refreshed; sequence unchanged |

---

## 2. `chain_flow_model_addendum_v1.md` → mostly `01` §3, some `02`

| Original | Decision / content | Landed in | How |
|---|---|---|---|
| §1 | Chain replaces the fixed phase table; adjustable once after plan approval; "a chain, not a DAG" | `01` §3.1 | verbatim |
| §2 | `Chain := ordered list of Node`; `Node := { id, tasks, gate_after }`; one node at a time; node completion = all tasks terminal | `01` §3.1 | verbatim |
| §3 | Template YAML format; `default.yaml` + `quick-task.yaml` full listings; "no `chain_review` node" is the skip mechanism | `01` §3.2 | amended — `default.yaml` reproduced in full; `quick-task.yaml`'s YAML block collapsed to a prose description ("identical from `env_setup` onward, no spec/plan/chain_review node"); skip mechanism re-keyed from "has chain_review node" to "has plan node" (fixes an internal inconsistency in the original between its §3 and §6); federation's repo-set-aware `open_mr`/`mr_checks`/`merge` noted inline, detailed in `06` §3 |
| §4 | Concurrency rule — read-only fan-out only; `verify` + `mr_checks` the two cases; concurrent writes out of scope (happen inside the worker plugin) | `01` §3.3 | verbatim — federation's per-repo-instance amendment (both `verify` and `mr_checks` run once per involved repo) now stated in `01` §3.3 |
| §5 | Chain lifecycle within a work item (intake resolve → nodes in order → chain review after plan approval → run against locked-in chain) | `01` §3.4 | verbatim |
| §6 | New stage: Chain Review (`on.chain.review_ready` → `on.chain.finalized`); why it's its own stage not folded into the plan hook; skip condition; 4th-gate trade-off noted | `01` §3.5 | verbatim |
| §7 | Skip conditions = template choices, no special-case logic | `01` §3.2, §3.4 | verbatim |
| §8 | State model changes: `work_items.phase` → `current_node_id` + `chain_definition`; `worker_sessions.node_id`; `chain_template` column; repo config `default_chain_template` | `02` §4.1, §4.3, §4.7 (repo config) | merged into the consolidated schema |
| §9 | New events: `chain_loaded`, `node_started`/`node_completed`. **Correction:** bespoke `chain_review_requested`/`chain_finalized` event types dropped — reuse generic `gate_*` | `02` §4.2 | amended — only the corrected (generic) form appears; the retracted draft is noted in the Changelog |
| §10 | Retry caps unaffected per task; per-hook-type concurrency ceiling stays cross-work-item; node concurrency is a distinct same-work-item concern | `02` §7, §11 | merged |
| §11 | Non-goals: no branching/merging, no concurrent writes in a node, no on-the-spot composition, no planner-generated chains | `01` §3.6 | verbatim — "no re-entry" reading clarified per backward-motion §5 |
| §12 | 3 open items (YAML validation, where ad hoc chains authored, `chain_finalized` semantics) — all resolved in `orchestrator_core_addendum` | `02` §12, §10.1, §4.2 | merged — resolutions folded in, open items closed |
| §13 | Self-review checklist | — | dropped (working notes) |

---

## 3. `chain_backward_motion_addendum_v1.md` → mostly `02` §7

| Original | Decision / content | Landed in | How |
|---|---|---|---|
| §1 | A node may run >1 cycle of its own tasks, bounded by a counter; `current_node_id` never moves backward; two entry points, one coordinator | `02` §7.2, §7.3 | verbatim |
| §2 | Backward-motion coordinator = policy-engine component; node-level fix-loop counters (`verify_fix_loop`, `mr_checks_fix_loop`); gate reject-loop counters; carve-out (coordinator launches don't touch `execution_worker`/producer budgets) | `02` §7.2, §4.4 | verbatim |
| §3 | Entry point A — measurement-failure fix loop: trigger table, per-cycle coordinator behavior (increment → breached?→capped_out / not→fix task), `fix_cycle_started`, fix-task terminal handling (`done`/`plan_diverged`/`error`) | `02` §7.2 | verbatim |
| §3.1 | Inner poll loop vs. outer fix loop; `ci_loop` cap "guards a hung pipeline, not a failed one"; `on.test.run` / `on.review.local.run` run once, no inner loop | `02` §7.2 ("Inner loop vs. outer loop") | verbatim |
| §3.4 | Which measuring tasks re-run each cycle — `verify` both every cycle; `mr_checks` `on.ci.poll` every cycle, `on.review.mr.run` only after CI green | `02` §7.2, `03` §4 | verbatim |
| §3.5 | Cross-repo fix loop layers per-repo; one shared `mr_checks_fix_loop` counter; `merge` mid-walk CI-red is explicitly *not* a fix loop | `02` §7.2, `06` §3.4, §3.5 | verbatim |
| §3.6 | One non-clean result → one fix task, not one per failing check | `02` §7.2 | verbatim |
| §4 | Entry point B — gate rejection: `POST .../reject` gains required `{ note }`; three producer gates re-invoke their producing hook bounded by `<gate>_reject_loop`; note injected into the system prompt | `02` §7.2, `01` §8 | verbatim |
| §4.4 | `human_review_approval` reject → `needs_human`, no counter, no auto-re-invoke; rationale (real forward chain re-entry, human stays in control) | `02` §7.2, `01` §8 | verbatim |
| §5 | Reconciliation with `chain_flow_model_addendum` §11 — "no re-entry" = `current_node_id` never decrements; a node re-running its own tasks under a bounded counter is permitted | `01` §3.6, `02` §7.3 | verbatim |
| §6 | New event `fix_cycle_started` (payload: `node_id`, `cycle`, `triggering_failures`); no other new types | `02` §4.2 | verbatim |
| §7 | Amendment table across all component docs | per-doc Changelogs + `04`/`06` bodies | merged — each row landed in the target consolidated doc; `04` and `06` also carry the fold in their body text with the change credited in their Changelogs |
| §8 | Open questions (counter defaults, findings severity threshold, "no-progress" early escalation, fix-prompt text, `on.review.mr.run` sync/async, UI iconography) | `02` §13 (counter defaults, no-progress escalation, `on.review.mr.run` sync/async); `01` §11 / `03` §10 (findings severity); `03` §10 (fix-prompt text); `05` §8 (UI iconography) | merged — every item retained |
| §9 | Self-review checklist | — | dropped (working notes) |

---

## 4. `orchestrator_core_design_v1.md` → `02_orchestrator_core.md`

| Original | Decision / content | Landed in | How |
|---|---|---|---|
| §1 | Internal architecture stack diagram; "Phase state machine" box left undefined | `02` §1 | amended — box is now the chain executor; validator added |
| §2 | Runtime & stack (Python 3.12+ asyncio, FastAPI/uvicorn single process, SQLite WAL single-writer-queue, psutil, YAML config, argon2 + keyring, macOS-first) | `02` §2 | verbatim — one clause added ("also serves the UI's static build output"), sourced from `ui_design_v1.md` §1 |
| §3 | Process & deployment (manual single process, `127.0.0.1` default + LAN flag forces auth, single machine/user/DB) | `02` §3 | verbatim |
| §4 | State model — `work_items` (incl. `phase`, `current_worker_session_id`), `events` (type list), `worker_sessions`, `retry_counters`, `auth_sessions`; one-transaction rule; registry config in YAML | `02` §4 | **amended heavily** — see `02` Changelog: `phase`→`current_node_id`+`chain_definition`; `chain_template` added; `current_worker_session_id` removed; `status` enum pinned; `pending_steer_context` added; `repo`→root; `root_merge_policy` added; event enum extended; `worker_sessions.node_id` + `capped_out`/`paused`; `retry_counters` gains fix/reject keys; `work_item_repos` table added |
| §5 | Event bus — event-sourced lite, materialized snapshot, live WS delivery, catch-up by seq, thin log + pointers | `02` §5 | verbatim |
| §6 | Plugin registry & subprocess adapter (v1 subprocess only; launch/detach/log-redirect/result-file-via-env-var/status-callback MCP; `session_summary_ref` sole writer) | `02` §6 (SubprocessAdapter, HttpClientAdapter, MCP-client "not added"); the HeadlessAgentInvocation layer is defined in `03` §1 | merged — `02` §6 keeps the subprocess-adapter contract and names the two adapter families it introduces; the three-way layering is spelled out in `03` §1 |
| §7 | Policy engine — cap resolution (YAML default + `config_overrides`), checked before every loop-bounded invocation, cost logged not enforced | `02` §7.1 | amended — cap-breach-under-node-concurrency revision (siblings not killed, `needs_human` only when all node tasks terminal); backward-motion coordinator added as §7.2 |
| §8 | Session tracking & reattach — row before subprocess, psutil PID + start-time check on startup, reattach or mark `unknown`; reattach never touches `retry_counters` | `02` §8 | verbatim — `paused` rows excluded from the check (from api addendum) |
| §9 | Auth — argon2id, opaque token + hash in `auth_sessions`, HttpOnly SameSite=Lax cookie, revocable, rate-limited, only active beyond localhost, no DB encryption keyed off password | `02` §9 | verbatim |
| §10 | Local API surface (v1 endpoints list) | `02` §10.1 | amended — `POST /work-items`, `/worker-sessions/{id}/log`, `GET /beads/search` added; `reject` body now required; `GET` response shapes pinned; pause/steer mechanism defined in §10.2 |
| §11 | Concurrency — one asyncio task per work item, per-hook-type `max_concurrent` ceiling, not a cost control | `02` §11 | amended — intra-node fan-out (`gather()`) execution model added |
| §12 | Open questions (retry defaults, ceiling default, auth expiry, YAML schema, launchd) | `02` §13 | merged |
| §13 | Suggested build order (state → events → adapter/tracker/reattach → policy → auth → API) | `02` §14 | amended — chain executor + validator step inserted |

---

## 5. `orchestrator_core_addendum_v1.md` → `02`, some `03`

| Original | Decision / content | Landed in | How |
|---|---|---|---|
| §1 | Chain Validator — new component, peer of plugin registry; startup tier + resolution tier | `02` §12 | verbatim |
| §2 | `POST /work-items { repo, title, chain_template? }` — intake as a synchronous hook dispatch, not passive bead-watching; ad hoc chain composition **dropped**; only path a work item is created | `02` §10.1, `01` §3.6 | verbatim — federation later adds `submodules?`/`root_merge_policy?` |
| §3 | `chain_finalized` gate reuses the generic gate primitive; no new endpoint, no new event types; reject-behavior still undefined for all gates (pre-existing gap) | `02` §4.2, §10.1 | amended — reject behavior later defined by backward-motion; only that final state appears |
| §4 | Drop `work_items.current_worker_session_id`; replace with node-scoped `worker_sessions` query (every status, not `running`-only). Corrects an earlier `status='running'` draft | `02` §4.1, §10.1 | verbatim — only the corrected node-scoped-all-status form appears |
| §5 | Intra-node fan-out execution — work item's asyncio task `gather()`s node launches, waits on all before advancing | `02` §11 | verbatim |
| §6 | Cap breach under node concurrency — siblings not killed; new `worker_sessions.status = capped_out`; `needs_human` only when every task in the node is terminal. Real revision of §7's original behavior | `02` §7.1, §4.3 | verbatim |
| §7 | Self-review | — | dropped (working notes) |
| §8 | Follow-on — `chain_template` id flows through to bead metadata (survives independent of the orchestrator DB) | `03` §5 | verbatim |

---

## 6. `orchestrator_indexer_api_addendum_v1.md` → `02` §10, `04` §9

| Original | Decision / content | Landed in | How |
|---|---|---|---|
| §1.1 | Pause + steer collapse to one mechanism: kill the current attempt, optionally carry new context into the next | `02` §10.2 | verbatim |
| §1.2 | `work_items.status` enum `active/paused/needs_human/completed`; `worker_sessions.status` gains `paused`; `work_items.pending_steer_context`; new events `worker_session_paused`, `steer_context_set` | `02` §4.1, §4.3, §4.2 | merged into the consolidated schema |
| §1.3 | Endpoint behavior — `POST /pause` (SIGTERM current-node running rows, statuses → `paused`, work item → `paused`; HTTP-client tasks: withhold next poll), `/steer` (valid only while paused, writes `pending_steer_context`), `/resume` (relaunch as fresh rows, append steer context, no `retry_counters` increment) | `02` §10.2 | verbatim |
| §1.4 | Self-review | — | dropped (working notes) |
| §2 | `GET /work-items` list shape (full `chain_definition` + `chain_template` per item); `GET /work-items/{id}` detail shape (full row + node-scoped `worker_sessions` array) | `02` §10.1 | verbatim — federation adds `involved_repos`/`work_item_repos`/`root_merge_policy` |
| §3 | `GET /worker-sessions/{id}/log` — raw log contents, pull not stream, no pagination v1 | `02` §10.1 | verbatim |
| §4 | `GET /documents/{id}` — full `documents` row incl. `content` + resolved `document_links`; restates the shadow-data rule for bead docs | `04` §9 | amended — bead-doc caveat drops (no bead docs exist post-federation) |
| §5 | `GET /work-items/{id}/documents` — `document_links` scoped to the work item, joined with title/kind/path; chosen over a `/search` filter | `04` §9 | verbatim |
| §6 | Open questions (steer server-side validation, log pagination, `interactive: bool` in registry) | `02` §13, `05` §8 | merged |

---

## 7. `plugin_adapters_design_v1.md` → `03_plugin_adapters.md`

| Original | Decision / content | Landed in | How |
|---|---|---|---|
| §0 | Resolutions: Work-graph needs no MCP-client adapter; CI/MR gets a generalized HttpClientAdapter; Planning + one Review = thin variants, other Review + CI/MR = new usage | `03` §0, §1 | verbatim |
| §1 | Three adapter kinds (SubprocessAdapter / HeadlessAgentInvocation / HttpClientAdapter), layered; beads on plain SubprocessAdapter directly | `03` §1 | verbatim |
| §2 | Execution Worker — HeadlessAgentInvocation, Claude Code only; command sketch; context injection; subagent-driven-development skill; status callback; output schema; `plan_diverged` → guidance gate; `on.test.run` measurement-only + its schema | `03` §2 | amended — `on.implementation.start` fix mode documented (backward-motion) |
| §3 | Planning — same HeadlessAgentInvocation, config-only; two hook bindings + schemas | `03` §3 | verbatim — gate-reject re-invoke noted |
| §3a | Chain Review — HeadlessAgentInvocation with a **bespoke** skill; hook; proposed output schema (`revised_chain_nodes`); skill content TBD | `03` §3a | verbatim |
| §4 | Review — local (`ponytail-review` only, HeadlessAgentInvocation, Finding{} coercion, every iteration, gates `on.mr.open`) vs MR (`remote-review CLI` only, plain SubprocessAdapter, not in local loop, sync-assumption flagged) | `03` §4 | verbatim — fix-cycle cadence for the two added |
| §5 | Work-graph — SubprocessAdapter CLI only; hooks; thin `--json` passthrough; **chain-template flow-through to bead metadata required** | `03` §5 | amended — hydration hub responsibilities folded (federation §5) |
| §5a | Env-Prepare — plain SubprocessAdapter, deterministic; repo-defined convention (Makefile target / setup script); worktree add; output schema | `03` §5a | amended — multi-repo responsibilities + `involved_repos` output folded (federation §3.1) |
| §6 | CI/MR (GitLab) — HttpClientAdapter, keyring token, 4 hooks table, no `worker_sessions` row, polls emit events directly | `03` §6 | amended — per-repo operation + ordered-merge dance folded (federation §3) |
| §7 | Codex — deferred, Claude Code only this pass; known blocker (`--append-system-prompt`/`--mcp-config` equivalents unverified) | `03` §7 | verbatim |
| §8 | Reusability map | `03` §8 | verbatim |
| §9 | Suggested build order (7 steps) | `03` §11 | verbatim — fix-loop placement note updated (now resolved, not "parked") |
| §10 | Impact of chain model — gaps 1 (chain-review binding) & 2 (`on.test.run` read-only) resolved; **gap 3 (fix-then-retest loop) still open**; env.prepare elevated + resolved | `03` §9 | amended — **gap 3 resolved** by the backward-motion coordinator (`02` §7.2); option 1 (the doc's own lean) is what shipped |
| §11 | Open questions (remote-review CLI, `bd` CLI surface, Codex flags, Chain Review skill, draft schema field names) | `03` §10 | verbatim |

---

## 8. `indexer_search_backend_design_v1.md` → `04_indexer_search.md`

| Original | Decision / content | Landed in | How |
|---|---|---|---|
| §0 | Purpose + "⚠ Superseded in part by federation §4: bead ingestion removed" banner; open-tag `kind` settled going in | `04` §0 | amended — the banner's changes are simply applied; open-tag `kind` kept |
| §1 | Lives inside the orchestrator process; second SQLite file, disposable | `04` §1 | verbatim |
| §2 | Indexing triggers — no recurring poll; event-driven primary, startup full scan, `on.env.prepare` piggyback, manual `POST /index/rescan` | `04` §2 | amended — `on.intake`/`on.completed` → `documents` re-index replaced by hub `bd repo sync` |
| §3 | Schema — `documents` / `documents_fts` / `document_chunks` / `document_vectors` / `document_links`; `kind` open tag, `source_kind` closed enum | `04` §3 | amended — `source_kind` is 2 values (`artifact`/`session_summary`); all `documents.id` uuid; `document_links.work_item_id` stays (resolves against the hub) |
| §4 | Ingestion mechanics per `source_kind` — `bead` / `artifact` / `session_summary` | `04` §4 | amended — `bead` subsection deleted; `bd list --json` gone |
| §5 | Renamed / deleted content — per-repo per-kind diff, insert/delete/rename/re-extract rules, no tombstoning | `04` §5 | verbatim |
| §6 | Session summaries at `.engineering/sessions/`, `kind='session_summary'`, linkage front-matter (`work_item_ids`, `node_id`, `hook_point`, `worker_session_id`); cross-check against `worker_sessions` | `04` §6 | amended — fix-task self-summary (`node_id`=verify/mr_checks) added from `chain_backward_motion_addendum` §7; `.engineering/fix_notes/` confirmed not needed; the original's "gap the source docs leave open" framing dropped (`01` §5/§6 now enumerate `sessions/`) |
| §7 | Vector / semantic — `sqlite-vec` (not a separate engine), local embedding model, heading/paragraph chunking; all params placeholders | `04` §7 | verbatim |
| §8 | Beads boundary — write direction (Indexer never writes `bd`), read direction hard rule ("nothing that needs current state reads it from the Index"), "worth revisiting whether bead ingestion belongs here at all" | `04` §8 | **amended** — "resolved: removed"; write-direction rule kept; read-direction hard rule kept and generalized to the hydration hub |
| §9 | `GET /search?q=&source_kind=&kind=&repo=&mode=` — fts/vector/hybrid; resolved `document_links` inline | `04` §9 | amended — `source_kind` filter loses `bead`; `GET /beads/search` noted as the sibling; document-fetch endpoints added from api addendum |
| §10 | Open questions (embedding model, chunking defaults, `bd` type field, fusion weights) | `04` §10 | amended — the `bd`-type-field item drops with bead ingestion; the other three kept |
| §11 | Build order — step 3 "Bead ingestion" deleted, renumbered | `04` §11 | amended — applied |

---

## 9. `ui_design_v1.md` → `05_ui.md`

| Original | Decision / content | Landed in | How |
|---|---|---|---|
| §0 | Purpose — local SPA over the assembled API surface | `05` §0 | verbatim |
| §1 | Stack — React, no meta-framework, static assets served by the orchestrator, no component library | `05` §1 | verbatim |
| §2 | Client state — one global WS, normalized store keyed by `work_item_id`, event reduction list, REST as bootstrap/reconcile, reconnect by `after_seq` | `05` §2 | amended — `fix_cycle_started`, `involved_repos_resolved`, `worker_session_paused`, `steer_context_set` added to the reduced events |
| §3 | Auth flow — boot with `GET /health`, 200→board / 401→login, cookie server-side, 401 mid-session bounces to login store-intact | `05` §3 | verbatim |
| §4.1 | Board — flat card list (not kanban), card contents, full chain graph strip, `chain_template` filter, "New Work Item" button | `05` §4.1 | amended — repo cluster on multi-repo cards + `involved_repo` filter (federation §6.2) |
| §4.1a | New Work Item modal — repo/title/chain-template fields, submit → `POST /work-items`, navigate to detail on success, inline error on failure | `05` §4.1a | amended — "Advanced / cross-repo" disclosure (submodules + root merge policy) |
| §4.2 | Work Item Detail — chain stepper, current-node panel (one row per task, status chips, awaiting-gate inference), pause/resume/steer controls + steerable-hook table, event timeline + log viewer, linked documents panel | `05` §4.2 | amended — repos panel added above the stepper (federation); fix-task row badged "fix · cycle N" (backward-motion); steerable table unchanged |
| §4.3 | Search — global overlay, `mode=hybrid` default + advanced filters, results list, click-through by `source_kind` (bead → work item detail; artifact/summary → document viewer); hard rule "nothing from a search result is treated as current" | `05` §4.3 | amended — bead click-through removed; separate live bead-search box via `GET /beads/search` |
| §4.4 | Document Viewer — read-only markdown, `GET /documents/{id}`, metadata side panel, no edit/save, bead-doc "labeled as lagging" caveat | `05` §4.4 | amended — bead case deleted (no bead documents exist) |
| §4.5 | Gates — inline on the chain stepper, approve/reject buttons, "reject semantics explicitly undefined system-wide; UI displays whatever the event stream shows" | `05` §4.5 | **amended** — reject is now defined (required `{ note }`, per-gate re-invoke behavior); UI reflects the deterministic state |
| §5 | System Status — `GET /health` indicator (surfaces Chain Validator problems), manual per-repo "Rescan" → `POST /index/rescan` | `05` §5 | verbatim |
| §6 | What "live" means — task/node state transitions over the event bus, not a raw stdout tail; what's explicitly not live (raw output) | `05` §6 | verbatim |
| §7 | API surface consumed (enumerated) | `05` §7 | verbatim — federation additions included |
| §8 | Open questions (capped_out/failed/unknown iconography, steerable table server-side, CSS approach, mobile layout) | `05` §8 | amended — iconography item extended to cover the fix-task badge / bumped task-count (`chain_backward_motion_addendum` §8); others kept |
| §9 | Build order (9 steps) | `05` §9 | verbatim — repos panel folded into step 4 |

---

## 10. `cross_repo_federation_design_v1.md` → `06_cross_repo_federation.md` (+ applied across `01`–`05`)

| Original | Decision / content | Landed in | How |
|---|---|---|---|
| §0 | Purpose + the 3 deferred things it resolves | `06` §0 | verbatim |
| §1 | Federation research — `bd federation` (P2P Dolt replication) out of scope; multi-repo hydration is the real mechanism; cross-repo deps/links on hydrated rows; `bd migrate issues`; no first-class multi-repo object; submodule-nested topology assumption | `06` §1 | verbatim |
| §2 | Data model — `work_items.repo` → "root"; new `work_item_repos` table (full column list); `work_items.root_merge_policy`; why a table not JSON; beads side (anchor + linked sub-beads, lazy at `open_mr`) | `06` §2, `02` §4.5 | verbatim |
| §3 | Env-prep resolution + repo-set-aware `open_mr`/`mr_checks`/`merge`; §3.1 env-prep steps + output schema; §3.2 late discovery (before/at-or-after `open_mr`); §3.3 `open_mr` per policy; §3.4 `mr_checks` (root not gated on green under `bump`); §3.5 `merge` ordered walk + mid-walk failure (partial progress survives, idempotent per row); §3.6 retry caps (one `ci_loop` counter, ~3× burn) | `06` §3 | verbatim |
| §3a | Root-bump policy table (`bump`/`skip`/`bump_no_mr`); set at intake or chain review; empty submodule set → ignored; late-discovery interaction | `06` §3a | verbatim |
| §4 | Indexer bead ingestion removed — rationale, schema changes, ingestion deletion, trigger change, search-surface change, build-order change | `06` §4, applied in `04` | verbatim |
| §5 | The hydration hub — what it is (`primary: "."`, no beads of its own), reads here / writes to owning repo, sync (`export.auto` required, lags one cycle), when it syncs, failure modes | `06` §5, `03` §5.1 | verbatim |
| §6 | API deltas (`POST /work-items` new fields, `GET /work-items` list/detail additions, new `GET /beads/search`) + UI deltas (repo cluster, repos panel, intake advanced fields, search/viewer rework) | `06` §6, applied in `02` §10 / `05` | verbatim |
| §7 | "Documents this component amends" pointer table | `06` Changelog + per-doc Changelogs | merged — redistributed |
| §8 | Non-goals (no `bd federation`, no arbitrary multi-repo, no blanket submodule discovery, no transactional cross-repo merge, no semantic search over beads) | `06` §7 | verbatim |
| §9 | Open questions (`bd repo sync` fallback poll, `ci_loop` tuning, `bump_no_mr` allowlist, late-discovery command set) | `06` §8 | verbatim |
| §10 | Build order (9 steps) | `06` §9 | verbatim |

---

## Superseded Drafts — Confirmed Dropped

These were explicitly corrected *within* the originals; the clean docs carry only the
final form.

| Draft | Superseded by | Where the final form is |
|---|---|---|
| Bespoke `chain_review_requested` / `chain_finalized` event types | `chain_flow_model_addendum` §9 correction + `orchestrator_core_addendum` §3 | Generic `gate_*` events with `chain_finalized` as the gate name — `02` §4.2 |
| `GET /work-items/{id}` returns `worker_sessions` filtered to `status='running'` | `orchestrator_core_addendum` §4 correction | Node-scoped, all statuses — `02` §10.1 |
| Ad hoc / on-the-spot chain composition, raw-YAML-at-runtime endpoint | `orchestrator_core_addendum` §2 | Template selection by id only; listed as a non-goal — `01` §3.6 |
| `work_items.phase` (string), fixed 16-row phase table | `chain_flow_model_addendum` §3, §8 | `current_node_id` + `chain_definition` + chain model — `01` §3, `02` §4.1 |
| `work_items.current_worker_session_id` (singular pointer) | `orchestrator_core_addendum` §4 | Node-scoped query — `02` §4.1, §10.1 |
| `source_kind='bead'`, `bd list --json` ingestion, bead rows in `documents` | `cross_repo_federation_design` §4 | Beads not indexed; hydration hub — `04` §8, `06` §5 |
| "Reject semantics are explicitly undefined system-wide" | `chain_backward_motion_addendum` §4 | Defined per-gate re-invoke behavior — `02` §7.2, `01` §8 |
