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

    git clone https://github.com/itsOmidKarami/kraft-lite ~/.claude/skills/kraft-lite

Or clone into `.claude/skills/` for one repo only. The directory carries a plugin
manifest, which is what namespaces the commands as `/kraft-lite:*`. It needs
Python 3.10 or newer and nothing else — no pip install, no dependencies. CI tests
both ends of that range.

To update, see [Contributing](#contributing) — `main` is force-pushed, so `git
pull` will not work.

## What runs each node

`/kraft-lite:init` detects which skills you already have and writes
`.kraft-lite/registry.yaml` binding each node to one of them. Lite does not
supply a spec writer or a planner; it supplies the order, the gates, and the
caps, and calls whatever you already use. Edit that file freely — it is meant to
be read, diffed and committed.

The chain itself is `chains/default.json`: ten nodes from spec through plan,
implementation, verification, and review, with four gates where a human decides.

## State

`bd` when the repo has it, otherwise `.kraft-lite/chain.jsonl` — the same
`bd export` format either way, so adopting `bd` later is `bd import`, not a
migration.

## What it does not do

Unattended execution, parallel chains, a web board, CI polling, spend caps,
cross-repo search. Lite is the attended case: one chain, in front of you,
resumable across sessions but not outliving your terminal. Those other things
need a process that keeps running when you close the lid, which is a different
piece of software.

## Tests

    pytest tests -q

## Contributing

`main` here is regenerated and force-pushed from a private monorepo on every
publish, so a branch based on it loses its merge base and `git pull` on an
existing clone will diverge. Update with:

    git fetch && git reset --hard origin/main

Issues and PRs are welcome regardless: a PR is cherry-picked upstream and returns
in the next publish rather than being merged here.

## License

MIT. See [LICENSE](LICENSE).
