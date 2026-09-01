# Cross-Repo / Federation

> Component 5 of the decomposition (`01_conceptual_model.md` §12): **cross-repo /
> federation** — wiring up work items that span more than one repository. The last
> component, held open from the start on one research item: nobody had read beads'
> federation docs. That research is done (§1); everything below assumes what beads
> actually offers, not what `01` §7 originally speculated.

## 0. Purpose

Also resolves three things earlier components deferred here:

1. Whether bead ingestion belongs in the Indexer once cross-repo bead queries can be
   answered natively — **no, removed** (§4).
2. `work_items.repo` as a single column — **stays, meaning tightens to "root repo,"**
   a new `work_item_repos` table carries the rest (§2).
3. `POST /work-items` body — **gains two optional fields; single-repo intake
   unchanged** (§2, §6).

---

## 1. Federation Research — What Beads Actually Offers

`01` §7 guessed that "beads' relation/dependency mechanism" plus something called
federation would link cross-repo work items. Beads has **three** distinct cross-repo
mechanisms, and the one literally named "federation" is the wrong one.

### 1.1 `bd federation` — peer-to-peer database replication (out of scope)

`bd federation` synchronizes **entire Dolt databases** between **independent teams or
organizations**. `bd federation add-peer <name> <endpoint>`, `bd federation sync
--strategy ours|theirs`, data-sovereignty tiers T1–T4 (GDPR / regional compliance),
topologies (hub-spoke, mesh, hierarchical). It requires a CGO build and the Dolt
backend. A "peer" is another org's whole issue database.

A solo user, one machine, ~10 repositories is none of that — wrong shape, wrong
scale. **`bd federation` is out of scope for this system entirely.** The "solo-first,
single machine" principle (`01` §1.1) settles it.

### 1.2 Multi-repo hydration — the actual cross-repo query mechanism

`bd repo add <path>` / the `repos.additional` config list. `bd repo sync` reads each
listed repo's `.beads/issues.jsonl` export and imports its beads into one aggregating
database, each tagged with its `source_repo`. After that, `bd list`, `bd ready`,
`bd dep`, `bd search`, and `bd blocked` all operate across every hydrated repo as
ordinary rows. Routing (`routing.mode`) decides which repo a new bead lands in;
`bd create --repo <path>` overrides per bead.

This is what the Indexer's "beads boundary" question was actually asking about —
"cross-repo bead queries natively." This component adopts it (§5).

Caveat: `bd repo sync` reads the **JSONL export**, not Dolt-native history, so every
connected repo needs `export.auto` enabled, and the aggregated view lags by one
export+sync cycle. Acceptable because the aggregate is used for discovery only, never
for gating decisions (§5).

### 1.3 Cross-repo dependencies and links

- `bd dep add <local-id> external:<project>:<capability>` — an external reference,
  resolved at query time against the `external_projects` config map (name → path).
  Blocks the local issue until the named capability is "shipped" in the target
  project. Capability-grained, not issue-grained.
- On **hydrated** rows, `bd dep add <id-a> <id-b> --type blocks` works directly across
  repos — real issue-to-issue cross-repo dependencies.
- Non-blocking graph links also work on hydrated rows: `relates-to`, `parent-child`,
  `tracks`, `discovered-from`.
- Gate type `bead`: `bd create --type=gate --await-type=bead --await-id=<rig>:<id>` —
  a gate that resolves when a cross-rig bead closes.

### 1.4 `bd migrate issues`

`bd migrate issues --from <repo> --to <repo>` moves beads between repos (rewrites
`source_repo`), preserving dependencies with `--include closure`. Present in bd 1.2.2.
Not load-bearing for this design, noted for completeness.

### 1.5 Consequence

There is **no first-class "one work item, many repos" object** in beads. The native
shape is: an anchor bead in one repo, related beads in other repos (each with its own
`source_repo`), linked with `parent-child` / `relates-to` / `blocks`, and hydration
to query them together. Beads itself picks that model over a multi-repo primitive,
and so does this design (§2).

### 1.6 Repo topology assumption

The dominant cross-repo case is **submodule-nested**: a superproject plus one or more
git submodules, checked out as one worktree tree. Sibling top-level repos coordinated
together is explicitly not modeled (§8). Everything below is written for the submodule
case; a pure single-repo work item is the degenerate case of it (one repo,
`role='root'`, nothing else).

---

## 2. Data Model — The Involved-Repo Set

Full schema in `02_orchestrator_core.md` §4.5. Summary:

### 2.1 `work_items.repo` meaning tightens

Stays a column. Its meaning tightens to the **root repo** — the superproject for a
submodule-nested work item, or the sole repo for a single-repo work item. Every
existing single-repo work item is unaffected.

### 2.2 New table: `work_item_repos`

One row per repository in play (`work_item_id`, `repo_path`, `role`,
`submodule_path`, `merge_rank`, `bead_id`, `mr_ref`, `merge_state`; PK
`(work_item_id, repo_path)`).

- Every work item has **at least** the `role='root'` row (`merge_rank=0`,
  `repo_path = work_items.repo`), written at intake. A single-repo work item has
  exactly that one row, and every node in §3 that iterates this table sees a
  one-entry list and behaves exactly as the pre-component-5 design did.
- Submodule rows are written by the env-prep node (§3.1), not at intake.
- `merge_state='skipped'` marks a declared or discovered repo the final diff didn't
  touch (or the root under a `skip` policy, §3a) — kept as a row for the audit trail,
  never merged.

### 2.3 New column: `work_items.root_merge_policy`

Enum, default `bump`. Governs whether and how the root repo's submodule pointers are
updated once submodule MRs merge. Full behavior in §3a.

### 2.4 Why a table, not a JSON column

The `merge` node (§3.5) walks these rows in `merge_rank` order and mutates
`merge_state` / `mr_ref` one row at a time, mid-node, as each repo's MR merges — row-
at-a-time mutation under concurrent UI read, which a JSON blob on `work_items`
handles badly. Mirrors the `worker_sessions` precedent.

### 2.5 Beads side

The anchor bead lives in the root repo (`bd create --repo <root>`), and is the bead
`work_items` already points at. Each submodule that ends up with a non-empty diff
(and therefore its own MR) gets a linked bead created in that submodule's repo
(`bd create --repo <submodule>`), linked to the anchor with `bd dep add <sub-bead>
<anchor> --type parent-child`. Hydration (§5) makes the whole set one queryable graph.
`work_item_repos.bead_id` mirrors these links into orchestrator state. A submodule
with no `.beads/` gets a `work_item_repos` row with `bead_id=null` and is tracked for
MR/merge purposes only. Sub-beads are created lazily at `open_mr` time, once the diff
is known — not at env-prep, when it isn't.

---

## 3. Env-Prep Resolution and the Ordered Multi-MR Nodes

The `open_mr`, `mr_checks`, and `merge` nodes become **repo-set-aware**: each iterates
`work_item_repos` ordered by `merge_rank` descending (deepest submodule first, root
last). A one-entry list produces byte-identical behavior to the pre-component-5
design — this is **node-internal iteration, not chain branching.** The chain remains
a strictly sequential list of nodes; `01` §3.6's non-goals (no DAG, no re-entry,
static hand-authored node list) all still hold.

### 3.1 Env-prep node (`on.env.prepare`, `03_plugin_adapters.md` §5a)

New responsibilities, in order:

1. `git worktree add` the root repo (unchanged).
2. Resolve the submodule set:
   - `submodules` given at intake (§6) → that exact list, no others.
   - Omitted → parse `.gitmodules` and take **none** by default. A work item with no
     declared submodules and no later-detected submodule changes stays single-repo.
     Never "all submodules" — a project may carry many the work item doesn't care
     about.
3. `git submodule update --init -- <resolved paths>` — selective, never blanket.
4. For each resolved submodule: `git worktree add` inside it on the work item's
   branch; write a `work_item_repos` row (`role='submodule'`, `submodule_path`,
   `merge_rank`, `bead_id` if that repo has `.beads/` else null).
5. Compute `merge_rank` topologically from the submodule tree: a submodule nested
   inside another submodule outranks its parent. Root = 0.

Output schema (extends `03` §5a):

```
{ status: "ready" | "error",
  worktree_path: string,
  involved_repos: [ { repo_path, role, submodule_path, merge_rank } ] }
```

Env-prep emits `involved_repos_resolved` into the event log, parallel to
`chain_loaded`.

### 3.2 Late discovery

Implementation touches a submodule nobody declared. Detected by a diff scan run as
the last step of the implementation node (`git submodule summary` plus per-submodule
`git status --porcelain` in the worktree — exact command set is a build-time detail,
§9).

- **Caught before `open_mr`:** env-prep's resolution logic re-runs for the new path,
  a `work_item_repos` row is added, `merge_rank` recomputed. No chain disruption.
- **Caught at or after `open_mr`:** the chain is past the point where a new repo slots
  in cleanly. This trips ad hoc pause/steer (`01` §8) with an "undeclared repo
  `<path>` modified" message. The human adds it (re-run `open_mr` for that repo) or
  reverts the stray change. Not auto-handled — the one real edge this component
  introduces.

### 3.3 `open_mr` node

One `on.mr.open` task per repo whose diff is non-empty. Each: push the work item's
branch to that repo's remote, open an MR, write `mr_ref` and `merge_state='mr_open'`;
for a submodule with `.beads/`, create its linked sub-bead now (§2.5). A repo whose
diff is empty (a declared submodule that turned out untouched) → `merge_state='skipped'`,
no MR.

The root is handled per `root_merge_policy`:

- `bump` — root MR opened here, like any other repo. Its submodule-pointer commits
  still reference unmerged submodule SHAs at this stage — expected, corrected in
  `merge`.
- `skip` — no root MR; root row → `merge_state='skipped'` from this node onward.
- `bump_no_mr` — no root MR; root row stays `merge_state='pending'` (the pointer-bump
  commit happens directly in `merge`, §3.5 step 2).

Every later node passes over any row already `skipped`.

### 3.4 `mr_checks` node

`on.ci.poll` + `on.review.mr.run` per repo, concurrent across repos (each read-only
against its own frozen MR — satisfies `01` §3.3's read-only-fan-out rule).

- Submodule MRs: green CI + clean review → `merge_state='checks_green'`.
- Root MR (`bump` only): CI may be red here because it points at unmerged submodule
  SHAs. The node records the review outcome and a provisional CI result but does
  **not** gate the root on green. The root's real CI gate is in `merge`, after the
  pointer bump. Under `skip` / `bump_no_mr` there is no root MR and the node has
  nothing to check for the root.

The node completes when every submodule repo is `checks_green` and — under `bump` —
the root MR review is clean. A `capped_out` on any submodule's CI or review loop
makes the whole node terminal and drops the work item to `needs_human` (`02` §7.1,
unchanged).

Fix loop (backward-motion, `02` §7.2): a red submodule MR launches a fix task with
`cwd` = that submodule's worktree; commits pushed to that submodule's MR branch; that
repo's `on.ci.poll` re-runs. Still one `mr_checks_fix_loop` counter for the node (a
3-repo item burns it ~3× faster).

### 3.5 `merge` node — the ordered walk

Sequential walk of `work_item_repos` by `merge_rank` descending:

1. For each **submodule** repo, deepest first:
   a. Re-confirm `merge_state='checks_green'` (re-poll — it can go stale during the
      walk).
   b. `on.merge` → merge that MR. Capture the merged SHA. `merge_state='merged'`.
   c. In the **parent** repo's worktree (the next rank up), stage the submodule
      pointer at the merged SHA and commit it. If the parent is a submodule, or the
      root under `bump`, the commit lands on that repo's MR branch and is pushed,
      dirtying its MR so CI re-runs. If the parent is the root under `bump_no_mr`, the
      commit is held in the worktree for step 2.
2. Apply `root_merge_policy` (§3a) for the root:
   - `bump` — poll the root MR CI to green, optionally re-run `on.review.mr.run` on
     the root (config flag, default off — pointer bumps are mechanical), then
     `on.merge` the root MR. `merge_state='merged'`.
   - `bump_no_mr` — push the held pointer-bump commit(s) straight to the root's
     default branch. No CI gate, no MR. `merge_state='merged'`.
   - `skip` — nothing; root row is already `merge_state='skipped'`.

**Failure mid-walk.** Submodule 2 of 3 merges, submodule 3's CI is now red. Already-
merged submodules **stay merged** — git merges don't unwind. The work item drops to
`needs_human` with `work_item_repos` showing exactly which repos merged and which
didn't. The human fixes submodule 3 and re-runs `merge`, which is **idempotent per
row**: it checks `merge_state` and skips anything already `merged`. This is the one
place partial progress survives a halt, and it is deliberate — cross-repo merge is
not transactional and pretending otherwise would be worse. This case is **not** a fix
loop (`merge` is a sequential walk, not a measurement node).

### 3.6 Retry caps

Each `on.ci.poll` / `on.merge` invocation resolves its own `(work_item_id,
hook_type)` cap exactly as before — no new per-repo cap dimension. A submodule and
the root both polling CI are the same `hook_type` (`ci_loop`) under one counter;
acceptable at this scale, but a 3-repo work item burns the `ci_loop` budget roughly
3× faster — a tuning consideration (§9).

---

## 3a. Root-Bump Policy

Sometimes the superproject pointer bump isn't wanted — the superproject is just
pointers to the submodules, and a stale pointer is not the end of the world.
`work_items.root_merge_policy` (enum, default `bump`):

| Value | `merge` node behavior for the root |
|---|---|
| `bump` (default) | Full dance from §3.5 — pointer-bump commits, root CI to green, merge the root MR. |
| `skip` | Merge the submodule MRs only. Root `work_item_repos` row → `merge_state='skipped'`. No pointer-bump commit, no root MR, stale pointer left in place. |
| `bump_no_mr` | Pointer-bump committed straight to the root's default branch — no MR, no CI gate. For repos where the superproject genuinely is only a pointer file and review adds nothing. |

- Set at intake (`POST /work-items`, §6) or adjusted at **chain review** (`01` §3.5)
  — the node that already re-fits the chain to the known spec/plan is the natural
  place to decide "this only touches submodule X, skip the superproject."
- `skip` also skips the root in `open_mr` and `mr_checks` (§3.3, §3.4).
- If the submodule set resolves empty (a pure single-repo work item), the policy is
  meaningless and ignored — the sole repo is the root and always merges.
- **Interaction with late discovery:** a `skip`-policy item that turns out to modify
  the root repo's own non-pointer files trips the same pause/steer path as §3.2 —
  `skip` asserts "root has only stale-safe pointer drift," and real root file changes
  contradict that assertion.

---

## 4. Indexer — Bead Ingestion Removed

The Indexer's "beads boundary" section flagged exactly this: revisit whether bead
ingestion belongs there once cross-repo bead queries can be answered natively. The
hydration hub (§5) is that, and the answer is **remove it**.

### 4.1 Rationale

The sole stated reason to index beads: `bd` is per-repo, cross-repo bead querying
didn't exist, so the Index was "the only thing that can answer 'which bead, across
any of the ~10 repos, mentions X' as one query." The hydration hub is that now —
`bd list`, `bd ready`, `bd dep`, and `bd search --json` all run cross-repo against
the primary DB. Structured queries cover the need; full-text over bead bodies is a
nice-to-have `bd search` already provides against the hub. Semantic search over beads
is dropped — it returns only as an ingestion source if that need materializes.

### 4.2 Schema changes (`04_indexer_search.md` §3)

- `documents.source_kind` enum shrinks `'bead' | 'artifact' | 'session_summary'` →
  **`'artifact' | 'session_summary'`**. Still a closed enum reflecting ingestion
  mechanism, just one fewer.
- No `documents` rows with `source_kind='bead'`. All `documents.id` are now
  uuid-at-first-sighting; the bead-id special case is deleted.
- `document_links.work_item_id` **stays** — still a bead ref, still the M:N link from
  an artifact or session summary to the beads it concerns. Those ids resolve against
  the hub, not against `documents`.
- `metadata_json`'s bead-specific absorption (status / priority / deps /
  `chain_template`) is no longer relevant to `documents`; it still lives on the bead.

### 4.3 Ingestion (`04` §4)

The `bead` `source_kind` subsection is deleted. `bd list --json` per repo is gone
from the Indexer entirely.

### 4.4 Triggers (`04` §2)

The `on.intake` / `on.completed` Work-graph writes no longer trigger a `documents`
re-index — nothing bead-shaped remains to index. Those events instead trigger a hub
`bd repo sync` (§5.3). Artifact and session-summary triggers are unchanged.

### 4.5 Search surface (`04` §9)

`GET /search`'s `source_kind` filter loses `bead` as a value. `/search` returns
artifacts and session summaries only. Bead search moves to `GET /beads/search` (§6).

### 4.6 Build order (`04` §11)

The "Bead ingestion" step is deleted; remaining steps renumber. "Validates the merge
story" now falls to session-summary ingestion, which was already a later step.

---

## 5. The Hydration Hub

### 5.1 What it is

A dedicated beads workspace the orchestrator owns (e.g. `~/.orchestrator/beads-hub/`),
configured with every connected repo:

```yaml
repos:
  primary: "."                 # the hub workspace itself — holds no beads of its own
  additional:
    - ~/repos/project-a
    - ~/repos/project-b
    - ...                      # all ~10 connected repos
```

- The hub holds **no beads of its own**. `primary: "."` is only the aggregation
  point. Every real bead lives in a connected repo's `.beads/`, `source_repo` set —
  the git-native source-of-truth principle (`01` §1.3) is intact.
- Cross-repo **reads** run here: `bd -C <hub> ready`, `bd -C <hub> list`,
  `bd -C <hub> dep tree`, `bd -C <hub> search`, `bd -C <hub> blocked`.
- **Writes** never target the hub. `bd create --repo <path>`, `bd update <id>` (id
  lookup falls back to the owning repo), `bd close <id>` all resolve to the real repo.

### 5.2 Sync

`bd repo sync` reads each additional repo's `.beads/issues.jsonl` export and imports
changed beads tagged with `source_repo`. Consequences:

- Every connected repo needs `export.auto=true` and `export.git-add=true` in its
  beads config. One-time setup per repo, part of "connecting" a repo to the
  orchestrator.
- The hub view **lags** by one export+sync cycle. Acceptable, and bounded by the same
  rule the Indexer applies to derived data: **nothing that gates a decision reads
  stale hub state.** The policy engine, node transitions, and "is this bead still
  open" for a gate all read the owning repo's `bd` directly (`bd -C <repo> show <id>
  --json`), never the hub. The hub is for discovery and cross-repo graph queries, not
  authority.

### 5.3 When the hub syncs

- After every orchestrator-caused bead write (`on.intake`, `on.completed`, submodule
  sub-bead creation) — targeted, immediately after the write.
- Piggybacked on `on.env.prepare` — the orchestrator is already touching that repo's
  worktree, so `bd repo sync` for that repo rides along (mirrors the Indexer's
  identical piggyback).
- A full `bd repo sync` at orchestrator startup — catches hand-edits made while the
  orchestrator was down (mirrors the Indexer's startup scan).
- No standalone poll loop — same posture as the Indexer.

### 5.4 Failure modes

- A repo with a stale or missing JSONL export → its beads are stale or absent in the
  hub. `bd doctor` on the hub catches missing `repos.additional` targets.
- A repo with no `.beads/` at all (a submodule that never adopted beads) → simply not
  in `repos.additional`; its `work_item_repos` rows carry `bead_id=null` and it is
  tracked for MR/merge only.

---

## 6. API and UI Deltas

### 6.1 Orchestrator API (`02_orchestrator_core.md` §10.1)

| Endpoint | Change |
|---|---|
| `POST /work-items` | Body gains `submodules?: [<relative path>]` and `root_merge_policy?: "bump" \| "skip" \| "bump_no_mr"`. Both optional; omitted → resolve-at-env-prep and `bump` respectively. Single-repo intake is unchanged. |
| `GET /work-items` (list) | Each item gains `involved_repos` (compact `work_item_repos` rows) and `root_merge_policy`. Cheap — same "already-materialized, no join cost" logic as `chain_definition`. |
| `GET /work-items/{id}` (detail) | Full `work_item_repos` array: `repo_path`, `role`, `submodule_path`, `merge_rank`, `bead_id`, `mr_ref`, `merge_state` per repo. A single-repo item has exactly one entry (`role='root'`). |
| `GET /beads/search?q=` | **New.** Thin passthrough to `bd search --json -C <hub>`. Replaces the bead half of `/search` the Indexer used to serve. Structured, not indexed — returns bead id, title, repo, status, snippet. |

No new event types are strictly required — `work_item_repos` state changes ride
existing `worker_session_*` and node events plus the row payload. One addition worth
having: `involved_repos_resolved`, emitted by env-prep, carrying the resolved set
into the audit log (parallel to `chain_loaded`).

### 6.2 UI (`05_ui.md`)

| View | Change |
|---|---|
| Board card (§4.1) | The repo tag becomes a repo cluster when `involved_repos` > 1 — root repo plus a "+N" badge. New optional `involved_repo` filter alongside the `chain_template` filter. |
| Work Item Detail (§4.2) | New **repos panel** — one row per `work_item_repos` entry: repo, role, `merge_state` chip (`pending` / `mr_open` / `checks_green` / `merged` / `skipped`), MR link. Rendered above the chain stepper since it is cross-cutting. During the `merge` node this panel is the live view of the ordered walk. Shown only when `work_item_repos` > 1. |
| New Work Item modal (§4.1a) | Two optional fields under an "Advanced / cross-repo" disclosure: **submodules** (multi-select from the target repo's `.gitmodules`, fetched when the root repo is chosen) and **root merge policy** (radio, default Bump). Single-repo intake stays one line. |
| Search overlay (§4.3) | The `source_kind: 'bead'` click-through path is **removed**. A bead-scoped search box backed by `GET /beads/search` sits alongside artifact/summary search; its results link to Work Item Detail via the bead's linked work item, or show the bead inline if unlinked. |
| Document Viewer (§4.4) | The `source_kind: 'bead'` case is **deleted** — no bead documents exist anymore. Bead detail is the hub passthrough, live. |
| Steerable-hook table (§4.2) | Unchanged — the multi-repo `open_mr` / `mr_checks` / `merge` nodes are still `HttpClientAdapter` / `SubprocessAdapter`, still not steerable. |

---

## 7. Non-Goals (v1)

- **No `bd federation`.** No peer sync, no sovereignty tiers, no multi-machine, no
  multi-user. If work ever spans machines or people, that is a separate future
  component — the hub is local-only.
- **No arbitrary multi-repo work items.** The model is a root plus submodules of that
  root. Two unrelated top-level repos in one work item is not supported — that is two
  work items joined by a `relates-to` bead link.
- **No blanket submodule discovery.** Env-prep never inits a submodule the work item
  didn't name or touch.
- **No transactional cross-repo merge.** Partial merge state is a real, surfaced
  outcome, not something rolled back.
- **No semantic search over beads.** Dropped with Indexer bead ingestion; returns
  only as an ingestion source if the need materializes.

---

## 8. Open Questions / Placeholders

- `bd repo sync` cadence for the hub beyond the event-driven, piggyback, and startup
  triggers — whether a slow fallback poll is ever needed for repos edited entirely
  outside the orchestrator. Same "not yet tuned" status as retry-cap defaults.
- `ci_loop` retry-cap tuning when one work item drives CI across three or more repos
  under a single counter (§3.6).
- Whether `bump_no_mr` needs a per-repo allowlist (which superprojects are "just a
  pointer file") or stays a per-work-item call.
- Exact late-discovery diff-scan command set (§3.2) — `git submodule summary` versus
  parsing `git status --porcelain` — settle at build time.

---

## 9. Build Order

1. `work_item_repos` schema + migration; `work_items.repo` semantics and
   `root_merge_policy` column.
2. Hydration hub workspace setup, `bd repo sync` wiring, per-repo `export.auto`
   onboarding.
3. Env-prep multi-repo resolution — selective init, per-submodule worktrees, row
   writes, `merge_rank` computation.
4. Indexer bead-ingestion removal — schema shrink, delete the ingestion path,
   `/search` filter change, `GET /beads/search`.
5. `open_mr` / `mr_checks` repo-set iteration.
6. `merge` node ordered walk — per-row idempotency, partial-halt handling, the three
   `root_merge_policy` branches.
7. Late-discovery diff scan and the pause/steer trip.
8. API shape changes and `GET /beads/search`.
9. UI — repos panel, card cluster, intake-modal fields, search/viewer rework.

State and schema first, then the hub and env-prep that populate the new state, then
the nodes that consume it, interface layer last.

---

## Changelog

Primarily from `cross_repo_federation_design_v1.md` — the last of the five component
designs, and the widest-reaching. One later addendum,
`chain_backward_motion_addendum_v1.md`, touches this component: its §3.5 / §7 add the
explicit note (§3.5 above) that the `merge` node's mid-walk CI-red case is **not** a
fix loop — it stays `needs_human` and the human re-runs `merge`. Otherwise the
content is substantially verbatim; the original's §7 ("Documents This Component
Amends") was a table of `doc §N → amendment` pointers, now redistributed into the
per-document changelogs of `01`–`05` and summarized here:

- **`01` §7, §11** — §7 rewritten (`bd federation` out of scope; hydration hub +
  linked beads; selective `--init`); §11's "beads' federation mechanism" open item
  struck as resolved.
- **`02` §4** — `work_item_repos` table; `work_items.repo` → "root"; `root_merge_policy`
  column; `involved_repos_resolved` event.
- **`02` §10.1** — `POST /work-items` body gains `submodules?` / `root_merge_policy?`;
  `GET /work-items` list + detail gain `involved_repos` / `work_item_repos`; new
  `GET /beads/search`.
- **`01` §3 / chain model** — `open_mr` / `mr_checks` / `merge` documented as
  repo-set-aware (node-internal iteration, not chain branching); `merge` node's
  per-row idempotency and partial-progress-survives-halt behavior spelled out.
- **`03` §5, §5a, §6** — hub config + sub-bead linking (work-graph); multi-repo
  env-prep responsibilities + `involved_repos` output; per-repo CI/MR operation and
  the ordered-merge dance.
- **`04` §2, §3, §4, §8, §9, §11** — bead ingestion removed; `source_kind` enum → 2
  values; build step deleted; "beads boundary" rewritten as "resolved: removed."
- **`05` §4.1, §4.1a, §4.2, §4.3, §4.4** — repos panel, repo cluster, intake-modal
  advanced fields, search/viewer bead-path rework.
