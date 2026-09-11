# Plugin Adapters

> **Status:** historical design record. It captures why the system was designed
> this way, and is not maintained against the code. Current intended behaviour
> lives in `docs/intent/` (today: `gates` only).

> Component 2 of the decomposition (`01_conceptual_model.md` §12): the **first-party
> plugin adapters** — work-graph (beads), planning, execution worker, env-prepare,
> chain review, review, CI/MR. Every plugin conforms to the plugin contract (`01`
> §4) and, where subprocess-based, the subprocess adapter contract
> (`02_orchestrator_core.md` §6).

## 0. Purpose

Refines the conceptual model and orchestrator core. Resolves:

- Work-graph needs **no** MCP-client adapter alongside CLI (§1).
- CI/MR gets a **generalized** HttpClientAdapter, not a one-off GitLab client (§1).
- Planning and one Review plugin are **thin variants** of Execution Worker's adapter;
  the other Review plugin and CI/MR are genuinely new adapter usage (§8).

Chains (ordered nodes, concurrent-eligible tasks) do not change hook points, plugin
contracts, or default bindings — see §9 for what did need attention.

---

## 1. Adapter Kinds

Three kinds, layered rather than parallel:

```
┌────────────────────────────────────────────────────────────┐
│ SubprocessAdapter (generic)                                 │
│ launch / detach / log-redirect / result-file-via-env-var / │
│ timeout — per 02_orchestrator_core.md §6.1                  │
└───────────────┬───────────────────────────────┬────────────┘
                │                               │
                ▼                               ▼
┌───────────────────────────┐   ┌───────────────────────────────┐
│ HeadlessAgentInvocation   │   │ (used directly, no extra layer)│
│ + system-prompt injection │   │ beads CLI, env-prepare,        │
│ + per-invocation MCP      │   │ remote-review CLI              │
│   status-callback server  │   └───────────────────────────────┘
└───────────────┬───────────┘
                │
   used by: Execution Worker (×2 hooks), Planning (×2), Chain Review, ponytail-review

┌────────────────────────────────────────────────────────────┐
│ HttpClientAdapter (generic)                                 │
│ base URL + keyring-sourced token + retry/backoff +          │
│ integration with policy engine's loop-bounded caps          │
└───────────────┬────────────────────────────────────────────┘
                │
   used by: CI/MR (GitLab) — only consumer today
```

- **SubprocessAdapter** — the generic mechanics from `02` §6.1, implemented once,
  reused by every subprocess-based plugin regardless of what's inside the subprocess.
- **HeadlessAgentInvocation** — a layer on top, specific to "launch a headless coding
  agent session": adds system-prompt injection (context-injection boundary, `01`
  §10) and the per-invocation MCP status-callback server. Parameterized by target
  binary, skill/prompt to inject, declared output schema, timeout. Claude Code is the
  only target this pass (§7).
- **HttpClientAdapter** — sibling to SubprocessAdapter, not a variant: no process,
  PID, log file, or reattach story. Generalized now even though GitLab is the only
  consumer, because auth + retry + poll-against-a-cap is what any future HTTP plugin
  needs.
- **Work-graph (beads) uses plain SubprocessAdapter directly** — no MCP-client
  adapter kind. Beads' CLI is JSON-native; its MCP server is an alternative
  transport, not an added capability. Work-graph operations are short point-operations
  (intake, status update, close, ready-query), so reattach barely matters. Revisit
  only if a needed beads capability has no CLI equivalent.

---

## 2. Plugin: Execution Worker

- **Adapter:** HeadlessAgentInvocation, Claude Code target only (§7).
- **Command template (sketch):** `claude -p "<injected prompt>" --append-system-prompt
  <context> --mcp-config <per-invocation MCP config path> --output-format json`,
  cwd = the work item's worktree.
- **Context injection:** system prompt assembled from the approved plan, spec refs,
  and any steering context — per-invocation only, never written to
  `CLAUDE.md`/`AGENTS.md` (`01` §10).
- **Skill invoked:** superpowers *subagent-driven-development*, via the injected
  prompt text.
- **Status callback:** per-invocation MCP server; worker self-reports progress/usage;
  orchestrator writes `worker_sessions.usage_reported_json`.

Two invocation modes on `on.implementation.start`, one output schema:

**Normal mode** — implement the approved plan.

```
{ status: "done" | "plan_diverged" | "error",
  session_summary_ref: string,
  files_changed: [string],
  commits: [string],
  usage: {...} }
```

`session_summary_ref` is parsed straight into `worker_sessions.session_summary_ref`
(`02` §6.1) — the only writer of that field. `status: "plan_diverged"` → orchestrator
routes to the guidance gate (`01` §8) instead of marking the hook complete.

**Fix mode** (backward-motion, `02` §7.2 entry point A) — same adapter, same output
schema. Input adds a fix-scoped injected prompt ("the following checks failed against
the current diff; fix them, make no unrelated changes") plus the structured failure
payload (`failures[]` / `findings[]` / pipeline job-log refs). `done` /
`plan_diverged` / `error` are all already handled; `plan_diverged` routes to the
guidance gate as before. Exact prompt text is a build-time detail.

**`on.test.run` — measurement only, confirmed.** Same adapter, a distinct invocation
from `on.implementation.start`. The injected prompt scopes the session to running the
test suite and reporting — no edits. This is what makes the `verify` node's fan-out
with ponytail-review valid under the read-only-fan-out rule (`01` §3.3): both tasks
read the same frozen diff, neither mutates it.

```
{ status: "green" | "red" | "error",
  failures: [{ test: string, message: string }] }
```

Fixing on a red result is a separate step — the backward-motion coordinator's fix
loop (`02` §7.2), not a branch in the chain.

---

## 3. Plugin: Planning

- **Adapter:** the same HeadlessAgentInvocation as Execution Worker, reused
  wholesale. Only the config differs — the confirmed thin-variant case.
- **Two hook bindings, one adapter class:**

  | Hook | Skill | Output schema |
  |---|---|---|
  | `on.spec.requested` → `on.spec.produced` | superpowers *brainstorming* | `{ status: "ready_for_approval" \| "error", spec_artifact_ref: path }` |
  | `on.plan.requested` → `on.plan.produced` | superpowers *writing-plans* | `{ status: "ready_for_approval" \| "error", plan_artifact_ref: path }` |

- Status-callback server included for consistency even though these are short,
  non-looping invocations.
- Gate rejection (`spec_approval` / `plan_approval`) re-invokes the corresponding hook
  here via the backward-motion coordinator, with the rejection note injected (`02`
  §7.2 entry point B).

---

## 3a. Plugin: Chain Review

- **Adapter:** HeadlessAgentInvocation, same pattern as Planning — but the skill is
  **bespoke**, not superpowers. Chain review needs its own guidance: how to weigh a
  finalized spec/plan against the loaded chain, what "add a security-review task" or
  "lighten verify" means in practice, heuristics for when to adjust vs. leave alone.
  Building that skill is its own piece of work, separate from wiring the adapter.
- **Hook:** `on.chain.review_ready` → `on.chain.finalized`, `gate_after:
  chain_finalized` (`01` §3.5). Fires once, immediately after plan approval, only
  when the loaded chain has a plan node.
- **Output schema:**
  ```
  { status: "ready_for_approval" | "error",
    revised_chain_nodes: [Node, ...],
    rationale: string }
  ```
  `revised_chain_nodes` covers only the not-yet-executed portion of the chain, and
  carries it in full — it is a replacement tail, not a diff; the orchestrator splices
  it into `work_items.chain_definition` on approval (`02` §4). `Node` is the
  materialized shape `{ id, tasks, gate_after, fix_loop }` (`02` §12).
  Mirrors Planning's `*_artifact_ref` shape — same adapter family.

  `rationale` was added when the skill was written: `chain_finalized` is a human
  gate, and a chain diff arriving with no stated reason makes that gate decorative.
  It also gives the skill somewhere to report a change it judged necessary but could
  not express — the needed hook not being registered for the repo — instead of
  silently approximating it with a different task.
- Gate rejection (`chain_finalized`) re-invokes `on.chain.review_ready` via the
  coordinator, note injected (`02` §7.2 entry point B).
- **Skill:** `chain-review`, authored at `src/kraft/skills/chain-review/SKILL.md`. It sits
  beside `templates/` because it is a product artifact injected into the headless
  session, not guidance for agents working on Kraft itself (`.agents/skills/`).
- **Allowed hook set is injected, not assumed.** The adapter passes the hook points
  registered *and enabled for the target repo* — a hook can be globally registered
  and disabled per repo (`02` §12, resolution tier). Without it the skill has no way
  to know which tasks it may compose and would invent hook names.

---

## 4. Plugin: Review — Local Loop vs. MR Loop

The flow model separates review into two hooks: `on.review.local.run` (pre-MR,
loop-bounded) and `on.review.mr.run` (post-MR-open, loop-bounded, CI/MR category).
**One plugin per hook, not both plugins merged onto both** — and the split is a
deliberate cost/thoroughness tradeoff, not just adapter convenience.

**`on.review.local.run` → ponytail-review only.**
- Adapter: HeadlessAgentInvocation (Claude Code + `/ponytail:ponytail-review` skill).
  Ponytail's native output is one line of prose per finding; the injected system
  prompt carries an added instruction to write findings to the result file as
  `Finding{severity, message, file, line, source_plugin}` instead of prose. Scope
  stays as ponytail defines it — over-engineering and complexity only, not
  correctness or security.
- Runs every iteration of the local loop, up to the loop-bounded cap — cheap and
  local enough to be the default filter.
- A clean result gates progression to `on.mr.open`.

**`on.review.mr.run` → remote-review CLI bot only.**
- Adapter: plain SubprocessAdapter, *not* HeadlessAgentInvocation — a blocking CLI
  call that kicks off a review on a remote service and returns output on exit, not an
  agent session.
  > **Unreconciled across the source docs:** §6's GitLab hook table lists
  > `on.review.mr.run` as `GET discussions/notes` (HttpClientAdapter), and
  > `orchestrator_indexer_api_addendum_v1.md` §1.3 groups it with the
  > HttpClientAdapter-backed poll tasks. This section (from `plugin_adapters_design_v1.md`
  > §4) treats it as a SubprocessAdapter CLI bot. The tool itself is a placeholder, so
  > the adapter kind resolves when it is identified. Either way it is not steerable and
  > it is not run during the local loop. No prompt injection, no status-callback server. Command template,
  auth (assumed keyring-sourced token, GitLab pattern), timeout are placeholders
  until the tool is identified. Output mapped onto `Finding{}` via a small
  plugin-specific translation step.
- Deliberately **not** run during the local loop — let cheap local tooling catch what
  it can before spending the slower/costlier remote bot. Fires only once the local
  loop is clean and the MR is open, alongside `on.ci.poll`.
- In a `mr_checks` fix cycle (`02` §7.2): `on.ci.poll` re-runs every cycle;
  `on.review.mr.run` runs **only once `on.ci.poll` is green**, once more in the final
  cycle, not per cycle — preserving the cost rationale.
- **Assumption flagged:** treated as synchronous (blocks, exits with results). If
  actually async (kicks off a job, returns an ID, needs polling), the adapter usage
  shifts to a kickoff + poll-loop shape closer to the CI loop (§6). Confirm once the
  tool is in hand.

Both hooks stay on the shared `Finding{}` schema regardless of how many plugins bind
today — a second reviewer on either loop merges through the schema with no adapter
change; `source_plugin` disambiguates.

---

## 5. Plugin: Work-graph (beads)

- **Adapter:** SubprocessAdapter, CLI only (§1).
- **Hooks:** `on.intake` (roughly `bd create` / `bd show`), `on.completed`
  (`bd close`, `bd update`) — exact `bd` invocation surface to be pinned against the
  current CLI reference when built. Neither shipped template (`01` §3.2) carries an
  explicit completion node — the templates end at `merge`; `on.completed` fires on
  merge-node completion (it also drives Indexer/Notification per `01` §5). Whether it
  warrants its own trailing node is an open template-design question.
- Short synchronous calls — no per-invocation MCP server, no meaningful
  detach/reattach concern, though the mechanism is available via SubprocessAdapter if
  a slower `bd` operation ever needs it.
- **Output schema:** thin passthrough of `bd`'s own `--json` output, mapped to the
  minimal shape the orchestrator needs (bead id, status).
- **Chain-template flow-through (required — `orchestrator_core_addendum_v1.md` §8):**
  `on.intake` is dispatched via
  `POST /work-items { repo, title, chain_template? }` (`02` §10.1), and the resolved
  `chain_template` id must be persisted into the bead's own metadata, not just the
  orchestrator's DB — otherwise a work item's chain choice only exists in SQLite and
  doesn't survive independent of it, breaking the git-native source-of-truth
  principle (`01` §1.3). Exact mechanism (`bd` custom field, tag, description block —
  whatever `bd` supports for arbitrary metadata) is TBD alongside the rest of this
  section's placeholder CLI surface.

### 5.1 Hydration Hub (federation)

Work-graph also owns the **hydration hub** — full design in
`06_cross_repo_federation.md` §5. Summary of the plugin's responsibilities:

- A dedicated beads workspace (`~/.orchestrator/beads-hub/`, `primary: "."`, holds no
  beads of its own) with every connected repo in `repos.additional`.
- All cross-repo bead **reads** run against the hub (`bd -C <hub>
  ready|list|dep tree|search|blocked`). **Writes** always resolve to the owning repo,
  never the hub.
- Each connected repo needs `export.auto=true` and `export.git-add=true` — one-time
  setup per repo.
- `bd repo sync` runs after every orchestrator-caused bead write (`on.intake`,
  `on.completed`, sub-bead creation), piggybacked on `on.env.prepare`, and as a full
  sweep at orchestrator startup — no standalone poll loop.
- Nothing that gates a decision reads hub state (it lags one export+sync cycle);
  gating reads hit the owning repo's `bd` directly.
- For a multi-repo work item, each co-developed submodule with `.beads/` gets a
  linked sub-bead (`bd create --repo <submodule>`), linked to the anchor bead with
  `bd dep add <sub-bead> <anchor> --type parent-child`, created lazily at `open_mr`
  time once the diff is known.

---

## 5a. Plugin: Env-Prepare

- **Adapter:** plain SubprocessAdapter, no HeadlessAgentInvocation — confirmed
  deterministic, not agent-invoking.
- **Hook:** `on.env.prepare` (the `env_setup` node — runs after chain review, before
  implementation).
- **Command template:** invokes a repo-defined convention, not a hardcoded command —
  a `Makefile` target (e.g. `make setup`) or an `.engineering/setup.sh` script,
  resolved per-repo via the plugin registry's per-repo config tier. Exact discovery
  order (Makefile first vs. script first, behavior if neither exists) is a low-stakes
  build-time detail.
- Short synchronous call — no status-callback server.

**Single-repo responsibilities:**

1. `git worktree add` the root repo.
2. Run whatever the repo's own convention does (dependency install, env scaffolding).

Output schema:

```
{ status: "ready" | "error",
  worktree_path: string }
```

**Multi-repo responsibilities (federation, `06_cross_repo_federation.md` §3.1):**
after `git worktree add` on the root, in order —

1. Resolve the submodule subset: the `submodules` list from intake if given, else
   parse `.gitmodules` and take **none** by default. Never all.
2. `git submodule update --init -- <resolved paths>` — selective, never blanket.
3. For each resolved submodule: `git worktree add` inside it on the work item's
   branch; write a `work_item_repos` row (`role='submodule'`, `submodule_path`,
   `merge_rank`, `bead_id` if that repo has `.beads/` else null).
4. Compute `merge_rank` topologically (nested submodule outranks its parent, root = 0).
5. Emit `involved_repos_resolved`.
6. Run the late-discovery diff scan as the last step of the implementation node.

Extended output schema:

```
{ status: "ready" | "error",
  worktree_path: string,
  involved_repos: [ { repo_path, role, submodule_path, merge_rank } ] }
```

`worktree_path` is what every downstream hook resolves `cwd` from — a new source for
a value the orchestrator already needs, no orchestrator-side change expected.

---

## 6. Plugin: CI/MR (GitLab)

- **Adapter:** HttpClientAdapter (§1).
- **Auth:** GitLab access token in macOS Keychain via `keyring`, matching `02` §9's
  secrets pattern.
- **Hooks:**

  | Hook | Call | Notes |
  |---|---|---|
  | `on.mr.open` | `POST merge_requests` | |
  | `on.ci.poll` | `GET pipelines` | loop-bounded; policy engine cap checked before each poll |
  | `on.review.mr.run` | `GET discussions/notes` | loop-bounded; findings → `Finding{}`, `source_plugin: "gitlab"`. **Adapter kind conflicts with §4** (remote-review CLI on SubprocessAdapter) — resolves when the review tool is identified |
  | `on.merge` | `PUT merge_requests/:iid/merge` | |

- No process, PID, or log file — HttpClientAdapter's unit of work is a
  request/response pair, logged to the event bus, no `worker_sessions` row analog.
- **Decided:** GitLab polls don't get a dedicated audit table — they emit events
  directly onto the event log (e.g. `ci_poll_result`, `mr_review_result`).

**Multi-repo (federation, `06_cross_repo_federation.md` §3):** for a multi-repo work
item, `on.mr.open` / `on.ci.poll` / `on.review.mr.run` / `on.merge` operate **per
repo**, driven by the `work_item_repos` rows. `on.mr.open` opens one MR per repo with
a non-empty diff (empty → `merge_state='skipped'`, no MR). The ordered-merge dance —
walk `work_item_repos` by `merge_rank` descending, merge each submodule MR
deepest-first, stage+commit the submodule-pointer bump in the parent repo, then apply
`root_merge_policy` for the root — lives in the `on.merge` hook's node logic
(`06_cross_repo_federation.md` §3.5). A one-repo `work_item_repos` list reduces this
to the original single-MR behavior exactly.

---

## 7. Codex (explicitly deferred, not built this pass)

Execution Worker ships Claude Code only. Codex is added later "if feasible and if we
have enough feature coverage." HeadlessAgentInvocation's target binary/flags are a
resolved-from-config parameter specifically so a Codex target slots in without
re-architecting the layer — but no Codex-specific code ships now.

**Known blocker for whenever Codex work resumes:** `codex exec` has no first-class
per-invocation equivalent to Claude Code's `--append-system-prompt` / `--mcp-config`.
Closest candidates are `-c developer_instructions=...` and `-c mcp_servers.*=...`
config overrides; it's unverified whether the latter supports inline MCP server
definitions the way Claude Code's `--mcp-config` file does. Codex also leans on
`AGENTS.md`/persistent config by default, which runs against the context-injection
boundary (`01` §10) if not handled carefully. Verifying the override behavior is the
first task when Codex work resumes.

---

## 8. Reusability Map

- **SubprocessAdapter** (generic) — shared by every plugin in this component.
- **HeadlessAgentInvocation** (on top of SubprocessAdapter) — shared by Execution
  Worker (×2 hooks), Planning (×2 hooks), Chain Review, ponytail-review.
- **Per-plugin code** — reduced to config wherever possible: command template,
  skill/prompt to inject, declared output schema, any schema-coercion instructions
  (ponytail-review's `Finding{}` instruction is the only one needed so far).
- **HttpClientAdapter** — sibling to SubprocessAdapter, used only by GitLab today.

Beads CLI and the remote-review CLI sit directly on SubprocessAdapter with no shared
layer between them — they don't resemble each other closely enough to warrant one.

---

## 9. Impact of the Chain-Based Flow Model

**Confirmed, no change needed:** `default.yaml` groups `on.test.run` +
`on.review.local.run` into one concurrent `verify` node, and `on.ci.poll` +
`on.review.mr.run` into one concurrent `mr_checks` node — which matches §4's
local-loop/MR-loop split exactly. The concurrency is new; the hook-to-plugin mapping
isn't.

**Resolved:**

1. `on.chain.review_ready` → `on.chain.finalized` has a binding — §3a, a bespoke
   skill (new authored content).
2. `on.test.run` is confirmed read-only — measurement only. The `verify` node's
   fan-out is valid as designed (§2).
3. `on.env.prepare` is confirmed deterministic — plain SubprocessAdapter,
   repo-defined convention, no LLM (§5a).
4. **"How does fix-then-retest loop, given chains don't branch?"** — resolved by the
   **backward-motion coordinator** (`02` §7.2): the policy engine coordinates a paired
   `on.implementation.start` (fix mode, §2) then re-runs the node's measuring tasks,
   under one `<node>_fix_loop` counter. The node list stays static; `current_node_id`
   never moves. This was the leaning option in the original draft ("reuse
   `on.implementation.start` as-is, keep the node list static") and it landed as the
   chain-backward-motion addendum.

---

## 10. Open Questions / Placeholders

- Remote-review CLI: actual tool, command shape, auth mechanism, sync vs. async —
  needed before that half of Review can be built (§4).
- `bd` CLI: exact command surface for intake/completion, plus how it stores arbitrary
  metadata for the `chain_template` field — pin down against the current reference
  when built (§5).
- Codex per-invocation system-prompt/MCP-config equivalent — unverified; blocks Codex
  work specifically (§7).
- Output schema field names throughout are draft — not yet cross-checked against what
  the Indexer or UI will actually consume.
- Findings severity threshold for the fix loop — v1 loops on any non-empty
  `findings[]` (`01` §11).
- Fix-scoped prompt text for `on.implementation.start` fix mode (§2) — the exact
  injected instruction and failure-payload formatting. Build-time detail.
- `on.review.mr.run` sync vs. async affects the `mr_checks` fix-cycle "once more after
  CI green" step — a localized revisit when the tool is identified, not a rework.

---

## 11. Build Order

1. **Execution Worker (Claude Code)** — builds SubprocessAdapter and
   HeadlessAgentInvocation from scratch; exercises the full subprocess contract
   including the status-callback server and `session_summary_ref`.
2. **Work-graph (beads CLI)** — simplest, needed for intake regardless; validates
   SubprocessAdapter alone.
3. **Env-Prepare** — same profile as Work-graph, plain SubprocessAdapter, confirmed
   deterministic; natural to build alongside it.
4. **Planning** — near-zero marginal cost once (1) exists; two hook bindings, config
   only.
5. **Chain Review** — same marginal-cost logic as Planning. The skill it depends on
   is written (`src/kraft/skills/chain-review/SKILL.md`), so this is adapter wiring only:
   bind `on.chain.review_ready`, inject the skill plus the repo's enabled hook set,
   and validate the returned tail before splicing.
6. **Review** — ponytail-review reuses (1)'s machinery directly; remote-review CLI is
   new SubprocessAdapter usage plus a `Finding{}` translation shim.
7. **CI/MR (GitLab)** — the one genuinely new adapter kind (HttpClientAdapter); stands
   alone, built and tested independently.

The fix-loop mechanism is a policy-engine change plus a fix-scoped prompt config, not
a new plugin (`02` §7.2) — it lands after Execution Worker (1) and the policy engine
exist, before or alongside Review (6).

---

## Changelog

Consolidated from `plugin_adapters_design_v1.md`, with amendments from
`chain_backward_motion_addendum_v1.md` §7, `cross_repo_federation_design_v1.md` §7,
and `orchestrator_core_addendum_v1.md` §8 folded in.

- **§2 Execution Worker** — `on.implementation.start` gains a documented **fix mode**
  (fix-scoped prompt + structured failure payload as input; output schema unchanged)
  per `chain_backward_motion_addendum_v1.md` §7.
- **§4 Review** — the `mr_checks` fix-cycle cadence for `on.ci.poll` (every cycle) vs.
  `on.review.mr.run` (once more after CI green) added per
  `chain_backward_motion_addendum_v1.md` §7.
- **§5 Work-graph** — the chain-template → bead-metadata flow-through rule is credited
  to `orchestrator_core_addendum_v1.md` §8. Hub workspace config, sub-bead creation +
  `parent-child` linking, `bd repo sync` triggers, per-repo `export.auto` requirement
  folded from `cross_repo_federation_design_v1.md` §5 / §2.5.
- **§5a Env-Prepare** — multi-repo responsibilities (resolve submodule subset,
  selective `--init`, per-submodule worktrees, `work_item_repos` rows, `merge_rank`,
  late-discovery scan) and the `involved_repos` output field folded from
  `cross_repo_federation_design_v1.md` §3.1.
- **§6 CI/MR** — per-repo operation and the ordered-merge dance folded from
  `cross_repo_federation_design_v1.md` §3.
- **§9 gap #3 (fix-then-retest loop)** — was "still open, may not be a plugin-adapters
  question at all." **Resolved** by the backward-motion coordinator (`02` §7.2). The
  original's leaning option (option 1: reuse `on.implementation.start`, static node
  list) is what shipped.
- Original §10's parked fix-loop discussion and §11's "left alone for now" list are
  reflected as resolved above; remaining genuine placeholders retained in §10.
- **§3a Chain Review** — skill name and location resolved: `chain-review`, authored at
  `src/kraft/skills/chain-review/SKILL.md` (Kraft-nk7). The output schema loses its "proposed,
  unconfirmed" label and gains `rationale`, and the adapter's obligation to inject the
  repo's *enabled* hook set is stated. §10's "Chain Review skill: name and actual
  content" placeholder is retired; §11 step 5 is no longer blocked and is now adapter
  wiring only.
