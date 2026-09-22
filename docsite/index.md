# Kraft

A local orchestrator for semi-autonomous software work. One FastAPI process plus a
React SPA: work items enter as **chains** — ordered nodes materialized from a YAML
template — and each node runs typed tasks (a headless agent, a subprocess, a
builtin, a forge action). Every retry loop is capped; hitting a cap escalates
to a person with the full trace. Gates stop the chain where a human decision belongs.

Runs on your machine, binds loopback by default, and edits your repos through git
worktrees.

![The Kraft board: work items grouped by Needs you, Running, Not started, and Done](assets/board.png)

## Start here

- **[Getting started](getting-started.md)** — install, connect a repo, run a
  work item end to end, approve its gate.
- **[Concepts](concepts.md)** — chain, node, task, gate, cap: the whole
  vocabulary.
- **[Install](install.md)** — `uv tool install`, Homebrew, or from source.
- **[CLI reference](cli.md)** — every `kraft` verb, grouped by what it does.
- **[Configuration](configuration.md)** — every field in `library.yaml`,
  `repos.yaml`, `policy.yaml`, `access.yaml`.
- **[Agent integration](agent-integration.md)** — driving Kraft with `/kraft:*`
  slash commands from a coding agent instead of the browser: onboarding a repo,
  checking its config, filing work, acting on gates. Install via the Claude
  Code plugin marketplace or `kraft admin init`.
- **[Remote access](remote-access.md)** — approving a gate from your phone.
- **[Inbound triggers](triggers.md)** — starting a chain from a cron schedule or a
  webhook instead of typing into `kraft item create`.

## A gate stops the chain where a human decides

Approve, reject with a note that re-runs the producing node, or open the full
detail view.

![Approving a spec_approval gate from the board's side panel](assets/gate.png)

## Search, ⌘K

Hybrid full-text + vector search across work items, pending actions, and linked
documents.

![The search overlay: a query for "caching" surfacing a pending gate action, the matching work item, and a source-repo attribution](assets/search.png)

## Analytics

Lead time, cost, and where both go — by node, by repo, over whatever window you
pick. Built from the same events the board renders live, not a separate pipeline.

![The Analytics view: completed count, median lead time, cost; throughput by week; cost share by node; per-repo totals](assets/analytics.png)

## Source

Kraft is on GitHub at [itsOmidKarami/kraft](https://github.com/itsOmidKarami/kraft),
Apache-2.0 licensed. [CONTRIBUTING.md](https://github.com/itsOmidKarami/kraft/blob/main/CONTRIBUTING.md)
covers getting a dev environment running and how releases work.
