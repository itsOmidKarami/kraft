# Kraft

[![test](https://github.com/itsOmidKarami/kraft/actions/workflows/test.yml/badge.svg)](https://github.com/itsOmidKarami/kraft/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/kraft-sdlc)](https://pypi.org/project/kraft-sdlc/)
[![Latest release](https://img.shields.io/github/v/release/itsOmidKarami/kraft)](https://github.com/itsOmidKarami/kraft/releases)
[![License](https://img.shields.io/github/license/itsOmidKarami/kraft)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-itsomidkarami.github.io%2Fkraft-blue)](https://itsomidkarami.github.io/kraft/)

**A local orchestrator that takes your coding agent from spec to pull request,
stopping only when a decision is yours.**

Kraft isn't another coding agent. It runs the one you already use, and adds
what a single session can't: a process the agent can't skip, checks it doesn't
grade itself on, and a person at the decisions that matter.

Hand Claude Code (or another coding agent) a spec and walk away. It's for
developers tired of babysitting a session to the end of a task.

![A 30-second tour: file a work item, read the spec an agent wrote at its gate, and look at a finished item's diff](https://github.com/user-attachments/assets/34d3197d-b07b-4f9a-a13b-04f0c377230e)

- **Gates where a human decides.** Kraft pauses at a spec, a plan, or a merge
  request for your approval; reject with a note and the node that wrote it
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

![The Kraft board: work items grouped by Needs you, Running, Not started, and Done](.github/assets/board.png)

<table>
<tr>
<td width="65%">

**A gate stops the chain where a human decides.** Read what the agent wrote,
then approve, or reject with a note that re-runs the node that wrote it.

![A spec an agent wrote, waiting for your approval in the item's detail view](.github/assets/gate.png)

</td>
<td width="35%">

**Same board, phone-sized.** Reach it from another device through a tunnel; see
[Remote access](https://itsomidkarami.github.io/kraft/guides/remote-access).

![The board at a 390px phone viewport, with bottom tab navigation](.github/assets/mobile.png)

</td>
</tr>
</table>

Each work item follows a
[chain](https://itsomidkarami.github.io/kraft/concepts/vocabulary): a list of
nodes such as write the spec, write the plan, implement, verify and open the
pull request. Some nodes run your coding agent, some run a command, and some
stop and wait for you. Chains are YAML files you can edit. How the pieces fit
together is in
[Architecture](https://itsomidkarami.github.io/kraft/project/architecture).

## Requirements

- **macOS or Linux.** CI runs on Linux, and `kraft admin install-service`
  supports launchd and systemd only. Windows is not supported, and WSL is
  untested.
- **`git`.**
- **[uv](https://docs.astral.sh/uv/) 0.9.0 or newer**, which fetches Python
  3.14 for you. Older uv offers only a 3.14 release candidate and the install
  fails; `uv self update` upgrades it. Or Homebrew on macOS.
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)**, installed
  and logged in. Every agent task in the shipped chains runs on it.
- **`gh` or `glab`**, logged in, for the nodes that open and merge the pull
  request on GitHub or GitLab.

**Supported agents:** Claude Code, Codex, Cursor, Gemini, OpenCode and Amp all
run as [harnesses](https://itsomidkarami.github.io/kraft/reference/harnesses).
The shipped chains use Claude Code only; to use another agent, edit the chains.

## What it costs

Kraft is free and Apache-2.0 licensed. Your agent provider bills you for the
sessions Kraft runs. The shipped `policy.yaml` caps spend at $10 per work item
and $50 per day across all items, and `kraft item create --budget USD` sets one
item's cap. A cap stops the next agent task from starting, so spend can go over
it by the cost of the task already running. In testing, a small fix on the
`quick-task` chain cost a few cents. See
[Caps and budgets](https://itsomidkarami.github.io/kraft/concepts/caps-and-budgets).

## Install

Install Kraft with `uv`, or with Homebrew on macOS:

```bash
uv tool install kraft-sdlc
# or
brew tap itsOmidKarami/kraft && brew install kraft
```

To drive Kraft from Claude Code, install the plugin:

```bash
claude plugin marketplace add itsOmidKarami/kraft
claude plugin install kraft@kraft
```

Start the server and leave it running in its own terminal:

```bash
kraft              # http://127.0.0.1:8765
kraft --version    # in another terminal: confirms what you installed
```

Then open a Claude Code session in your repo, run this, and follow what it
asks. It needs the server running:

```text
/kraft:onboard
```

To clear gates and review diffs from VS Code, install the
[Kraft extension](https://itsomidkarami.github.io/kraft/guides/vscode) from the
VS Code Marketplace (publisher `kraft-sdlc`), or download `kraft-<version>.vsix`
from a [GitHub release](https://github.com/itsOmidKarami/kraft/releases) and run
`code --install-extension kraft-<version>.vsix`. Open VSX (VSCodium, Cursor)
is coming; until then, use the `.vsix`.

Every install path, connecting your agent, and updating with
`kraft admin update` are in the
[install guide](https://itsomidkarami.github.io/kraft/get-started/install).

## First run

Open http://127.0.0.1:8765, then follow the
[first-work-item tutorial](https://itsomidkarami.github.io/kraft/get-started/first-work-item)
to file your first work item.

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

## Project status

Kraft is young. Expect rough edges and frequent releases. Report bugs and ask
questions in [GitHub issues](https://github.com/itsOmidKarami/kraft/issues).
Report security problems as [SECURITY.md](SECURITY.md) describes.

## Contributing

Set-up, the `just` recipes, tests, and the pull request rules are in
[CONTRIBUTING.md](CONTRIBUTING.md). Releases are described in
[RELEASING.md](RELEASING.md). Everyone taking part follows the
[Code of Conduct](CODE_OF_CONDUCT.md).
