# Planning adapter: on.spec.requested and on.plan.requested

Date: 2026-09-06
Beads: Kraft-pqu

## Problem

`templates/registry.yaml` binds nine hooks to `builtin:noop`. Two of them —
`on.spec.requested` and `on.plan.requested` — are the first two nodes of
`templates/default.yaml`, each with a gate after it. A `default` chain therefore
opens by asking a human to approve a spec that was never written and a plan that
was never written.

The practical consequence is that `default` is not usable, so every real work
item runs `quick-task`, whose three nodes (`env_setup`, `implementation`,
`verify`) have no gates at all. Spec-driven work is the thing Kraft's design
claims and the thing Kiro competes on; today Kraft has the chain for it and no
plugin behind it.

`03_plugin_adapters.md` §3 calls Planning a thin variant of the execution
worker's adapter — "only the config differs". That is nearly right and the
"nearly" is the whole sub-project. Two things are missing:

1. **No per-hook instruction.** `executor.py:282` derives an agent task's
   instruction from the work item title plus `_attachment_note`; the only other
   source is `instruction_override`, which exists for the fix loop. A binding
   cannot say what its hook's session is supposed to *do*. Fine while exactly
   one agent hook is bound; blocking the moment a second one is.
2. **No worktree.** `default.yaml` runs `spec`, `plan` and `chain_review`
   *before* `env_setup`, and `builtins.env_setup` (`builtins.py:52`) is what
   creates the worktree. `executor.py:744` computes
   `run_dirs.worktrees / work_item_id` unconditionally and `_dispatch` passes it
   as `cwd` (`executor.py:306`), so the first spec session would fail in `Popen`
   with `FileNotFoundError` before the agent ran at all. §1 fixes this; it is a
   pre-existing latent bug that only a bound spec hook can reach.

`skills/chain-review/SKILL.md` — 150 lines, already written for Kraft-hm0 — is
unwired for reason (1). Whatever mechanism Planning gets, Chain Review consumes
unchanged.

## Non-goals

- **The other seven noop hooks.** `on.chain.review_ready` (Kraft-hm0),
  `on.review.local.run`, `on.review.mr.run` (Kraft-pl7), `on.mr.open`,
  `on.ci.poll`, `on.human_review.requested`, `on.merge` (Kraft-33j) are
  separate efforts. This one is expected to unblock the first of them and to
  change nothing about the rest.
- **Editing skills from Settings.** Skills are code, not config; §2.3 argues the
  point. The overlay directory is the escape hatch and it is a filesystem one.
- **Per-repo method selection.** `repos.yaml` already layers `deny_tools`,
  `steering` and `default_model`, so adding a per-repo skill override later
  follows a path that exists. Nothing today asks for two repos with different
  spec processes.
- **Making `default` the intake default.** Which template a work item gets is a
  separate decision from whether `default` works.
- **Interactive brainstorming.** §3 explains why the dialogue shape cannot
  survive a headless session and what replaces it.

---

## 1. The worktree exists before the first node

`builtins.env_setup` does three things: pin `base_ref` to the repo's HEAD,
`git worktree add` on branch `kraft/<work_item_id>`, and copy intake
attachments. It is already idempotent — `if worktree.is_dir(): return "done"` —
and the early return is deliberately placed *after* the base-ref pin so a
crashed-and-retried run never re-pins to a moved HEAD.

The first two of those three are a precondition of every node, not the work of
one. They move into `builtins.ensure_worktree(db, run_dirs, repo, work_item_id)`,
which `executor.run` calls once before walking the chain. `env_setup` calls the
same function and then copies attachments, so its node keeps its identity, its
session row and its existing guard.

**In code, not by reordering `default.yaml`.** A template change would be one
line, and free today because `env_setup` does not yet run the repo's setup
convention that `03_plugin_adapters.md` §5a describes. But `templates/` is
seeded once and never overwritten, so every `$KRAFT_HOME` that already exists
would keep the broken order forever, silently. A code fix reaches those homes.
It also keeps `env_setup` positioned where §5a wants it — a `make setup` running
before anyone approved the spec is work done for an item that may be rejected at
the first gate.

`ensure_worktree` inherits the idempotency: called twice, the second call
returns without touching git. `run` calls it before `start_index` is applied, so
a gate rejection re-entering mid-chain (§6) and a reattach-driven resume both get
the same guarantee.

`base_ref` is therefore pinned at intake-time HEAD rather than post-plan HEAD.
That widens the eventual review diff by whatever landed on the default branch
during planning, and it is the correct direction: `2026-09-04-sub-project-a-gate-diff-review-design.md`
§1 requires the base to be pinned rather than recomputed precisely so the diff
does not move under an unchanged work item.

---

## 2. Two injections, one overridable

The guidance a spec session needs splits cleanly in two, and conflating them is
what makes the pluggability question hard:

- The **contract** — write one document at a known path, commit it, ask for
  missing information through `needs_context`. Kraft owns this; a plugin author
  cannot be trusted to honour it and Kraft cannot verify that they did.
- The **method** — how to decide what belongs in a good spec. Kraft has no
  monopoly on this. superpowers has an opinion, so does grillme, so does any
  team with a house process.

So the contract is injected as code, unconditionally, and the method is injected
from a source the binding names.

### 2.1 The contract block

`adapters/agent.py` already assembles a system prompt from `_CTX`, which carries
the session-summary path convention and the four-value status vocabulary. A hook
that produces a document gets a second block, controlled by a new binding key:

```yaml
on.spec.requested:
  { kind: agent, command: claude, skill: spec, artifact: spec }
on.plan.requested:
  { kind: agent, command: claude, skill: plan, artifact: plan }
```

`artifact: <kind>` makes `run_agent_task` append a block that:

- names the exact path — `.engineering/<kind>s/<work_item_id>.md`, creating the
  directory if absent;
- requires the front matter in §2.2;
- requires the session to `git add` and commit the file on the work item's
  branch before it exits.

The commit is not optional. It is what puts the document in the
`human_review_approval` diff later, what survives a worktree that has to be
recreated, and what makes §5's post-merge indexing true rather than aspirational.

**Kraft derives the path; the session does not report it.** An earlier draft had
the result file carry an `artifact_ref` the way it carries `session_summary_ref`.
That was wrong: Kraft *mandates* the path, so the path is already a function of
the hook's `artifact:` kind and the work item id. Deriving it deletes a result
field, a `worker_sessions` column and its migration, store-time validation, a
drop-event for a rejected ref, the question of which of several session rows owns
the ref after a rejection re-run, and — most importantly — a real bug:
`reattach.py:52` is a second, parallel result-file reader that enumerates the
fields it carries, so a Kraft crash during a spec session would have recorded the
exit without the ref and brought the gate up empty.

What remains is a question the filesystem answers: does the derived path exist in
the worktree.

`artifact:` and `skill:` stay independent keys, not one key inferred from the
other. Chain Review wants a skill and produces no document — it returns
`revised_chain_nodes` — so a single key would grow an exception on its first
reuse.

### 2.2 Front matter

The contract block requires the same front-matter block session summaries
already use:

```yaml
---
work_item_ids: [<work_item_id>]
node_id: <node_id>
hook_point: <hook_point>
kind: specs            # or plans
title: <a human title>
---
```

`ingest.py` reads all of these already — `derive_kind`, `derive_title` and
`links_from_front_matter` are written and tested. One line stops it being used:
`ingest.py:154` attaches link rows only when `source_kind == "session_summary"`,
so an artifact carrying `work_item_ids` is indexed and never linked, and
`documents_for_work_item` (`index/service.py:295`) joins on exactly that table.

That condition becomes "attach whatever front matter declares", for every
document. A `.engineering/` file with no `work_item_ids` produces no rows, which
is the behaviour it has today. With it, a merged spec appears under
`kraft docs <ID>` and in the detail screen's documents panel with no further
machinery — which is the only reason the front matter is worth mandating.

### 2.3 The method: how `skill:` resolves

Two modes, discriminated by a colon:

| Value | Resolves as |
|---|---|
| `spec` | **Local directory.** `$KRAFT_HOME/skills/spec/SKILL.md` if present, else Kraft's bundled `src/kraft/skills/spec/SKILL.md`. The file's text is injected. |
| `superpowers:brainstorming` | **Plugin reference.** Injected as an instruction for the agent CLI to resolve the named skill itself. Kraft never reads a file. |

The colon is required, not sniffed. A bare name that resolves to no directory on
either layer is a `RegistryError` at config load — the same posture
`_steering.validate` already takes, and the reason a typo in a local name can
never silently degrade into a plugin reference.

A typo in the *plugin* half — `superpwers:brainstorming` — is not detectable by
Kraft, which cannot see the user's plugin installation. It is closed from the
other end: **the skill injection block**, not the artifact block, instructs the
session to return `status: needs_context` naming the skill if the skill is not
available. Attaching it to the skill and not the artifact is what makes it cover
Chain Review, which has a `skill:` and no `artifact:` and is the first hook
outside this sub-project to use either. A wrong plugin name costs one stop and
one readable question; it does not cost a plausible-looking bad document.

Plugin mode has no consumer on day one — §3 argues the skill that motivates it
cannot run headless as written. It ships anyway, deliberately, so that the
overlay is not the only way to point Kraft at someone else's method.

### 2.4 Why the overlay is not seeded

`$KRAFT_HOME/skills/` is an overlay: absent by default, never written by
`seed_home`, checked first when present.

`templates/` is seeded once and **never overwritten** — the guarantee from
`2026-09-04-packaging-and-dev-execution-design.md` that an upgrade cannot
clobber an edited config. That guarantee is right for config and wrong for
skills: a seeded skill would freeze at whatever version the user first ran, and
every later improvement to Kraft's spec methodology would reach new users only.
Not seeding it means the bundled skill improves with the package and an operator
who wants to pin their own drops a file to do it.

Kraft's own skills ship as package data **inside the package**, at
`src/kraft/skills/`, rather than beside `templates/` at the repo root. Inside the
package they resolve identically from a checkout and from site-packages with no
`just install` copy step and no checkout-versus-installed fallback chain —
`_bundled/` needs that chain because it holds build output, which skills are not.

This does require a packaging change, which the "no copy step" argument above
should not be read as denying: `[tool.setuptools.package-data]` is
`kraft = ["_bundled/**/*"]` today and gains `"skills/**/*"`, or the wheel ships
no skills and every local binding fails at config load on an installed Kraft.

This moves `skills/chain-review/SKILL.md` to `src/kraft/skills/chain-review/`.
Nothing loads it today, so the move costs a path in one design document.

### 2.5 Registry validation and plumbing

`load_registry` (`templates.py:63`) gains `skill` and `artifact` to `agent_only`
and to the `known` allowlist, so a `skil:` typo fails loudly the way an unknown
`profile` already does. `artifact:` must be a non-empty string. `skill:` is
additionally resolved at load time, like `steering`, so a missing skill directory
is a config error rather than a first-dispatch surprise.

Resolution needs the overlay path, and `load_registry` cannot derive it the way
it derives `steering_dir` from `path.parent`: the overlay lives at
`kraft_home()/skills`, a *sibling* of `templates/`, and `KRAFT_TEMPLATES_DIR` can
point somewhere else entirely. So `load_registry` takes a `skills_dir` keyword
alongside `steering_dir`, defaulting to `kraft_home()/skills`. Both call sites
pass it: `api.py:87`'s startup load, and `put_registry` (`api.py:1552`), which
validates an edited registry against a temp copy and would otherwise accept a
`skill:` the running server cannot resolve.

### 2.6 Injection order

`run_agent_task` assembles: `_CTX`, then the artifact block, then the review
package note, then the method text, then steering. Widest and most binding
context first, matching the precedence `resolve_invocation` already documents.
Steering stays last because it is the operator's, and last is the position that
reads as an amendment.

---

## 3. The two shipped skills

`spec` and `plan`, authored for a headless session. Neither restates the
contract — that is §2.1's code block, so an override cannot drop it.

The bead proposes binding straight to superpowers *brainstorming*. That skill
cannot work here as written: its own HARD-GATE requires presenting a design and
waiting for a human to approve it before proceeding, and its process is "ask
questions one at a time". A `claude -p` session has nobody to ask and nobody to
wait for. Kraft's substitute already exists and is fully wired —
`status: needs_context` stops the item with the agent's question
(`executor.py:413`), the human answers with `/steer`, and `POST /resume`
(`api.py:928`) relaunches. What each skill says about it:

> Batch every question you genuinely cannot answer from the repository into a
> single `needs_context`. Each one costs a full relaunch, so stopping once per
> fact is the expensive way to ask three things.

Which is the instruction `_CTX` already gives; the skills reinforce it because a
spec session is the one place where the temptation to ask is highest.

On a re-run after a gate rejection, the skill revises the existing document at
the same path rather than writing a new one. The rejection note arrives as steer
text (§6), so the session can see what was wrong with the previous draft.

The `plan` skill additionally reads the approved spec — it is at the sibling
`.engineering/specs/<work_item_id>.md`, committed, and by the time `plan` runs
has passed `spec_approval`.

**No new status.** `03_plugin_adapters.md` §3 drafts `ready_for_approval`. Kraft
already expresses that as `done` on a node carrying `gate_after`. A second
encoding of a state the chain model owns is a state machine with two sources of
truth.

**No fix loop.** Neither node declares one, so a session that returns `failed`
goes to `mark_needs_human` (`executor.py:539`) and stops the item. That is
correct: a spec that could not be written is a human problem, not something to
retry against the same inputs. Rejection, which *does* have new inputs, is §6.

---

## 4. Reading the artifact

`Gate.tsx` has an `artifact` slot (`Gate.tsx:42`, rendered at 161);
`WorkItemDetail.tsx:296` fills it only for `human_review_approval`.
`spec_approval` and `plan_approval` currently render a title, a prompt line, and
two buttons — approve or reject a document the human cannot see.

**`gate_artifact` on the work item detail payload.** Null, or the repo-relative
path derived from the pending gate's node: find the node whose `gate_after` is
the pending gate (`_gate_node_index`, `api.py:505` — already written), take the
task on it whose binding carries `artifact:`, derive
`.engineering/<kind>s/<wid>.md`, and report it if the file exists in the
worktree. Computed beside `pending_gate` (`api.py:658`).

**`GET /work-items/{wid}/artifact`** returns `{path, title, content}` read from
the worktree. Since the path is derived and not stored, the only untrusted input
is the *contents* of the worktree, which the previous agent session wrote.

**Containment.** `adapters/agent.py`'s own comment is explicit that `deny_tools`
"does not stop the agent running a shell that ignores it", so a session can leave
`.engineering/specs/<wid>.md` as a symlink to `~/.ssh/id_ed25519`. The path being
Kraft-derived does not help — the *file at* that path is the attack. Therefore:

- `Path.resolve(strict=True)` the candidate **and** the worktree root, and
  compare with `is_relative_to`. Resolving both sides is required, not belt and
  braces: on macOS `run_dirs` under `/var` resolves to `/private/var`, and a
  prefix comparison against the unresolved root fails open or closed depending
  on which side was normalized.
- Open the **resolved** path, not the original. The TOCTOU window is small — no
  session for this item runs while its gate is pending — but reading a path you
  did not check is the bug regardless of how narrow the window is.
- Cap the response at `DIFF_MAX_BYTES` (`api.py:55`, 1 MB), truncating with a
  flag the way the diff endpoint does. A multi-gigabyte file is a plausible agent
  mistake and an easy denial of service; an uncapped `read_text` into a JSON body
  is the same hole the diff endpoint already declined to leave open.

A symlink escape, a missing file, or a read error is a 404 with the reason in an
event — not a partial render.

**The indexer is deliberately not involved at gate time.** `scan_repo` lists
files with `git ls-files` against the repo (`ingest.py:129`), so a document
committed only on the work item's branch is invisible to it. Reading the worktree
directly is what makes the gate work at the moment it is actually pending, and it
works from off the machine, which
`2026-09-04-sub-project-a-gate-diff-review-design.md` establishes is the
requirement that killed `open-worktree` as a review surface. §2.2's front matter
is what makes the same document appear in the ordinary documents surface *after*
the branch merges.

**Client.** A new `ArtifactModal` — modal shell, `useModal`, `<Markdown>`.
`DocumentModal` is not reused because every one of its controls beyond the
markdown body — the editor-launch menu, copy-path, breadcrumbs — is keyed on a
document id that an unindexed artifact does not have. Threading an id-optional
mode through it leaves a component with two modes, one of which cannot support
half its chrome. The duplication is the modal shell, which is small.

`WorkItemDetail` passes a "Review spec"/"Review plan" button into the `artifact`
slot when `gate_artifact` is set, mirroring the "Review changes" button already
there.

**CLI and MCP.** `kraft approve` / `kraft reject` and the matching MCP tools can
already resolve `spec_approval` (`cli.py`, `mcp.py`), so without a read verb the
non-browser surfaces can approve a spec they cannot read — and `kraft doc` needs
an index id the artifact does not have until merge. CLAUDE.md's rule is that
every MCP tool is also a subcommand, so:

- `kraft show` reports `gate_artifact` in its gate line, and `--json` carries it.
- `kraft artifact [ID]` prints the document; `--json` returns the payload.
- The same read is exposed as an MCP tool, so an agent at a gate can read what it
  is approving.

---

## 5. Rejection

Already works, and needs no change. `reject_gate` (`api.py:806`) bounds the
rejection with the same counter machinery as a fix loop, then re-runs the chain
from the gate's node with `steer=body.note`. `_dispatch` prepends the note via
`_STEER_PROMPT`. §1's `ensure_worktree` runs on that path too, so a re-entry
after a worktree was removed by hand still has one.

The one thing this sub-project adds is that the re-run now has something to do:
the note reaches a session with a spec skill and a committed document to revise.

---

## 6. Registry bindings and existing homes

`templates/registry.yaml` binds the two hooks per §2.1.

An **existing** `$KRAFT_HOME` does not get them. `templates/` is seeded once and
never overwritten, so a user who ran Kraft before this ships keeps
`builtin:noop` and keeps the broken `default` chain, with no error and no
notice. This is the cost of the never-overwrite guarantee and it is the right
trade, but silence is not.

`kraft doctor` gains a check: for each hook the **bundled** registry binds to
something other than `builtin:noop`, name any hook the **live** registry still
binds to `builtin:noop`.

It is reported as `ok=True` with the detail carrying the hook names. `doctor.py`
has no warning state — `_check` is `{ok, skipped}` and `cli.py:132` exits 1 on
any `not ok` — and inventing one for this would make `kraft doctor && deploy`
fail for an operator who deliberately disabled a hook, which is a supported
configuration.

The check skips when `BUNDLED / "templates" / "registry.yaml"` is absent, which
is every source checkout (`paths.py`: "Absent in a plain source checkout"). A
skipped check is already `ok` by `_check`'s own contract.

```
hooks   ok    on.spec.requested, on.plan.requested are builtin:noop in
              ~/.kraft/templates/registry.yaml but bound in this version's
              defaults — Settings → Hooks, or edit the file
```

`default.yaml` needs no change: its node order was latently broken (§1) and the
fix is in code, so an old copy of the template behaves the same as a new one.

---

## 7. Testing

**Worktree readiness** (`tests/test_executor.py`) — a chain whose first node is
not `env_setup` dispatches successfully; `ensure_worktree` called twice does not
re-pin `base_ref`; `env_setup` running second still copies attachments; a
rejection re-entry with the worktree deleted recreates it.

**Registry** (`tests/test_templates.py`) — `skill:`/`artifact:` accepted on an
agent hook and rejected on subprocess and builtin hooks, sharing the existing
`agent_only` case. A bare `skill:` naming no directory raises `RegistryError`. A
colon-bearing value is accepted with no filesystem lookup. `skil:` is rejected by
the existing unknown-key path. `put_registry` rejects a `skill:` the server
cannot resolve.

**Resolution** — overlay beats bundled; bundled is used when the overlay is
absent; the overlay directory not existing at all is not an error.

**Injection** (`tests/test_agent_adapter.py`) — a dispatch with `skill:` and
`artifact:` puts the skill text and the derived artifact path in the system
prompt in §2.6's order; a plugin-reference value puts the reference and no file
content; the skill-unavailable instruction is present for a `skill:`-only
binding with no `artifact:`.

**Indexing** (`tests/test_indexer.py`) — an artifact carrying `work_item_ids`
links to the work item; one without it produces no link rows, as today; a session
summary is unchanged.

**Gate** (`tests/test_api.py`) — `gate_artifact` is null when the file is absent
and set once it exists; `GET /work-items/{wid}/artifact` returns the document,
404s when absent, 404s on a symlink pointing outside the worktree, and truncates
past `DIFF_MAX_BYTES`. The symlink case is the one that must exist: it is the
only test that fails if an implementer reaches for `startswith`.

**Doctor** — a live registry with `builtin:noop` where the bundled one binds
something names the hooks and does not fail the run; a fully-bound live registry
reports clean; an absent bundled registry skips.

**End to end** (`tests/test_executor.py`, fake agent) — a `default` chain runs
`spec`, stops at `spec_approval` with `gate_artifact` set; a rejection re-runs the
node with the note; an approval advances to `plan`.
`fixtures/fake-claude.sh` branches on the `Hook point:` line already present in
`_CTX` to write and commit an artifact, so this needs no real agent.

**Frontend** — `ArtifactModal` renders markdown and closes; `WorkItemDetail`
shows the review button on `spec_approval` only when `gate_artifact` is set.

`just test`, `just test-ui`, `just lint` pass.

---

## 8. Acceptance

- A `default` work item produces a spec, stops at `spec_approval`, and the human
  can read the spec — in the UI, from `kraft artifact`, and through MCP —
  without shell access to the machine.
- Rejecting `spec_approval` with a note re-runs the node and revises the same
  document.
- Approving advances to `plan`, which reads the approved spec.
- A binding naming a nonexistent local skill fails at config load; one naming an
  unavailable plugin skill stops the item with a readable question.
- A spec that is a symlink out of the worktree is not served.
- Once the branch merges, the spec appears under `kraft docs <ID>`.
- `kraft doctor` names the unbound hooks in a pre-existing `$KRAFT_HOME`.
- Kraft-hm0 needs no code from this sub-project — only a `skill: chain-review`
  binding.

---

## 9. Open questions

- **Whether `default` should become the intake default** once it works. Out of
  scope here; worth a bead once the chain has run a few real items.
- **Whether `on.spec.requested` should be skipped for a small work item.**
  Intake attachments already trim satisfied gates (`ATTACHMENT_GATES`,
  `templates.py:31`), and `quick-task` exists for work that needs neither. A
  third path — the chain sizing itself — is a chain-review question.
- **Multi-repo.** `06_cross_repo_federation.md` gives each involved repo a
  worktree, but one work item has one spec. The derived path resolves against the
  root repo's worktree, which is right for a single spec and unexamined for
  anything else. No multi-repo item can reach this today.
- **Artifact naming.** `<work_item_id>.md` is unambiguous and unreadable. A
  slugged title reads better and collides. Deferred: every surface shows the
  front matter's `title`, so the filename is rarely seen.
