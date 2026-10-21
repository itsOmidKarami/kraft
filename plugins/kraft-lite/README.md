# Kraft Lite

Runs a chain of work inside one agent session: ordered nodes, human gates at the
points where a decision belongs to you, and fix loops that are capped instead of
endless. The chains are Kraft's own, the same files Kraft reads. No service, no
port, no database process.

    /kraft-lite:init            # once per repo, optional — writes .kraft-lite/bindings.yaml
    /kraft-lite:start "<work>"  # materialize the chain and run the first node
    /kraft-lite:next            # run the next node; also the resume point
    /kraft-lite:gate            # approve or reject
    /kraft-lite:status          # where the chain got to
    /kraft-lite:run-slices      # controller: build a large plan as slices, one session each
    /kraft-lite:build-slice     # child: build one slice for its controller

## Install

Kraft Lite ships in the Kraft marketplace, beside the `kraft` plugin:

    claude plugin marketplace add itsOmidKarami/kraft
    claude plugin install kraft-lite@kraft

It used to publish from `itsOmidKarami/kraft-lite`, which is frozen at 0.5.2 and
receives no more releases. Installing it from there still works; it just stops
moving.

The plugin name namespaces the commands as `/kraft-lite:*`. Update with
`claude plugin update kraft-lite`. It needs Python 3.10 or newer and nothing
else — no pip install, no dependencies. CI tests both ends of that range.

Add `--scope project` to either command to keep it to one repo. The plugin
version follows Kraft's release tag. To check the install, run
`/kraft-lite:status` in a repo: it reports that no chain exists yet.

## What is fixed and what is not

A chain freezes when it starts, resolved: its nodes cannot be reordered, added
to or removed, and editing the chain file later does not retarget a run in
flight. A verb that meets a node missing from the frozen chain fails rather
than guessing. The bindings are the opposite - `.kraft-lite/bindings.yaml` is
read again on every dispatch, so rebinding a task is how a chain already in
flight gets corrected.

The shipped chain is Kraft's own, bundled as the `default` chain: nineteen
nodes from spec and plan through implementation and verification to opening a
merge request, checking it, review by a person, merge and a check of the merged
branch's CI, five of them gates where a human decides. Two nodes,
`verification` and `merge_request_feedback`, carry a fix loop; a node past its
cap stops the chain and escalates to you. Lite supplies the order, the gates,
and the caps, and calls whatever you already use for each task: it does not
supply a spec writer or a planner.

## Where chains come from

Lite reads the files Kraft reads, a `library.yaml` and its `chains/*.yaml`,
and expands `extends` the way Kraft does: library components first, then a
chain that extends another chain. A chain comes from one of four places:

1. **`--chain PATH`.** A chain file, told by its `.yaml` or `.yml` ending or a
   `/` in the name. Its `extends` resolve against your library, else the
   bundled one.
2. **The repo library.** The `.kraft/` folder at the repo root, addressed as
   `repo:<id>`, for example `repo:ship`. Lite has no repo id of its own, so
   `repo:` stands for this repo. Inside that library a bare name means its
   own entry, as in Kraft.
3. **Your library.** The directory Kraft reads its configuration from:
   `$KRAFT_CONFIG_DIR` when set (or its 1.x name `$KRAFT_TEMPLATES_DIR`), else
   `$KRAFT_HOME/config`, else `~/.kraft/config`. The first one set is the one,
   even without a `library.yaml` in it: Kraft seeds that directory from its
   packaged library, so Lite reads the bundled one, as step 4. Your library
   replaces the bundled library wholesale, with no layering, as it does in
   Kraft. A bare name such as `default` means an entry here.
4. **The bundled library.** A copy of Kraft's packaged `config/library.yaml`
   and `config/chains/`, shipped with the plugin and kept identical by a test.

A repo library differs from Kraft's in two ways. Lite reads the working tree,
not the default branch's commit, and there is no acceptance step, because you
are already running in that checkout. An edit to `.kraft/chains/ship.yaml`
counts at your next `start`, committed or not. See
[repo libraries](https://kraft-sdlc.dev/reference/configuration/repo-libraries) for what the folder holds.

With no `--chain`, `start` runs `repo:default` when the repo library has a
`chains/default.yaml`, and `default` otherwise. `kl.py chains --available`
lists every chain id Lite can see, with the library it came from and its
file. A reference to an add-on's chain, such as `acme:ship`, is refused by
name: Lite loads no add-ons.

### Reading the YAML

Lite needs nothing installed. When PyYAML is importable it reads with
`yaml.safe_load`, the reader Kraft uses. Otherwise it uses a built-in reader
for the subset Kraft's own files are written in: block and flow collections,
plain and quoted scalars, `|` and `>` block scalars, and comments.

The built-in reader refuses what it cannot be sure to read as PyYAML would,
and names the file and line: anchors, aliases, tags, merge keys (`<<`), more
than one document, complex keys, multi-line quoted scalars, and plain
scalars PyYAML would read as a timestamp. Quote the value, or install PyYAML.

It also refuses what PyYAML itself rejects: a tab outside a quoted or block
scalar's text or a comment, and a plain scalar that starts with an indicator
such as `%`, `,`, `]` or `? `.

## What runs each task

Lite supplies the order, the gates and the caps. A task runs on its default
unless `.kraft-lite/bindings.yaml` binds it to something else. The file is
optional, so is every entry in it, and a chain runs with no file at all.
`/kraft-lite:init` writes one from what it finds in the repo, listing only
the entries that differ from the defaults. Edit it freely and commit it.

The file has three sections, plus a fourth for children:

- `tasks`: by the library component's name (`spec_author`), or by canonical
  path, `node.step.task` (`implementation.main.implement`), the paths Kraft's
  overrides use.
- `builtins`: by ref, `kraft.verify_changed_test_scopes` or `kraft.mr_rebase`.
- `forge`: by target, such as `mr.open_draft`, `mr.ci` or `mr.merge`.
- `slice`: the same three sections, read only in a child walking a slice,
  ahead of everything else.

```yaml
tasks:
  spec_author:
    kind: skill
    skill: superpowers:brainstorming
    prompt: Agree requirements and write a spec before any code.
  verification.review.code_review:
    kind: skip
    why: the merge request review covers it
builtins:
  kraft.verify_changed_test_scopes:
    kind: subprocess
    command: [just, test]
forge:
  mr.ci:
    kind: subprocess
    command: [gh, pr, checks]
slice:
  tasks:
    implementer:
      kind: prompt
      prompt: Implement the slice's tasks test first, one at a time.
```

An entry has a `kind`:

- `skill`: invoke `skill`. It needs a `prompt` too, which runs instead if the
  skill is not installed, so a renamed skill degrades to an instruction.
- `prompt`: follow the `prompt`.
- `subprocess`: run `command`, a list of arguments, with no shell.
- `skip`: run nothing. It needs a `why`, which is shown when the task is
  skipped.

### Which binding wins

The first match wins: the task's canonical path, then each library component
it extends, nearest first, then its ref (a builtin task) or target (a forge
task). A child walking a slice looks in `slice:` first, then in its built-in
slice defaults, then in the top-level sections.

With no match, the task runs on its default:

- An **agent** task runs its `skill` if that is installed in this harness,
  else its `prompt`.
- A **subprocess** task runs its `command`, split like a shell and run
  without one, as Kraft runs it. A command Lite cannot split, such as one
  with an unclosed quote, stops `start`.
- A **builtin** or **forge** task runs an instruction Lite carries for every
  ref and target Kraft allows, written for this repo's forge.

Every task that runs a skill or a prompt, bound or default, gets the task's
`steering` text appended. Its `profile`, `model` and `effort` reach the
session as hints for choosing a subagent's model.

`start` works out the instruction for every task before it writes anything, so
a bindings file Lite cannot use, or a task with no instruction, stops `start`
and names the entry. The file is read again on every dispatch.

## What Lite reads past, and what it refuses

Lite runs inside one session, so it cannot enforce a budget, a time cap, an
allowed-tools list, a sandbox or a harness choice. It reads past `policy`
(all but a node's `max_attempts`, which caps its fix loop), `wait`, `scope`, `execution`, `harness`, `inputs`, `skippable`, `read_only`,
`icon`, `timeout`, `artifact_required`, `escalation`, and a gate's
`auto_review`. A chain that sets them still runs. An escalation is you, as
for a fix loop past its cap.

Lite stops, naming the file and the key, at what it cannot walk without
changing what the chain means: a reference to an add-on, a step's or a
task's own `on_failure`, and `on_conflict` under `on_base_changed`. It also
refuses the cases Kraft refuses, such as an unknown builtin ref or forge
target.

Lite checks what it needs to walk a chain, not everything Kraft would.
Before relying on a chain you wrote, run
[`kraft admin templates lint --dir`](https://kraft-sdlc.dev/reference/cli/admin#lint-the-templates) on its folder: that is the full
check.

## How a node runs

A chain is nodes, and a node is either an **exec** node or a **gate**. An
exec node holds steps, each of tasks. Lite runs the steps in order, and the
tasks inside one step in any order or in parallel. A step fails when a task
in it fails or reports findings of critical or important severity, and no
later step runs after that.

When a task has `produces`, Lite records the file it wrote with
`kl.py produced --kind <kind> --path <file>`. A gate whose `artifact` names
that kind then presents the newest such file.

A node that fails goes through its fix loop, in Kraft's order:

1. If the node has an `on_failure` and a task failed, that runs once, then
   the node runs again from its first step.
2. A builtin or forge task that fails with no findings, such as missing
   credentials, stops for you. It spends no attempt.
3. Each cycle spends an attempt. From the second, the loop's judge, if it
   has one, rules first: continue, stop for you, or finish with the findings
   left.
4. Past the cap, the chain stops and shows you every attempt's output. The
   cap is the loop's `max_attempts`, else the node policy's
   `max_attempts`, else 3.
5. The loop's repair steps run, then the whole node again from its first
   step.

A step that moved the branch's base, such as a rebase or sync that did work,
has not failed. If the node has `on_base_changed`, `kl.py restart` reopens
the walk at its `restart_from` node and every node through this one, with
their attempts reset. A node restarts at most three times.

### Gates

A gate is a node of its own, with a `message`, an `artifact`, and where a
rejection goes. You `approve` it or `reject` it; `close` is refused. A
rejection reopens the node the gate's `reject_to` names, else the nearest exec
node before it, and every node after that, carrying your note. `reject
--from-node` lands the rejection on another node.

A gate on a `chain_revision` artifact is the one case Lite cannot finish: it
cannot apply a change set to a running chain. If the proposal changes
anything, Lite says so and offers two ways on: approve without the change, or
stop and start again on an edited copy of the chain's file.

That file is the frozen chain's `source` when it is a path, else the one `kl.py chains --available`
lists for its id; a `repo:` chain's copy goes in `.kraft/chains/` under a new
id. The new run passes the approved spec and plan with `--spec` and `--plan`,
so it starts at the chain revision.

### What `state` reports

`kl.py state` is all a session needs to carry on, so a cleared context loses
nothing. It reports `node`, `kind` (`exec` or `gate`), `status`, `note`,
`attempt`, `cap`, `backend`, `sliced`, and for an exec node its `steps`,
`on_failure`, `on_failure_ran`, `fix_loop` and `on_base_changed`. For a gate
it reports `gate`: the `message`, the `artifact` and its `artifact_path`, the
`reject_to` node, and whether it is the `chain_finalized` or the slice gate.

Each task in `steps` is one dispatch record: the `path`, how it runs (`kind`
is `skill`, `prompt`, `subprocess` or `skip`, with `skill`, `prompt`,
`command` or `why`), `produces`, `hints`, and `source`, the rule that chose
it. Its own `task_kind` (`agent`, `builtin`, `forge` or `subprocess`), `ref`
and `target` come from the chain, whatever binding ran it.

## Start with a spec or a plan

`start --spec PATH` and `start --plan PATH` hand over documents you already
have. For each kind, Lite drops the gate that approves it, never the
`chain_finalized` gate, and the exec node whose tasks all produce it. A
surviving gate whose `reject_to` named a dropped node falls back to the
nearest exec node before it. A surviving `on_base_changed.restart_from` that
named a dropped node moves to the nearest kept exec node before it, else the
first one kept.

Lite records the path, relative to the repo when the file is inside it, and
does not copy the file. The gates that remain present it as that kind's
artifact. With neither flag, the chain writes both documents itself.

## State

`bd` when the repo has it, otherwise `.kraft-lite/chain.jsonl` — the same
`bd export` format either way, so adopting `bd` later is `bd import`, not a
migration.

`/kraft-lite:status` lists what is in the directory; `kl.py chains` is the verb
behind it. `kl.py summary` reports one run back: per-node times split into
work and waiting at a gate, attempts against their caps, gates answered, and
rejection notes. To make that possible, every write also records the wall-clock
time it happened, the seconds already spent waiting at a gate, and the attempts
already spent, because a later write would otherwise overwrite each one. It
counts no tokens and no money — Lite runs inside your session and never sees
them.

A directory can hold several chains. Every `kl.py` verb that reads or writes a chain takes `--chain-id <id>`; with one
unfinished chain the flag is optional, and with two or more it is required —
Lite refuses to guess which run a gate belongs to. Each run's frozen chain lives in
`.kraft-lite/chains/<chain-id>.json`, one per run, so two runs can walk
different chains side by side.

## Slices: one plan, several sessions

A large plan strains one session: one context carries every task, and nothing
runs in parallel. Slices split the plan across sessions. The session that walks
the feature chain becomes the **controller**. Each slice is one or more plan
tasks, built by its own **child** session.

### When to slice

Slice at the slice gate, when the approved plan is large. The
controller reads the plan's `## Slices` section, whose bullets look like
`- **Slice 2: <title>.** Tasks 3-5. Comes after slice 1.` A plan without that
section gets a proposed grouping. Either way the proposal shows which slices
run in parallel, and gives each helper or fixture that two slices would create
one owner.

The proposal is shown at the slice gate, and your answer is the
approval. A small plan is not sliced: the controller says so, and the chain
continues as one. Slicing is accepted only at that gate. A rejection leaves no
slice behind.

Sending the chain back with `reject --from-node` keeps the slices only when the
plan stands. A rewind to the slice gate or earlier retires them: `slices`
lists none, and the chain is sliced again, from a new proposal, at the slice
gate.

A rewind to the first build node is refused while every slice is done, because
it would close again at once with nothing redone. Reopen the slices to redo
first, with `slice-set --slice KEY --status running`. A rewind to a node after
the first build node leaves the slices as they are.

### Where a chain slices

Lite finds the places from the chain's shape, never from node names, so a
chain you wrote slices as the default does. `state` marks the slice gate, and
`slices` reports the `shape` it found.

- The **first build node** is the first exec node none of whose tasks
  produces a document. When a node produces the `plan`, it is the first such
  node after the last one that does, so a check node before the plan is not
  it. In the default chain, `implementation`.
- The **slice gate** is the last gate before the first build node, and is
  never the `chain_finalized` gate. In the default chain,
  `chain_revision_approval`. In a chain with a plan gate and then the build,
  the plan gate.
- The **MR node** is the first node with a forge task that targets
  `mr.open_draft`. It must come after the first build node. In the default
  chain, `draft_merge_request`.
- The **child chain** is the nodes after the slice gate and before the first
  gate after the MR node, less every gate and every exec node that produces a
  document some gate presents. In the default chain: `implementation`,
  `verification`, `describe_merge_request`, `draft_merge_request` and
  `merge_request_feedback`.

A chain without that shape is walked unsliced. `approve --slices` refuses on
it and names the missing piece, such as "no gate before the first build node",
which is what Kraft's `quick-task` chain lacks.

### Controller and children

The controller starts a child for each slice whose "comes after" slices are
merged, so independent slices run in parallel. A slice that comes after exactly
one slice may start early, from that slice's branch, once that slice is in
review or ready to merge. The controller logs that choice. A slice that comes
after two or more waits until all of them are merged.

A child walks the child chain (above), cut from the controller's frozen
chain. It starts with `kl.py start --slice-of <state checkout>/chain.json
--title "<slice>"`, so it never resolves a library itself and walks exactly
what the controller froze. A kept node's `on_base_changed.restart_from` that
names a dropped node moves to the nearest kept exec node before it, else to
the child chain's first node.

The child's `slice:` bindings win over everything else. Without them, the
agent tasks of the first build node run the subagent-driven skill, whatever
component they extend. The changed-scope tests run only the paths the slice
touches, and the merge request's automated review is skipped, because the
controller's review is that review. CI runs the full suite.

Where Lite would stop for you, the child asks the controller, never you. The
controller answers, reviews each child's pull request, merges, archives the
finished child, and keeps the run's state. Every subagent and child gets a
model set on purpose: the cheapest for reading and relaying, the middle for
implementation and child sessions, the strongest for reviews and judgment calls.

The controller does not start a child until you have finished giving
instructions for the run. Say "go" when you have.

### Two pull request modes

The default is the **feature** mode. Each slice opens its pull request into a
feature branch, and the controller reviews it and merges it there on green CI.
When every slice is in, the controller opens the pull request from the feature
branch to `main`, and its review covers how the slices fit together. You approve
and merge that one.

Before the MR node, an exec node that produces nothing, such as `verification`
in the default chain, runs only its changed-scope tests over the feature
branch's diff, where CI cannot cover it. Every other node runs as in an
unsliced chain, and you answer its gates.

In the **main** mode, each slice opens its own pull request to `main`. The
controller reviews it and reports, and you merge it.

A slice is merged only once you merge it. A slice after exactly one other need
not wait for that: while that slice is in review or ready to merge, it can
start early from its branch, and it moves onto `main` when you merge that
slice. A slice that comes after two or more waits until you have merged them
all, then starts from `main`.

In the main mode, every node after the first build node closes with nothing
to do, as there is no single merge to watch. Two kinds wait until you have
merged every slice pull request: the `chain_finalized` gate, and a node that
merges a merge request (`final_review` and `merge` in the default chain).

Set the mode in `.kraft-lite/slices.json`, or say so when the run starts.

### Who decides what

These are the defaults:

| Decision | Setting | Default |
|---|---|---|
| A plan question a child raises | `plan_question` | controller |
| Its review verdict on a slice | `slice_review` | controller |
| Approving and merging a slice pull request into the feature branch | `merge_slice` | controller |
| The pull request from the feature branch to `main` | `merge_main` | user |
| A push outside the child's own slice branch | `push_outside_branch` | user |

Change any of them for one run by saying so when it starts, or as a standing
rule in `.kraft-lite/slices.json`, which you commit beside `bindings.yaml`:

```json
{
  "mode": "main",
  "rights": { "merge_slice": "user" }
}
```

A right you leave out keeps its default. An unknown key or value is refused,
naming it. `kl.py settings` prints what is in effect.

The controller writes down every call it makes on its own: the question, the
answer and why. It shows you the log when you next check in, and again before
the work wraps up, while a call is still cheap to overturn. The log is
`.kraft-lite/decisions/<chain-id>.jsonl`.

The controller may hold a critical question, or one that is costly to undo, for
you. The child keeps working on whatever does not depend on it. Silence is never
an answer: a held question stays at the top of every status until you answer
it. A decision that is only yours, such as a push the child was not given, is
passed up to you with the words to type and the session to type them in.

### The state branch

`.kraft-lite/` is not in git, and a cloud container comes back as a fresh clone.
So after every change the controller exports the run to a branch of its own and
pushes it. The export is the feature chain's and the slices' records, its
frozen chain (`chain.json`) and the decisions log. A resumed controller imports
it before it writes anything. An export made by an older Kraft Lite has no
`chain.json`, and `import` refuses it.

In a cloud session the state branch is the controller's own session branch.
Locally it is the feature branch's name plus `-state`. It holds state only,
never product code, specs or plans, and is never merged. Specs and plans reach
the children as messages. At the end the controller lists the branches that are
safe for you to delete.

Add `.kraft-lite/decisions/`, `.kraft-lite/messages/` and
`.kraft-lite/proposals/` to `.gitignore`, and `.kraft-lite/registry.yaml.migrated`
if `migrate` left one. `/kraft-lite:init` tells you to.

### Harness support

Slices need four abilities from the harness: start a child session, message it,
be messaged by it, and archive it. The controller also needs a way to check in
later. `skills/run-slices/references/` maps them to the tools of Claude Code,
Codex and Cursor, and marks the ones it could not confirm.

Where a harness lacks an ability, there is a fallback. A child on the same
machine writes to a file under `.kraft-lite/messages/`. A child on another
machine comments on its own draft pull request. A controller that cannot be
woken says so, and you run `/kraft-lite:run-slices` to continue the run.

If the harness has no session tools at all, the controller walks the slices
itself, one after another. A harness with no reference, such as Amp or Gemini,
gets every fallback, and the run goes on.

A cloud child cannot message a controller on another machine. For a local
controller with cloud children, pull request comments are the channel.

### Verbs for slices

The skills run these. `kl.py state` also reports `sliced` once a run is sliced.
`slices` reports `shape`, the slice gate, first build node and MR node it found,
or why the chain cannot be sliced.

| Verb | What it does |
|---|---|
| `approve --slices FILE` | Approves the slice gate and creates the slices. A plan with a cycle, an unknown `after`, a duplicate key, an empty task list, a task in two slices or a bad branch is refused, and nothing is written. |
| `start --slice-of FILE` | Starts a child on the child chain cut from the exported `chain.json` at FILE, with the `slice:` bindings. It takes no `--chain`, `--spec` or `--plan`. |
| `slices` | Lists the slices, and which may start or must rebase (below). |
| `slice-set --slice KEY` | Sets `--status`, `--session`, `--branch` or `--fork`. Statuses: `waiting`, `starting`, `running`, `in_review`, `ready_to_merge`, `merged`. The last slice merged closes the run. A call that changes nothing writes nothing. |
| `settings` | Prints the mode and the rights in effect. |
| `decide` | Logs a decision, under a slice with `--slice KEY`, which must name one of the run's slices. With `--held`, holds the question for you. |
| `answer --decision ID --answer TEXT` | Records your answer to a held question. A decision that is not held is refused. |
| `decisions` | Lists the log. `--held` shows held questions, `--unreported` what is new or newly answered since the last report, and `--mark` marks those reported. |
| `export --to DIR`, `import --from DIR` | Copy a run to the state branch's checkout, and back. An import never overwrites a newer local record or decision: it keeps the copy written more times, so a clock that runs behind cannot keep a stale one. |

`slices` gives each slice's status, what it comes after, its session and
branch, and `cut_from`, the slice it was cut early from. `ready` lists those
whose "comes after" slices are all merged. `early` lists those that come after
exactly one slice, which is in review or ready to merge.

`base_moved` lists the started slices cut early from a slice whose branch has
since moved, and those cut early from them: each rebases onto the new tip of
the slice its `cut_from` names.

`slice-set` starts a waiting slice only from `ready` or `early`, and moves
one never started no further than `running`. It records a slice merged only
once every slice it comes after is merged, so a slice cut early from another's
branch merges after that one.

A slice that goes back from review to work, or records a new `--fork`, flags
each slice cut early from it in `base_moved`, and each cut early from those,
until each records the fork of its rebase with `--fork`. `slice-set` refuses
a `--fork` that is not 4 to 64 hex digits, a `--branch` that is not a good
branch name (below), and a `--session` that is empty or holds whitespace.

`close` and `approve` refuse to close a sliced run's first build node until
every slice is done: merged, or in `main` mode merged or ready to merge. In
`main` mode they refuse to close a node that waits for your merges until every
slice is merged. The refusal names the slices that are not.

`import` refuses a directory with no `records.jsonl`, naming the path, and a
chain id that `start` would not mint (`kl-` and 8 of `0-9a-f`), before it reads
or writes a file. It refuses an export holding a record that is not its
chain's epic, node, run or slice under the id it was minted with, naming each,
and a line it cannot read, naming the file and line, and writes nothing.

`import` ranks decisions as it ranks records, comparing stamps as times, so a
held copy never takes back an answer given where the clock runs behind.

`approve --slices` also refuses a task listed twice in one slice, a slice
named twice in one `after`, and a key that is not 1 to 40 lowercase letters,
digits, `-` and `_`, starting with a letter or digit. It refuses a key the
plan or a slice does not take, naming it, so a misspelt `after` is not
dropped.

A bad branch is one that is not a string, holds whitespace, starts with `-`,
or is one git refuses (`..`, a part starting with `.`, an ending of `/`, `.` or
`.lock`, or any of `~^:?*[\`), a feature branch that is also the state branch,
or any feature branch in `main` mode, which has none.

## Moving an older directory

Before Kraft Lite walked Kraft's own chains, a repo bound hooks in
`.kraft-lite/registry.yaml`, and a run froze one of the plugin's JSON chains.
A directory in that state is carried over, not stranded:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/kl.py" migrate
```

Until then, a verb that meets a legacy registry, or an unfinished run in the
old format, refuses with one line telling you to run `migrate`. Only `chains`, `detect` and
`migrate` run beside a registry that has no bindings file.

`migrate` does two things.

- **The registry becomes `bindings.yaml`.** Each hook maps to the task, ref or
  target that now does its job (the table below). A binding that says what
  the default already says is dropped, and so is a hook V1 has no task for.
  The command lists what it dropped. Comments are not carried. The old file is
  renamed `registry.yaml.migrated`: add it to `.gitignore`, or delete it.
- **Each unfinished run moves to a new id** on Kraft's bundled `default`, not a
  `default` from your library or the repo's. Closed nodes stay closed, and
  the node the run was at keeps its attempts and note. The old run is closed
  and labelled `kraft-migrated-to:<new id>`.

| Old hook | Binding it becomes |
|---|---|
| `on.spec.requested`, `on.plan.requested`, `on.chain.review_ready` | `tasks`: `spec_author`, `plan_author`, `revise_chain` |
| `on.implementation.start`, `on.review.local.run` | `tasks`: `implementer`, `code_review` |
| `on.mr.describe` | `tasks`: `describe_mr` |
| `on.test.run`, `on.mr.rebase` | `builtins`: `kraft.verify_changed_test_scopes`, `kraft.mr_rebase` |
| `on.mr.open` | `forge`: `mr.open_draft` |
| `on.ci.poll`, `on.review.mr.run` | `forge`: `mr.ci`, `mr.automated_review` |
| `on.merge`, `on.merge.watch` | `forge`: `mr.merge`, `mr.post_merge_ci` |
| `on.slice.implementation.start`, `on.slice.test.run` | `slice`: `tasks.implementer`, `builtins.kraft.verify_changed_test_scopes` |
| `on.env.prepare`, `on.repos.scan` | dropped: Kraft's worktree setup has no such task |
| `on.mr.sync` | dropped: it pulled the merge request into the branch, and V1's `mr.sync` pushes the branch |
| `on.human_review.requested` | dropped: presenting the work for sign-off is the `final_review` gate's job; `write_summary` only writes the brief it shows |

A legacy node becomes one or more V1 nodes, so a run lands on the same work:
`spec` and `plan` each become the node and its approval gate; `chain_review`
becomes `chain_revision` and `chain_revision_approval`; `verify` becomes
`verification`; `open_mr` becomes `work_brief`, `local_review`,
`describe_merge_request` and `draft_merge_request`; `mr_checks` becomes
`merge_request_feedback`; `human_review` becomes `work_item_summary` and
`final_review`; `merge` becomes `mark_ready`, `external_approval` and `merge`;
and `post_merge_watch` becomes `post_merge_ci`.

`migrate` refuses, and migrates nothing, when it finds:

- a run that is sliced, or a slice's child run;
- a run on a custom chain, or on one with nodes the old default lacked;
- a run whose chain record is missing;
- a registry binding a hook outside the table. Take it out, run `migrate`,
  then add its binding to `bindings.yaml` by hand, keyed by task name;
- a registry entry that would make a bindings file Lite refuses, such as a
  `skill` with no `prompt`. Correct it in the registry;
- both `bindings.yaml` and `registry.yaml`, because it would overwrite the
  bindings. Move yours aside, run `migrate`, and merge the two by hand.

For a run it refuses, finish it on the previous Kraft Lite version, or abandon
it, then run `migrate` again:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/kl.py" migrate --abandon <chain-id>
```

`--abandon` closes the run's open records, each labelled `kraft-abandoned`,
and deletes nothing: the records and the frozen chain stay. It refuses an id
that is not an unfinished run in the old format.

A run that finished in the old format has nothing to move, and blocks
nothing. `state` reports it `done` and `chains` lists it, but `summary`
refuses, because its summary needs the previous Kraft Lite.

`migrate` is safe to run twice: it renames the registry last, so a run
interrupted halfway finishes when you run it again. With nothing left to
move, it reports nothing moved.

A state export written before this change holds no `chain.json`, and `import`
refuses it, saying it came from an older Kraft Lite.

## What it does not do

Unattended execution, a web board, waiting on CI while you are away, spend caps,
cross-repo search. Lite is the attended case: the chain is in front of you,
resumable across sessions but not outliving your terminal. Several chains can
share a directory, but nothing walks one while you are away. Those other things
need a process that keeps running when you close the lid, which is a different
piece of software.

Slices spread one plan over several sessions, so a large plan no longer has to
fit in one session. Someone still starts the run, and wakes the controller where
its harness cannot.

## Tests

From `plugins/kraft-lite/`, run the suite with your Python test runner
(`pytest tests -q`). From the Kraft repo root, use `just test
plugins/kraft-lite/tests`.

## Contributing

This plugin lives in `plugins/kraft-lite/` in the
[Kraft](https://github.com/itsOmidKarami/kraft) monorepo, the same repository
`kraft` itself ships from. Open a normal pull request there — see the root
[CONTRIBUTING.md](../../CONTRIBUTING.md) for tests, the release-label
convention, and how a merge becomes a release.

## License

Apache-2.0. See [LICENSE](LICENSE).
