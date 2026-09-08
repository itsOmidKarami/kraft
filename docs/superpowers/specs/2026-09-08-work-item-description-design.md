# Work items carry a description, not just a title

Kraft-e821 · 2026-09-08

## The problem

`store.create_work_item` (`store.py:29`) takes `title`, `repo`, `chain_template`,
`chain_definition`, `submodules`, `root_merge_policy`, `attachments`, `bead_cwd`.
There is no description column in the schema, no API field, no CLI flag, no MCP
argument and no UI input for one.

A title is a label. The description is the brief, and it matters most in exactly
the case the chains exist for: when no spec has been written yet and the spec
node is supposed to write one *from* the description. Today that intent has
nowhere to live, so it is either smuggled into the title or lost.

The loss is concrete and has one address. `executor.py:287`:

```python
instruction = instruction_override or (
    work_item_row["title"] + _attachment_note(_attachments(work_item_row))
)
```

That string is the entire task instruction handed to every agent node. A work
item's brief is whatever fits in its title.

## The approach

Add a nullable `description` column and thread it through every door that
creates a work item, every payload that reads one, and the one line above that
turns a work item into an agent's instruction.

This is not a new mechanism. `submodules`, `root_merge_policy`, `attachments`,
`base_ref` and `bead_cwd` were each added to `work_items` the same way: a
numbered `ALTER TABLE ... ADD COLUMN` in `db.py:MIGRATIONS`, a keyword argument
on `store.create_work_item`, a field on the API model, a flag on the CLI verb.
This design applies that established recipe once more rather than inventing
anything.

**Rejected — put the brief in the bead and read it back at prompt time.** The
bead is the system of record for the *issue*; the work item is the system of
record for the *run*. Reading `bd show` on every agent launch makes an agent
prompt depend on a subprocess that is already best-effort everywhere else it is
used (`adapters/beads.py:ready`, `:search` both swallow failures and return
empty). A prompt that silently loses its brief because `bd` was not on PATH is
the worst possible failure for this feature.

**Rejected — a free-form `metadata` JSON column.** One speculative column to
avoid one concrete one. Every consumer would then need to know a key name that
the schema does not enforce, and the next field would go in the same bag. A
named column is smaller and says what it is.

**Rejected — reuse `pending_steer_context`.** It is consumed and cleared by the
next agent launch. A brief must persist for the life of the item.

## Design

### 1. Schema — `db.py`, `store.py`

`SCHEMA_VERSION` 12 → 13, and:

```python
12: ["ALTER TABLE work_items ADD COLUMN description TEXT"],
```

Nullable, no backfill. Existing rows read `NULL`, which is byte-for-byte
today's behavior at every consumer.

`store.create_work_item` gains `description: str | None = None`, added to the
INSERT column list and values tuple.

It is **not** added to the `work_item_created` event payload. That payload
(`{"title", "repo", "chain_template"}`) is a label for the timeline, and a
multi-paragraph brief in an event row is noise in the one place that has to stay
scannable.

`analytics.py:90` selects its columns explicitly and needs no change. The
document index under `src/kraft/index/` indexes repo **documents**, not work
items — `kraft view search` and the search overlay both hit that index — so
there is no FTS or ingest change in this work.

### 2. The instruction — `executor.py`

`_run_task` builds the instruction at `:287`. It becomes title, blank line,
description when non-empty, then the attachment note:

```python
instruction = instruction_override or (
    _brief(work_item_row) + _attachment_note(_attachments(work_item_row))
)
```

where `_brief` returns the title alone when there is no description, and
`f"{title}\n\n{description}"` when there is.

**Every agent node, not just spec.** The implementation and verify nodes are
working the same task and are equally entitled to the brief; branching per node
would buy a narrower blast radius at the cost of a special case in the one
function that all agent work funnels through.

The comment at `executor.py:65` explains that attachments follow the title
because the title is the task. The description slots between them for the same
reason, and that comment is updated to say so.

### 3. beads — both directions

**Outbound, manual intake.** `adapters/beads.py:intake` hardcodes
`-d "Created by the Kraft orchestrator."`. It takes `description: str | None =
None` and passes it to `-d` when non-empty, keeping the current string as the
fallback. `bd` is invoked with an argument list through `subprocess.run` with no
shell, so arbitrary prose needs no escaping.

**Inbound, auto-intake.** This is a touchpoint the issue did not name.
`adapters/beads.py:ready` projects each ready bead down to
`id`/`title`/`priority`/`issue_type`, **discarding the description the bead
already has**. `intake.py:133` then calls `executor.intake(title=row["title"])`.

`ready` adds `description` to its projection, and `intake.py` passes it through.
An auto-intaken item then starts with the brief its author actually wrote
instead of a bare title — which is the case that needs it most, since no human
is present to notice the loss.

The two directions are complementary and neither is circular: manual intake
creates the bead and writes Kraft's description into it; auto-intake reads an
existing bead's description into Kraft. `executor.intake` only calls
`beads.intake` when `bead_id` is None, which is exactly the manual case.

### 4. API — `api.py`

- `NewWorkItem` gains `description: str = ""`, forwarded through
  `executor.intake`. No validation beyond the model: this is prose, it is never
  resolved as a path, and it never reaches a shell.
- The list payload (`:709`) and detail payload (`:793`) carry `description`.
- **New:** `PATCH /work-items/{id}` with body `{"description": "..."}`, the only
  field it accepts. It writes the column, bumps `updated_at`, and appends a
  `work_item_description_edited` event.

The endpoint is deliberately narrow. There is no per-work-item update route
today, and this one is not the beginning of a general one: it takes a single
named field, and a request carrying anything else is ignored by the Pydantic
model rather than quietly applied.

The event is not decoration. `events` is the authoritative log
(`02_orchestrator_core.md` §4.2) and the description now feeds every agent
prompt — an edit changes what future nodes are told, so "the brief changed at
14:02" has to be answerable from the timeline. Payload is the new description.

The CSRF/origin middleware that answers "cross-site request refused"
(`api.py:444`) covers all non-GET methods, so PATCH is protected by the existing
check with no change.

### 5. CLI — `cli.py`, `client.py`, `render.py`

- `create.add_argument("--description")` at `cli.py:587`, passed by `_cmd_create`
  (`:322`) into `client.create_work_item(..., description=...)`, which forwards
  it in the POST body.
- Multi-line prose comes from the shell (`$'a\nb'`, or a quoted heredoc-style
  argument). No `$EDITOR` flow: it is a second input mechanism, with its own
  temp-file and exit-code handling, for a field most callers will pass in one
  line.
- **The read path is already generic.** `_render_show` (`:276`) renders
  `item.items()`, so `kraft view show` displays the description with no change.
- **But `render.kv` (`:126`) is one line per pair.** It joins
  `f"{label.ljust(w)}  {value}"`, so the second and later lines of a multi-line
  value start at column zero and break the aligned block. `kv` is fixed to
  indent continuation lines to the value column. This is the first field that
  can legitimately contain a newline; the bug is latent today and certain
  tomorrow.

### 6. MCP — `mcp.py`

`create_work_item` (`:50`) gains `description: str | None = None`, forwarded to
`client.create_work_item`.

Its docstring says what the field is *for* — the brief the spec node writes the
design from. An agent handing work off will otherwise keep packing intent into
the title, which is the behavior this whole change exists to end.

### 7. UI — `types.ts`, `api.ts`, `IntakeModal.tsx`, `WorkItemDetail.tsx`

- `WorkItem` (`types.ts:27`) gains `description?: string | null`.
- `api.ts:68` `createWorkItem` body gains `description?: string`; a new
  `updateWorkItem(id, description)` issues the PATCH.
- `IntakeModal.tsx`: a `<textarea className="input">` directly under the Title
  field (`:176`), labeled *Description* with a hint naming it as the brief the
  spec is written from. Optional — submit stays enabled without it.
- `WorkItemDetail.tsx`: the description renders under `detail-title` (`:212`) as
  plain pre-wrapped text. Editing reuses the existing textarea-plus-save shape
  from the needs-context card (`:85-106`) rather than introducing an inline-edit
  pattern the app does not otherwise have.
- The board (`Board.tsx`) stays title-only. A board is a scan surface.

### 8. Skills — `src/kraft/skills/`, `src/kraft/init.py`

Two separate skill sets, both in this repo, both stale the moment this lands.

**The skills agents run** (`src/kraft/skills/`, bundled via `skill.py:BUNDLED`):

- `spec/SKILL.md:3` — the frontmatter description reads "Turn a work item
  **title** into a design a human can approve or reject in one read." It becomes
  the brief. This is the sentence that tells the spec agent what its input is.
- `review-brief/SKILL.md:26` — warns against writing "a restatement of the work
  item title"; updated to the same vocabulary.

**The skills agents use to file work** (`src/kraft/init.py:SKILLS`, inline
strings written out by `kraft admin init`):

- `handoff` `:39` — `create_work_item(title)` becomes the description-carrying
  call, with one line saying the description is what the spec node writes from.
- `handoff` `:52` — "follow the documents rather than guess from the title"
  becomes "…rather than guess".

These are string literals compiled into the package, so they ship with the
change. Installed copies under `~/.claude/skills/kraft/` are refreshed by
re-running `kraft admin init` after the merge; that is a post-merge step for the
operator, not part of this work.

### 9. Docs

- `CLAUDE.md:96`, `AGENTS.md:164`, `README.md:104` — the `kraft item create`
  line gains `[--description "..."]`.
- `docs/consolidated/02_orchestrator_core.md` §4.1 — the `work_items` column
  table gains a `description` row noting it is nullable and that it is prepended
  to every agent task instruction.

## Out of scope

- Description on the board list view.
- Markdown rendering of the description in the UI. It is displayed as
  pre-wrapped plain text.
- Edit history or diffing of descriptions beyond the single event recording that
  an edit happened.
- A general-purpose `PATCH /work-items/{id}` that updates arbitrary fields.
- Editing the title, which remains fixed at intake as it is today.
- Backfilling descriptions onto existing work items.
- Re-running `kraft admin init` on this machine; that is a post-merge operator
  step.

## Verification

Named tests that must exist:

**Backend**

- `test_db.py` — a v12 database opens and migrates: `description` exists on
  `work_items` and pre-existing rows read `NULL`.
- `test_store.py` — `create_work_item` round-trips a description; omitting it
  stores `NULL`; `work_item_created` payload does **not** contain it.
- `test_executor.py` — **the test that pins the feature**: run an agent node on
  an item with a description and assert via `KRAFT_FAKE_AGENT_PROMPT_LOG` (the
  seam `tests/support/fake_agent.py:90` already provides) that the recorded `-p`
  instruction contains both title and description, description after title. A
  second case asserts an item with no description produces today's exact string.
- `test_adapters_beads.py` — `intake` passes a non-empty description as `-d`;
  falls back to the orchestrator string when it is empty or None; `ready`
  includes `description` in its projected rows and tolerates a bead JSON object
  that has no description key.
- `test_intake_poller.py` — an auto-intaken bead's description reaches
  `store.create_work_item`.
- `test_api.py` — `POST /work-items` with a description persists it and returns
  it on both the list and detail payloads; `PATCH /work-items/{id}` updates it,
  bumps `updated_at`, appends `work_item_description_edited`, and 404s on an
  unknown id.
- `test_cli.py` — `kraft item create --description` reaches the POST body.
- `test_render.py` — `kv` with a value containing `\n` indents the continuation
  line to the value column.
- `test_mcp.py` — the `create_work_item` tool forwards `description`.

**Frontend**

- `IntakeModal.test.tsx` — the description textarea submits its value in the
  POST body, and submission still succeeds when it is left empty.
- `WorkItemDetail.test.tsx` — a description renders when present, is absent from
  the DOM when null, and saving an edit issues the PATCH.

`just test` and `just test-ui` must pass; per the repo's testing note, run the
named files rather than the full suite while iterating.
