# Kraft Lite

Runs a chain of work inside one agent session: ordered nodes, human gates at the
points where a decision belongs to you, and fix loops that are capped instead of
endless. No service, no port, no database process.

    /kraft-lite:init            # once per repo — writes .kraft-lite/registry.yaml
    /kraft-lite:start "<work>"  # materialize the chain and run the first node
    /kraft-lite:next            # run the next node; also the resume point
    /kraft-lite:gate            # approve or reject
    /kraft-lite:status          # where the chain got to

## Install

This repo is its own marketplace:

    /plugin marketplace add itsOmidKarami/kraft-lite
    /plugin install kraft-lite@kraft-lite

That is what namespaces the commands as `/kraft-lite:*`. `/plugin update
kraft-lite` afterwards — Claude Code owns the clone, so the force-push below
never becomes your problem. It needs Python 3.10 or newer and nothing else — no
pip install, no dependencies. CI tests both ends of that range.

Add `--scope project` to either command to keep it to one repo. Each release is
tagged `kraft-lite--vX.Y.Z`, which is the only stable point in this history —
see [Contributing](#contributing).

## What is fixed and what is not

A chain freezes when it starts: its nodes cannot be reordered, added to or
removed, and a verb that meets a node missing from the frozen template fails
rather than guessing. The registry is the opposite - it is re-read on every hook
dispatch, so rebinding a hook is how a chain already in flight gets corrected.

## What runs each node

`/kraft-lite:init` detects which skills you already have and writes
`.kraft-lite/registry.yaml` binding each node to one of them. Lite does not
supply a spec writer or a planner; it supplies the order, the gates, and the
caps, and calls whatever you already use. Edit that file freely — it is meant to
be read, diffed and committed.

The chain itself is `chains/default.json`: eleven nodes from spec through plan,
implementation, verification, and review, with four gates where a human decides.

## State

`bd` when the repo has it, otherwise `.kraft-lite/chain.jsonl` — the same
`bd export` format either way, so adopting `bd` later is `bd import`, not a
migration.

`kraft-lite:status` lists what is in the directory; `kl.py chains` is the verb
behind it. `kl.py summary` reports one run back: per-node times split into
work and waiting at a gate, attempts against their caps, gates answered and
rejection notes, off records the walk was writing anyway. Three labels ride along
on those writes to make it possible - when a record was written by the wall clock,
seconds already spent at a gate, and attempts already spent - because each is
something a later write would otherwise overwrite. It counts no tokens and no money - Lite runs inside your
session and never sees them.

A directory can hold several chains. Every verb takes `--chain-id <id>`; with one
unfinished chain the flag is optional, and with two or more it is required —
Lite refuses to guess which run a gate belongs to. Frozen chain templates live in
`.kraft-lite/chains/<chain-id>.json`, one per run, so two chains can walk
different templates side by side.

## What it does not do

Unattended execution, a web board, CI polling, spend caps,
cross-repo search. Lite is the attended case: the chain is in front of you,
resumable across sessions but not outliving your terminal. Several chains can
share a directory, but nothing walks one while you are away. Those other things
need a process that keeps running when you close the lid, which is a different
piece of software.

## Tests

    pytest tests -q

## Contributing

`main` here is regenerated and force-pushed from a private monorepo on every
publish, so a branch based on it loses its merge base and `git pull` on a hand
clone will diverge. Refresh a hand clone with:

    git fetch && git reset --hard origin/main

Issues and PRs are welcome regardless: a PR is cherry-picked upstream and returns
in the next publish rather than being merged here.

## License

MIT. See [LICENSE](LICENSE).
