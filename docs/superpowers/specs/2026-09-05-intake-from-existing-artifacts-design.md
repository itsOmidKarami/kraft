# Intake From Existing Artifacts — Start a Work Item at Implementation

**Status:** approved
**Date:** 2026-09-05
**Bead:** Kraft-dgh
**Component:** intake (`api.py`, `executor.py`, `templates.py`, `builtins.py`) + UI (`IntakeModal.tsx`)

---

## 1. What this is

Today every work item starts at node zero. If the spec and the plan already
exist — written by hand, or by an agent in another session — Kraft has no way to
say so. `intake()` takes `title`, `repo`, `template` and nothing else, and the
agent's whole instruction is the item's title (`executor.py:107`). The two
workarounds are both bad: hand-write a template with the spec and plan nodes
deleted, and smuggle the plan's path into the title.

This effort makes "I already have the spec and the plan" a first-class intake
choice. You attach the documents; the chain drops the phases they satisfy; the
agent is told to follow them.

Scope is intake and the first agent launch. Nothing about how spec or plan
documents get *produced* changes.

## 2. Decisions

**Attachments trim the chain; there is no start-node selector.** Attaching a
plan is the same statement as "skip the plan phase", so it is one action, not
two that have to agree. A general "start at node N" control can select a start
that contradicts what was attached; this cannot.

**Trimming keys on the gate name, not the node id.** A node is dropped when its
`gate_after` is the gate the attachment satisfies: `spec` → `spec_approval`,
`plan` → `plan_approval`. Gate names are a validated closed vocabulary
(`GATE_NAMES`, `templates.py:17`); node ids are free text a custom template
picks. Keying on `n["id"] == "spec"` would silently fail to trim any template
that names the node `specification`.

**A template with no such gate is not an error.** `quick-task` has neither gate.
Attaching a plan to it trims nothing and the attachment still rides along as
agent context. The alternative — rejecting the combination — makes the useful
case (a short chain that should still follow the plan) impossible.

**The attachment is context for every agent node, not just implementation.** The
verify and review agents benefit from knowing what was agreed as much as the
implementer does. A fix-loop `instruction_override` still wins: it is a
narrower, more urgent instruction (`executor.py:281`).

**Attachments live on `work_items`, not in a side table.** Same reasoning as
`submodules` (`store.py:41`): chosen once at intake, never queried across items,
belongs to this item as much as its chain does.

**Uncommitted documents are copied into the worktree, not rejected.** The
worktree is `git worktree add -b` from HEAD (`builtins.py:22`), so a plan you
wrote five minutes ago and have not committed does not exist for the agent.
Rejecting it puts a wall exactly where the flow is fastest. Copying it in as an
untracked file, for the agent to commit alongside its work, costs six lines.

**Validation is a trust boundary.** `path` comes from a browser and is used to
read a file and to write into the worktree. It is resolved against the repo root
and rejected if it escapes it, before anything touches the filesystem.

## 3. Intake contract

`api.py`:

```python
class Attachment(BaseModel):
    kind: Literal["spec", "plan"]
    path: str                      # repo-relative

class NewWorkItem(BaseModel):
    ...
    attachments: list[Attachment] = []
```

Rejected with `422` when:

- two attachments share a `kind`;
- `(repo / path).resolve()` is not under `repo.resolve()` — traversal, absolute
  path, or symlink escape;
- the resolved path is not an existing file.

Existence is checked against the working tree, not `HEAD`: an uncommitted
document is legal input (§6).

## 4. Chain trimming

`templates.materialize` grows one keyword argument:

```python
def materialize(template: Template, *, satisfied_gates: frozenset[str] = frozenset()) -> dict:
```

Nodes whose `gate_after` is in `satisfied_gates` are omitted from the
materialized node list. The gate set is derived at intake:

| attachment kind | gate satisfied  |
|-----------------|-----------------|
| `spec`          | `spec_approval` |
| `plan`          | `plan_approval` |

The trimmed chain is what `create_work_item` stores in `chain_definition`, which
is also what the detail view renders. The progress display, the gate list and
the timeline therefore show the short chain with no changes of their own — the
item genuinely has no spec node, rather than having one that is skipped at
runtime.

## 5. Storage and the agent instruction

Migration 7 (`db.py`):

```sql
ALTER TABLE work_items ADD COLUMN attachments TEXT
```

JSON, `NULL` when nothing was attached — the shape `submodules` already uses.
`executor.intake` takes `attachments` and passes it through to
`store.create_work_item`.

`executor._dispatch` builds the instruction for `kind == "agent"` where
`instruction_override or work_item_row["title"]` is decided today:

```
<title>

Spec: .engineering/specs/2026-09-05-foo.md
Plan: .engineering/plans/2026-09-05-foo.md
Follow the documents above; they are the agreed spec and plan for this work
item. Do not re-plan.
```

Only the attached kinds appear; the wording is one fixed block either way, so a
plan-only item is not described with spec-only phrasing. With no attachments the instruction is the
title, unchanged. `instruction_override` (the fix prompt) bypasses this block
entirely; the steer note still leads, as it does now.

## 6. Worktree copy

`builtins.env_setup`, after the `git worktree add` subprocess succeeds: for each
attachment, if the path does not exist inside the worktree, copy it from the
source repo's working tree to the same relative path, creating parent
directories.

A committed document arrives through git and the copy is skipped. An uncommitted
one lands untracked, and the agent commits it with the rest of its work.

The idempotent early return for an existing worktree (`builtins.py:12`) keeps its
current behaviour: a crashed run that already has a worktree also already has the
copies.

## 7. Visibility

Three surfaces, all of them existing machinery:

**Documents tab.** Attachments are joined in at *read* time, not stored as
`document_links` rows: `Indexer.documents_for_work_item` reads the item's
`attachments` from the state DB (it already holds `self._state`) and looks the
paths up in `documents` by `(repo, path)`. Each such row carries
`attachment_kind` so the panel can tag it "attached at intake".

Writing link rows was the obvious approach and is wrong: `upsert_document` calls
`replace_links`, which deletes *every* link for a document and re-inserts only
what its front matter declares (`ingest.py:281`, artifacts declare none). An
attachment row would survive exactly until the next repo rescan. Reading through
has no such failure, needs no index-schema change, and picks an attachment up
automatically once an uncommitted document is committed and scanned.

**Timeline.** A `work_item_attachments` event at intake carrying the kinds and
paths, so the timeline explains why the chain has no spec node.

**Badge.** `attachments` is returned on the work-item API row. The card and the
detail header show `from spec+plan` (or `from plan`), so the shortened chain is
legible from the list without opening the item.

## 8. UI

`IntakeModal.tsx` gains a "Start from existing" field between Title and Chain
template:

- A typeahead per kind, querying `GET /search` with the chosen repo,
  `source_kind=artifact` and `kind=specs` / `kind=plans` — the values ingestion
  actually writes (`ingest.source_kind_for`, `ingest.derive_kind`). `/search`
  requires a non-empty `q` (`api.py:768`), so it is a type-to-search field, not
  a browse list: nothing is queried until a repo is entered and a character is
  typed, the same dependency the submodule probe already has.
- A free path input beside it for a document that is not indexed — the
  uncommitted case, which by §7 cannot appear in the typeahead.
- A one-line chain preview under the template control, with the nodes an
  attachment removes struck through, so the skip is visible before Create is
  pressed.

The field is not inside "Advanced · cross-repo"; it is a primary intake choice,
not a cross-repo detail.

## 9. Testing

Backend:

- `materialize` drops exactly the nodes carrying a satisfied gate, and is a
  no-op for a template that has none.
- Intake rejects a duplicate kind, a traversing path, an absolute path, and a
  path to a non-existent file.
- The instruction for an agent node names both documents; with no attachments it
  is the bare title; a fix-loop override is unaffected.
- `env_setup` copies an uncommitted attachment into the worktree and leaves a
  committed one alone.
- A work item created with attachments round-trips them on `GET /work-items/{id}`.

Frontend:

- The typeahead queries with the entered repo and filters to the chosen kind.
- The chain preview strikes through the nodes an attachment removes.
- The badge renders for an item with attachments and not for one without.
