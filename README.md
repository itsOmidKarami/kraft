# Kraft

[![test](https://github.com/itsOmidKarami/kraft/actions/workflows/test.yml/badge.svg)](https://github.com/itsOmidKarami/kraft/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/kraft-sdlc)](https://pypi.org/project/kraft-sdlc/)
[![Latest release](https://img.shields.io/github/v/release/itsOmidKarami/kraft)](https://github.com/itsOmidKarami/kraft/releases)
[![License](https://img.shields.io/github/license/itsOmidKarami/kraft)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-itsomidkarami.github.io%2Fkraft-blue)](https://itsomidkarami.github.io/kraft/)

Hand Claude Code (or another coding agent) a spec and walk away. Kraft runs
the work in its own git worktree, opens the pull request, and only interrupts
you when a decision is actually yours — for developers tired of babysitting a
session to the end of a task.

- **Gates where a human decides.** Kraft pauses at a spec, a plan, or a merge
  request for your approval; reject with a note and the step that wrote it
  tries again.
- **Capped retries, not runaway loops.** Attempts, wall-clock time, and spend
  are all bounded. Hit a cap and the item stops and hands you the full trace
  instead of quietly burning tokens.
- **Isolated git worktrees.** Each work item runs on its own branch in its own
  worktree, so your checkout stays as you left it and items run in parallel.
- **Local.** One process on your machine, bound to loopback by default — not a
  hosted service.
- **A board you can check from your phone.** Reach it from another device
  through a tunnel.

New here? [Why Kraft](https://itsomidkarami.github.io/kraft/get-started/why-kraft) covers what it does that a session, a loop or a skill does not, and when not to use it.

Under the hood, a work item runs as a
[chain](https://itsomidkarami.github.io/kraft/concepts/vocabulary) of ordered
nodes, each running tasks through plugin adapters (a headless agent, a
subprocess, a builtin). How it fits together:
[Architecture](https://itsomidkarami.github.io/kraft/project/architecture).

![The Kraft board: work items grouped by Needs you, Running, Not started, and Done](.github/assets/board.png)

<table>
<tr>
<td width="65%">

**A gate stops the chain where a human decides.** Approve, reject with a note
that re-runs the producing node, or open the full detail view.

![Approving a spec_approval gate from the board's side panel](.github/assets/gate.png)

</td>
<td width="35%">

**Same board, phone-sized.** Reach it from another device through a tunnel; see
[Remote access](https://itsomidkarami.github.io/kraft/guides/remote-access).

![The board at a 390px phone viewport, with bottom tab navigation](.github/assets/mobile.png)

</td>
</tr>
</table>

## Install

Install Kraft with `uv`, or with Homebrew on macOS:

```bash
uv tool install kraft-sdlc
# or
brew tap itsOmidKarami/kraft && brew install kraft
```

To drive Kraft from Claude Code, install the plugin, then run `/kraft:onboard`
in your repo and follow what it asks:

```text
/plugin marketplace add itsOmidKarami/kraft
/plugin install kraft@kraft
```

Every install path, connecting your agent, and updating with
`kraft admin update` are in the
[install guide](https://itsomidkarami.github.io/kraft/get-started/install).

## First run

```bash
kraft              # http://127.0.0.1:8765
kraft --version    # confirms what you installed
```

Open the URL, then follow the
[first-work-item tutorial](https://itsomidkarami.github.io/kraft/get-started/first-work-item)
to connect a repo and file your first work item.

## Analytics

Lead time, cost, and where both go — by node, by repo, over whatever window
you pick. Built from the same events the board renders live, not a separate
pipeline.

![The Analytics view: completed count, median lead time, cost; throughput by week; cost share by node; per-repo totals; why items stopped for a person](.github/assets/analytics.png)

## Where to go next

- [Concepts](https://itsomidkarami.github.io/kraft/concepts/vocabulary): work items, chains, nodes, gates, caps.
- [CLI reference](https://itsomidkarami.github.io/kraft/reference/cli): every `kraft` verb.
- [Configuration](https://itsomidkarami.github.io/kraft/reference/configuration): `repos.yaml`, `policy.yaml`, `access.yaml`, and the state directory (`$KRAFT_HOME`).
- [Agent integration](https://itsomidkarami.github.io/kraft/guides/agent-integration): MCP tools and skills.
- [Remote access](https://itsomidkarami.github.io/kraft/guides/remote-access): reach the board from a phone or another machine.
- [Triggers](https://itsomidkarami.github.io/kraft/reference/triggers): start a chain from a schedule or an HTTP call.
- [Security](https://itsomidkarami.github.io/kraft/project/security): threat model; report vulnerabilities per [SECURITY.md](SECURITY.md).

Kraft Lite (`plugins/kraft-lite/`) runs a chain inside a single agent session
with no service; see the [Kraft Lite guide](https://itsomidkarami.github.io/kraft/guides/kraft-lite).

## Contributing

Set-up, the `just` recipes, tests, and the pull request rules are in
[CONTRIBUTING.md](CONTRIBUTING.md). Releases are described in
[RELEASING.md](RELEASING.md).
