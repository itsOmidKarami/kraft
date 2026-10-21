# Kraft Lite

Runs a chain of work inside one agent session: ordered nodes, human gates at the
points where a decision belongs to you, and fix loops that are capped instead of
endless. No service, no port, no database process.

    /kraft-lite:init            # once per repo — writes .kraft-lite/registry.yaml
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

A chain freezes when it starts: its nodes cannot be reordered, added to or
removed, and a verb that meets a node missing from the frozen template fails
rather than guessing. The registry is the opposite - it is re-read on every hook
dispatch, so rebinding a hook is how a chain already in flight gets corrected. A hook is a named step a
node runs, such as `on.spec.requested`.

## What runs each node

`/kraft-lite:init` detects which skills you already have and writes
`.kraft-lite/registry.yaml` binding each node to one of them. Lite does not
supply a spec writer or a planner; it supplies the order, the gates, and the
caps, and calls whatever you already use. Edit that file freely — it is meant to
be read, diffed and committed.

The chain itself is `chains/default.json`: ten nodes from spec through plan,
implementation, verification, and review, with four gates where a human decides. Two nodes, `verify` and `mr_checks`,
carry a fix loop capped at three attempts; a node past its cap stops the chain
and escalates to you.

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
Lite refuses to guess which run a gate belongs to. Frozen chain templates live in
`.kraft-lite/chains/<chain-id>.json`, one per run, so two chains can walk
different templates side by side.

## Slices: one plan, several sessions

A large plan strains one session: one context carries every task, and nothing
runs in parallel. Slices split the plan across sessions. The session that walks
the feature chain becomes the **controller**. Each slice is one or more plan
tasks, built by its own **child** session.

### When to slice

Slice at the `chain_review` node, when the approved plan is large. The
controller reads the plan's `## Slices` section, whose bullets look like
`- **Slice 2: <title>.** Tasks 3-5. Comes after slice 1.` A plan without that
section gets a proposed grouping. Either way the proposal shows which slices
run in parallel, and gives each helper or fixture that two slices would create
one owner.

The proposal is shown at the `chain_finalized` gate, and your answer is the
approval. A small plan is not sliced: the controller says so, and the chain
continues as one. Slicing is accepted only at that gate. A rejection leaves no
slice behind.

### Controller and children

The controller starts a child for each slice whose "comes after" slices are
merged, so independent slices run in parallel. A slice that comes after exactly
one slice may start early, from that slice's branch, once that slice is in
review or ready to merge. The controller logs that choice. A slice that comes
after two or more waits until all of them are merged.

A child walks `chains/slice.json`:
`implementation`, `verify`, `open_mr` and `mr_checks`. It runs scoped tests
only, and CI runs the full suite.

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
and merge that one. `verify` tests only what the feature branch changed, where
CI cannot cover it.

In the **main** mode, each slice opens its own pull request to `main`. The
controller reviews it and reports, and you merge it.

A slice is merged only once you merge it. A slice after exactly one other need
not wait for that: while that slice is in review or ready to merge, it can
start early from its branch, and it moves onto `main` when you merge that
slice. A slice that comes after two or more waits until you have merged them
all, then starts from `main`.

`verify`, `open_mr`, `mr_checks` and `post_merge_watch` close with nothing to
do, as there is no single merge to watch. `human_review` and `merge` wait until
you have merged every slice pull request.

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
rule in `.kraft-lite/slices.json`, which you commit beside `registry.yaml`:

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
pushes it. The export is the feature chain's and the slices' records, the frozen
chain template and the decisions log. A resumed controller imports it before it
writes anything.

In a cloud session the state branch is the controller's own session branch.
Locally it is the feature branch's name plus `-state`. It holds state only,
never product code, specs or plans, and is never merged. Specs and plans reach
the children as messages. At the end the controller lists the branches that are
safe for you to delete.

Add `.kraft-lite/decisions/`, `.kraft-lite/messages/` and
`.kraft-lite/proposals/` to `.gitignore`. `/kraft-lite:init` tells you to.

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

| Verb | What it does |
|---|---|
| `approve --slices FILE` | Approves `chain_finalized` and creates the slices from the proposal. A plan with a cycle, an unknown `after`, a duplicate key, an empty task list, a task in two slices or a bad branch is refused, and nothing is written. |
| `slices` | Lists the slices with their status, what they come after, session and branch. `ready` lists those whose "comes after" slices are all merged. `early` lists those that come after exactly one slice, which is in review or ready to merge. |
| `slice-set --slice KEY` | Sets `--status`, `--session`, `--branch` or `--fork`. Statuses: `waiting`, `starting`, `running`, `in_review`, `ready_to_merge`, `merged`. The last slice merged closes the run. A call that changes nothing writes nothing. |
| `settings` | Prints the mode and the rights in effect. |
| `decide` | Logs a decision, under a slice with `--slice KEY`, which must name one of the run's slices. With `--held`, holds the question for you. |
| `answer --decision ID --answer TEXT` | Records your answer to a held question. A decision that is not held is refused. |
| `decisions` | Lists the log. `--held` shows held questions, `--unreported` what is new or newly answered since the last report, and `--mark` marks those reported. |
| `export --to DIR`, `import --from DIR` | Copy a run to the state branch's checkout, and back. An import never overwrites a newer local record or decision: it keeps the copy written more times, so a clock that runs behind cannot keep a stale one. |

`slice-set` starts a waiting slice only from `ready` or `early`, and moves
one never started no further than `running`.

`close` refuses to close a sliced run's `implementation` until every slice is
done: merged, or in `main` mode merged or ready to merge. The refusal names the
slices that are not.

`import` refuses a directory with no `records.jsonl`, naming the path, and a
chain id that `start` would not mint (`kl-` and 8 of `0-9a-f`), before it reads
or writes a file. It ranks decisions as it ranks records, so a held copy never
takes back an answer given where the clock runs behind.

`approve --slices` also refuses a task listed twice in one slice, a slice
named twice in one `after`, and a key that is not 1 to 40 lowercase letters,
digits, `-` and `_`, starting with a letter or digit. A bad branch is a
feature branch that is also the state branch, or any feature branch in `main`
mode, which has none.

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
